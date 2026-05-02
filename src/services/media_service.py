"""
Media (video) fetching service — Pexels Videos API only.

Responsibilities:
  - Search Pexels for video clips matching a keyword.
  - Filter out already-used clip IDs (deduplication).
  - Download enough clips to cover the required audio duration + 30% buffer.
  - Return a list of local MP4 file paths.
"""
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from src.config import (
    PEXELS_API_KEY,
    PEXELS_CLIPS_BUFFER_FACTOR,
    PEXELS_MAX_RESULTS_PER_QUERY,
    PEXELS_VIDEO_API,
)
from src.utils.logger import get_logger
from src.utils.retry import retry

logger = get_logger(__name__)

_HEADERS = {"Authorization": PEXELS_API_KEY}


# ─────────────────────────── Pexels helpers ───────────────────────────

@retry(max_attempts=3, backoff=2.0, exceptions=(requests.RequestException, ValueError))
def _search_pexels_videos(keyword: str, page: int = 1) -> List[Dict[str, Any]]:
    """
    Query Pexels Videos API for *keyword* and return a list of video objects.
    """
    params = {
        "query": keyword,
        "per_page": PEXELS_MAX_RESULTS_PER_QUERY,
        "page": page,
        "orientation": "landscape",
    }
    resp = requests.get(PEXELS_VIDEO_API, headers=_HEADERS, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    videos = data.get("videos", [])
    logger.debug("Pexels search '%s' page %d → %d results", keyword, page, len(videos))
    return videos


def _get_best_video_file(video: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Pick the highest-quality HD video file from a Pexels video object.
    Prefers ≥1280 px wide files; falls back to the widest available.
    """
    files = video.get("video_files", [])
    if not files:
        return None
    hd = [f for f in files if f.get("quality") in ("hd", "uhd") and f.get("width", 0) >= 1280]
    if hd:
        return max(hd, key=lambda f: f.get("width", 0))
    return max(files, key=lambda f: f.get("width", 0))


@retry(max_attempts=3, backoff=2.0, exceptions=(requests.RequestException, OSError))
def _download_video(url: str, dest_path: str) -> str:
    """
    Stream-download a video from *url* to *dest_path*.
    Returns the destination path on success.
    """
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(dest_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if chunk:
                    fh.write(chunk)
                    downloaded += len(chunk)
    logger.debug("Downloaded %d bytes → %s", downloaded, dest_path)
    return dest_path


# ─────────────────────────── Public API ───────────────────────────

def get_clips(
    keywords: List[str],
    total_duration: float,
    used_ids: List[int],
    temp_dir: str = "assets/temp",
) -> List[str]:
    """
    Fetch and download video clips from Pexels to cover *total_duration* seconds.

    Strategy:
      1. For each keyword (in order), search Pexels.
      2. Skip videos whose IDs are in *used_ids*.
      3. Download clips until accumulated duration ≥ total_duration * buffer_factor.
      4. Move to the next keyword if the current one is exhausted.

    Args:
        keywords:       List of search terms (e.g. ["stock market", "trading"]).
        total_duration: Required seconds of footage (= audio length).
        used_ids:       Pexels video IDs already used in previous videos.
        temp_dir:       Directory to save downloaded MP4 files.

    Returns:
        List of absolute paths to downloaded MP4 files.
    """
    target_seconds = total_duration * PEXELS_CLIPS_BUFFER_FACTOR
    accumulated = 0.0
    downloaded_paths: List[str] = []
    new_used_ids: List[int] = list(used_ids)

    logger.info(
        "Fetching clips for keywords %s | need %.0fs (buffer %.0fs)",
        keywords,
        total_duration,
        target_seconds,
    )

    for keyword in keywords:
        if accumulated >= target_seconds:
            break
        for page in range(1, 4):  # up to 3 pages per keyword
            if accumulated >= target_seconds:
                break
            try:
                videos = _search_pexels_videos(keyword, page=page)
            except Exception as exc:
                logger.error("Pexels search failed for '%s' page %d: %s", keyword, page, exc)
                continue

            for video in videos:
                if accumulated >= target_seconds:
                    break
                vid_id = video.get("id")
                if vid_id in new_used_ids:
                    continue

                clip_duration = float(video.get("duration", 0))
                if clip_duration < 3:
                    continue  # skip very short clips

                best_file = _get_best_video_file(video)
                if not best_file:
                    continue

                dest = os.path.join(temp_dir, f"clip_{vid_id}.mp4")
                if os.path.exists(dest):
                    logger.debug("Clip %d already cached, reusing.", vid_id)
                    downloaded_paths.append(dest)
                    new_used_ids.append(vid_id)
                    accumulated += clip_duration
                    continue

                try:
                    _download_video(best_file["link"], dest)
                    downloaded_paths.append(dest)
                    new_used_ids.append(vid_id)
                    accumulated += clip_duration
                    logger.info(
                        "Clip %d downloaded (%.0fs) | total so far: %.0fs",
                        vid_id,
                        clip_duration,
                        accumulated,
                    )
                except Exception as exc:
                    logger.warning("Failed to download clip %d: %s", vid_id, exc)

    if not downloaded_paths:
        raise RuntimeError(
            f"No clips could be downloaded for keywords {keywords}. "
            "Check your PEXELS_API_KEY and network connection."
        )

    logger.info(
        "Media fetch complete. %d clips downloaded (≈%.0fs total footage).",
        len(downloaded_paths),
        accumulated,
    )
    return downloaded_paths
