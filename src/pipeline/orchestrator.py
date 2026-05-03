"""
Pipeline orchestrator — executes all 9 steps in sequence.

Each step receives and mutates the shared `context` dict.
On success, history.json is updated and written back to disk.
Any step failure raises immediately (fail-fast); the caller
(main.py) handles the top-level exception.
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from src.config import CHANNEL_BRANDING, PATHS
from src.services import (
    media_service,
    script_service,
    thumbnail_service,
    topic_service,
    tts_service,
    video_service,
    youtube_service,
)
from src.utils.file_manager import cleanup_temp, get_output_path, get_temp_path
from src.utils.logger import get_logger

logger = get_logger(__name__, log_dir=PATHS["logs"])


class PipelineOrchestrator:
    """Runs the daily YouTube video creation pipeline end-to-end."""

    def __init__(self, dry_run: bool = False):
        """
        Args:
            dry_run: If True, skip the YouTube upload step (useful for local testing).
        """
        self.dry_run = dry_run
        self.history: Dict[str, Any] = self._load_history()

    # ─────────────────────────── History I/O ───────────────────────────

    def _load_history(self) -> Dict[str, Any]:
        history_path = PATHS["history"]
        if history_path.exists():
            with open(history_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"videos": [], "used_pexels_videos": [], "used_pexels_images": []}

    def _save_history(self) -> None:
        history_path = PATHS["history"]
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=2, ensure_ascii=False)
        logger.info("History saved → %s", history_path)

    def _update_history(self, context: Dict[str, Any]) -> None:
        """Append today's video record to history and write to disk."""
        entry = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "weekday": context.get("topic_data", {}).get("weekday"),
            "category": context.get("topic_data", {}).get("category"),
            "subtopic": context.get("topic_data", {}).get("subtopic"),
            "title": context.get("script_data", {}).get("title"),
            "video_path": str(context.get("video_path", "")),
            "video_id": context.get("video_id", ""),
            "clips_used": context.get("clips_used_ids", []),
        }
        self.history["videos"].append(entry)

        # Merge used clip IDs
        used_clips = self.history.setdefault("used_pexels_videos", [])
        for vid_id in context.get("clips_used_ids", []):
            if vid_id not in used_clips:
                used_clips.append(vid_id)

        self._save_history()

    # ─────────────────────────── Pipeline Steps ───────────────────────────

    def _step_topic(self, context: Dict[str, Any]) -> None:
        logger.info("─── Step 1: Topic Selection ───")
        context["topic_data"] = topic_service.get_today_topic(self.history)

    def _step_script(self, context: Dict[str, Any]) -> None:
        logger.info("─── Step 2: Script Generation ───")
        context["script_data"] = script_service.generate_script(context["topic_data"])

    def _step_tts(self, context: Dict[str, Any]) -> None:
        logger.info("─── Step 3: TTS Generation ───")
        weekday: int = context["topic_data"]["weekday"]
        audio_path = str(get_temp_path("narration.mp3"))
        result = tts_service.generate_tts(
            text=context["script_data"]["full_text"],
            output_path=audio_path,
            voice_index=weekday % 2,  # alternate voices by day
        )
        context["audio_path"] = result["audio_path"]
        context["audio_duration"] = result["duration_seconds"]
        context["subtitle_path"] = result.get("subtitle_path")

    def _step_media(self, context: Dict[str, Any]) -> None:
        logger.info("─── Step 4: Media Fetching ───")
        keywords = context["topic_data"]["keywords"]
        duration = context["audio_duration"]
        used_ids = self.history.get("used_pexels_videos", [])
        clips = media_service.get_clips(
            keywords=keywords,
            total_duration=duration,
            used_ids=used_ids,
            temp_dir=str(PATHS["temp"]),
        )
        context["clip_paths"] = clips
        # Record newly used clip IDs (extract from filenames like clip_<id>.mp4)
        context["clips_used_ids"] = []
        for p in clips:
            stem = Path(p).stem  # "clip_12345"
            parts = stem.split("_")
            if len(parts) == 2 and parts[1].isdigit():
                context["clips_used_ids"].append(int(parts[1]))

    def _step_thumbnail(self, context: Dict[str, Any]) -> None:
        logger.info("─── Step 5: Thumbnail Creation ───")
        keyword = context["topic_data"]["keywords"][0]
        title = context["script_data"]["title"]
        thumb_path = str(get_temp_path("thumbnail.jpg"))
        thumbnail_service.create_thumbnail(
            keyword=keyword,
            title=title,
            output_path=thumb_path,
        )
        context["thumbnail_path"] = thumb_path

    def _step_video(self, context: Dict[str, Any]) -> None:
        logger.info("─── Step 6: Video Assembly ───")
        date_str = datetime.now().strftime("%Y%m%d")
        output_filename = f"video_{date_str}.mp4"
        output_path = str(get_output_path(output_filename))
        video_service.assemble_video(
            audio_path=context["audio_path"],
            clip_paths=context["clip_paths"],
            script_data=context["script_data"],
            output_path=output_path,
            subtitle_path=context.get("subtitle_path"),
        )
        context["video_path"] = output_path

    def _step_metadata(self, context: Dict[str, Any]) -> None:
        logger.info("─── Step 7: Metadata Generation ───")
        script = context["script_data"]
        # Enrich description with timestamps for section labels
        description = (
            f"{script['description']}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📌 CHAPTERS\n"
            "00:00 Introduction\n"
            "01:30 The Problem\n"
            "03:00 Deep Dive\n"
            "07:00 Real-World Example (₹)\n"
            "10:00 Your Action Plan\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔔 Subscribe to {CHANNEL_BRANDING} for daily finance education!\n"
            f"#{CHANNEL_BRANDING.replace(' ', '')} #PersonalFinance #IndiaFinance #MoneyTips"
        )
        context["upload_metadata"] = {
            "title": script["title"],
            "description": description,
            "tags": script.get("tags", []),
        }

    def _step_youtube_upload(self, context: Dict[str, Any]) -> None:
        logger.info("─── Step 8: YouTube Upload ───")
        if self.dry_run:
            logger.info("DRY RUN — skipping YouTube upload.")
            context["video_id"] = "DRY_RUN"
            return
        video_id = youtube_service.upload_video(
            video_path=context["video_path"],
            thumbnail_path=context["thumbnail_path"],
            metadata=context["upload_metadata"],
        )
        context["video_id"] = video_id
        logger.info("Video live at: https://www.youtube.com/watch?v=%s", video_id)

    def _step_history_update(self, context: Dict[str, Any]) -> None:
        logger.info("─── Step 9: History Update ───")
        self._update_history(context)

    # ─────────────────────────── Main run ───────────────────────────

    def run(self) -> Dict[str, Any]:
        """
        Execute the full pipeline.  Returns the final context dict.
        Raises on any step failure.
        """
        context: Dict[str, Any] = {}
        steps = [
            self._step_topic,
            self._step_script,
            self._step_tts,
            self._step_media,
            self._step_thumbnail,
            self._step_video,
            self._step_metadata,
            self._step_youtube_upload,
            self._step_history_update,
        ]

        logger.info("════════════════════════════════════════")
        logger.info("  Finance Decoded — Daily Pipeline START")
        logger.info("════════════════════════════════════════")

        for step_fn in steps:
            step_fn(context)

        logger.info("════════════════════════════════════════")
        logger.info("  Pipeline COMPLETE")
        if context.get("video_id") and context["video_id"] != "DRY_RUN":
            logger.info(
                "  Video: https://www.youtube.com/watch?v=%s", context["video_id"]
            )
        logger.info("════════════════════════════════════════")

        return context
