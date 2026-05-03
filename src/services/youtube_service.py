"""
YouTube Data API v3 upload service.

Authentication: OAuth2 via refresh token — no browser prompt needed in CI.
The YOUTUBE_CLIENT_SECRET environment variable must hold the full client
secret JSON string (as downloaded from Google Cloud Console).
The YOUTUBE_REFRESH_TOKEN must be a valid, non-expired refresh token
obtained by running scripts/get_youtube_token.py once locally.

Capabilities:
  - upload_video(): Resumable video upload with metadata.
  - set_thumbnail(): Upload custom thumbnail after video is live.
"""
import os
from typing import Any, Dict, List

import google.auth.transport.requests
import google.oauth2.credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from src.config import (
    YOUTUBE_CATEGORY_ID,
    YOUTUBE_CLIENT_ID,
    YOUTUBE_CLIENT_SECRET,
    YOUTUBE_DEFAULT_LANGUAGE,
    YOUTUBE_PRIVACY_STATUS,
    YOUTUBE_REFRESH_TOKEN,
)
from src.utils.logger import get_logger
from src.utils.retry import retry

logger = get_logger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]
_API_SERVICE_NAME = "youtube"
_API_VERSION = "v3"


# ─────────────────────────── Auth ───────────────────────────

def _build_credentials() -> google.oauth2.credentials.Credentials:
    """
    Build OAuth2 credentials from environment variables (no browser required).
    Expects YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET (raw string), YOUTUBE_REFRESH_TOKEN.
    """
    if not YOUTUBE_CLIENT_ID:
        raise EnvironmentError(
            "YOUTUBE_CLIENT_ID environment variable is not set. "
            "See .env.example for setup instructions."
        )
    if not YOUTUBE_CLIENT_SECRET:
        raise EnvironmentError(
            "YOUTUBE_CLIENT_SECRET environment variable is not set. "
            "See .env.example for setup instructions."
        )
    if not YOUTUBE_REFRESH_TOKEN:
        raise EnvironmentError(
            "YOUTUBE_REFRESH_TOKEN environment variable is not set. "
            "Run scripts/get_youtube_token.py to obtain it."
        )

    credentials = google.oauth2.credentials.Credentials(
        token=None,
        refresh_token=YOUTUBE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=YOUTUBE_CLIENT_ID,
        client_secret=YOUTUBE_CLIENT_SECRET,
        scopes=_SCOPES,
    )

    # Force token refresh to validate credentials before upload
    request = google.auth.transport.requests.Request()
    credentials.refresh(request)
    return credentials


def _get_youtube_client():
    """Return an authorised YouTube API client."""
    credentials = _build_credentials()
    return build(_API_SERVICE_NAME, _API_VERSION, credentials=credentials)


# ─────────────────────────── Upload helpers ───────────────────────────

@retry(max_attempts=3, backoff=3.0, exceptions=(HttpError, OSError))
def upload_video(
    video_path: str,
    thumbnail_path: str,
    metadata: Dict[str, Any],
) -> str:
    """
    Upload a video to YouTube with metadata and thumbnail.

    Args:
        video_path:      Local path to the MP4 file.
        thumbnail_path:  Local path to the JPEG thumbnail.
        metadata:        Dict containing: title, description, tags, (optional) category_id.

    Returns:
        YouTube video ID string (e.g. "dQw4w9WgXcQ").
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")
    if not os.path.exists(thumbnail_path):
        raise FileNotFoundError(f"Thumbnail file not found: {thumbnail_path}")

    youtube = _get_youtube_client()

    title: str = metadata.get("title", "Finance Video")[:100]  # YouTube 100-char limit
    description: str = metadata.get("description", "")[:5000]  # YouTube 5000-char limit
    tags: List[str] = metadata.get("tags", [])[:500]  # practical cap
    category_id: str = metadata.get("category_id", YOUTUBE_CATEGORY_ID)

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
            "defaultLanguage": YOUTUBE_DEFAULT_LANGUAGE,
        },
        "status": {
            "privacyStatus": YOUTUBE_PRIVACY_STATUS,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=10 * 1024 * 1024,  # 10 MB chunks
    )

    logger.info("Starting YouTube upload: '%s'", title)
    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    video_id: str = ""
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            logger.info("Upload progress: %d%%", pct)

    video_id = response.get("id", "")
    logger.info("Upload complete! Video ID: %s", video_id)

    # Upload thumbnail
    if video_id:
        _set_thumbnail(youtube, video_id, thumbnail_path)

    return video_id


def _set_thumbnail(youtube, video_id: str, thumbnail_path: str) -> None:
    """Upload and set the custom thumbnail for an already-uploaded video."""
    try:
        media = MediaFileUpload(thumbnail_path, mimetype="image/jpeg")
        youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
        logger.info("Thumbnail set for video %s", video_id)
    except HttpError as exc:
        logger.warning(
            "Thumbnail upload failed for video %s: %s. "
            "You can set it manually in YouTube Studio.",
            video_id,
            exc,
        )
