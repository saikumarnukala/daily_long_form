"""
Media (video) fetching service — Pexels Videos API only.

Responsibilities:
  - Search Pexels for video clips matching a keyword.
  - Filter out recently-used clip IDs (rolling dedup window).
  - Reuse older clips when the Pexels pool is exhausted.
  - Download enough clips to cover the required audio duration + 30% buffer.
  - Return a list of local MP4 file paths.
"""
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

from src.config import (
    PEXELS_API_KEY,
    PEXELS_CLIPS_BUFFER_FACTOR,
    PEXELS_FALLBACK_KEYWORDS,
    PEXELS_MAX_PAGES_FALLBACK,
    PEXELS_MAX_PAGES_PER_KEYWORD,
    PEXELS_MAX_RESULTS_PER_QUERY,
    PEXELS_MAX_TRACKED_USED_IDS,
    PEXELS_VIDEO_API,
)
from src.utils.logger import get_logger
from src.utils.retry import retry

logger = get_logger(__name__)

_HEADERS = {"Authorization": PEXELS_API_KEY}


# ─────────────────────────── Pexels helpers ───────────────────────────

@retry(max_attempts=3, backoff=2.0, exceptions=(requests.RequestException, ValueError))
def _search_pexels_videos(keyword: str, page: int = 1) -> List[Dict[str, Any]]:
    """Query Pexels Videos API for *keyword* and return a list of video objects."""
    params = {
        "query": keyword,
        "per_page": PEXELS_MAX_RESULTS_PER_QUERY,
        "page": page,
        "orientation": "landscape",
    }
    resp = requests.get(PEXELS_VIDEO_API, headers=_HEADERS, params=params, timeout=30)
    if resp.status_code in (401, 403):
        raise ValueError(f"Pexels API auth failed (HTTP {resp.status_code})")
    resp.raise_for_status()
    data = resp.json()
    videos = data.get("videos", [])
    logger.debug("Pexels search '%s' page %d → %d results", keyword, page, len(videos))
    return videos


def _get_best_video_file(video: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Pick a CI-friendly HD file from a Pexels video object.
    Prefers 1280x720; accepts wider SD; falls back to widest available.
    """
    files = video.get("video_files", [])
    if not files:
        return None
    hd = [
        f for f in files
        if f.get("quality") in ("hd", "uhd")
        and 1280 <= f.get("width", 0) <= 1920
        and f.get("height", 9999) <= 720
    ]
    if hd:
        return max(hd, key=lambda f: f.get("width", 0))
    sd = [f for f in files if f.get("width", 9999) <= 1920]
    if sd:
        return max(sd, key=lambda f: f.get("width", 0))
    return max(files, key=lambda f: f.get("width", 0))


@retry(max_attempts=3, backoff=2.0, exceptions=(requests.RequestException, OSError))
def _download_video(url: str, dest_path: str) -> str:
    """Stream-download a video from *url* to *dest_path*."""
    Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        downloaded = 0
        with open(dest_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if chunk:
                    fh.write(chunk)
                    downloaded += len(chunk)
    logger.debug("Downloaded %d bytes → %s", downloaded, dest_path)
    return dest_path


def _recently_used_ids(used_ids: List[int]) -> Set[int]:
    """Only skip clips used recently — allows rotation through the Pexels catalog."""
    if len(used_ids) <= PEXELS_MAX_TRACKED_USED_IDS:
        return set(used_ids)
    return set(used_ids[-PEXELS_MAX_TRACKED_USED_IDS:])


def _try_add_clip(
    video: Dict[str, Any],
    temp_dir: str,
    downloaded_paths: List[str],
    accumulated: float,
    exclude_ids: Set[int],
) -> Tuple[float, bool]:
    """Download or reuse one clip. Returns (new_accumulated, added)."""
    vid_id = video.get("id")
    if not vid_id or vid_id in exclude_ids:
        return accumulated, False

    clip_duration = float(video.get("duration", 0))
    if clip_duration < 3:
        return accumulated, False

    best_file = _get_best_video_file(video)
    if not best_file:
        return accumulated, False

    dest = os.path.join(temp_dir, f"clip_{vid_id}.mp4")
    if os.path.exists(dest):
        downloaded_paths.append(dest)
        return accumulated + clip_duration, True

    try:
        _download_video(best_file["link"], dest)
        downloaded_paths.append(dest)
        return accumulated + clip_duration, True
    except Exception as exc:
        logger.warning("Failed to download clip %d: %s", vid_id, exc)
        return accumulated, False


def _fetch_clips(
    keywords: List[str],
    target_seconds: float,
    exclude_ids: Set[int],
    temp_dir: str,
    max_pages: int,
    label: str = "",
) -> Tuple[List[str], float]:
    """Search keywords and download until *target_seconds* is reached."""
    downloaded_paths: List[str] = []
    accumulated = 0.0
    skipped_used = 0
    prefix = f"{label} " if label else ""

    for keyword in keywords:
        if accumulated >= target_seconds:
            break
        for page in range(1, max_pages + 1):
            if accumulated >= target_seconds:
                break
            try:
                videos = _search_pexels_videos(keyword, page=page)
            except Exception as exc:
                logger.error(
                    "%sPexels search failed for '%s' page %d: %s",
                    prefix, keyword, page, exc,
                )
                continue

            if not videos:
                break

            for video in videos:
                if accumulated >= target_seconds:
                    break
                vid_id = video.get("id")
                if vid_id in exclude_ids:
                    skipped_used += 1
                    continue
                accumulated, added = _try_add_clip(
                    video, temp_dir, downloaded_paths, accumulated, exclude_ids,
                )
                if added:
                    logger.info(
                        "%sClip %d added (%.0fs) | total: %.0fs / %.0fs",
                        prefix, vid_id, float(video.get("duration", 0)),
                        accumulated, target_seconds,
                    )

    if skipped_used and not downloaded_paths:
        logger.warning(
            "%sSkipped %d Pexels results already in recent-use list",
            prefix, skipped_used,
        )
    return downloaded_paths, accumulated


# ─────────────────────────── Public API ───────────────────────────

def get_clips(
    keywords: List[str],
    total_duration: float,
    used_ids: List[int],
    temp_dir: str = "assets/temp",
) -> List[str]:
    """
    Fetch and download video clips from Pexels to cover *total_duration* seconds.
    """
    target_seconds = total_duration * PEXELS_CLIPS_BUFFER_FACTOR
    exclude_ids = _recently_used_ids(used_ids)

    logger.info(
        "Fetching clips for keywords %s | need %.0fs (buffer %.0fs) | "
        "excluding %d recent clip IDs",
        keywords,
        total_duration,
        target_seconds,
        len(exclude_ids),
    )

    downloaded_paths, accumulated = _fetch_clips(
        keywords=keywords,
        target_seconds=target_seconds,
        exclude_ids=exclude_ids,
        temp_dir=temp_dir,
        max_pages=PEXELS_MAX_PAGES_PER_KEYWORD,
    )

    if accumulated < target_seconds:
        logger.warning(
            "Only %.0fs / %.0fs from topic keywords — trying fallbacks %s",
            accumulated, target_seconds, PEXELS_FALLBACK_KEYWORDS,
        )
        extra_paths, extra_secs = _fetch_clips(
            keywords=PEXELS_FALLBACK_KEYWORDS,
            target_seconds=target_seconds - accumulated,
            exclude_ids=exclude_ids,
            temp_dir=temp_dir,
            max_pages=PEXELS_MAX_PAGES_FALLBACK,
            label="fallback",
        )
        downloaded_paths.extend(extra_paths)
        accumulated += extra_secs

    if not downloaded_paths:
        logger.warning(
            "Pexels catalog exhausted (%d tracked IDs) — reusing clips from full library",
            len(exclude_ids),
        )
        downloaded_paths, accumulated = _fetch_clips(
            keywords=keywords + PEXELS_FALLBACK_KEYWORDS,
            target_seconds=target_seconds,
            exclude_ids=set(),
            temp_dir=temp_dir,
            max_pages=PEXELS_MAX_PAGES_FALLBACK,
            label="reuse",
        )

    if not downloaded_paths:
        raise RuntimeError(
            f"No clips could be downloaded for keywords {keywords}. "
            "Check your PEXELS_API_KEY and network connection."
        )

    logger.info(
        "Media fetch complete. %d clips (≈%.0fs footage, target %.0fs).",
        len(downloaded_paths),
        accumulated,
        target_seconds,
    )
    return downloaded_paths
