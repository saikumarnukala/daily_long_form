"""
Video assembly service — MoviePy-based.

Pipeline:
  1. Load narration audio → measure exact duration.
  2. Load all downloaded B-roll clips; resize each to 1920×1080.
  3. Concatenate/loop clips to cover audio duration (+ 1-second safety buffer).
  4. Attach narration audio to the video track.
  5. If assets/bgmusic.mp3 exists, mix it under narration at -20 dB.
  6. Add section-level subtitle TextClips (timing estimated from word count).
  7. Apply 0.5 s crossfade dissolves between clips.
  8. Add 1 s fade-in and 1 s fade-out on the final composite.
  9. Export as 1920×1080 libx264/aac MP4 at 8 Mbps.
"""
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import (
    AUDIO_CODEC,
    PATHS,
    TTS_WORDS_PER_MINUTE,
    VIDEO_BITRATE,
    VIDEO_CODEC,
    VIDEO_FPS,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

# MoviePy is imported inside functions so that import errors produce clear messages
# rather than crashing the entire module on import.


def _load_moviepy():
    """Lazy MoviePy import (supports MoviePy 2.x)."""
    try:
        from moviepy import (
            AudioFileClip,
            CompositeAudioClip,
            CompositeVideoClip,
            TextClip,
            VideoFileClip,
            concatenate_videoclips,
        )
        from moviepy.audio.fx import AudioLoop, MultiplyVolume
        from moviepy.video.fx import CrossFadeIn, FadeIn, FadeOut, Margin
        return (
            AudioFileClip, CompositeAudioClip, CompositeVideoClip,
            TextClip, VideoFileClip, concatenate_videoclips,
            AudioLoop, MultiplyVolume, CrossFadeIn, FadeIn, FadeOut, Margin,
        )
    except ImportError as exc:
        raise ImportError(
            "MoviePy is not installed. Run: pip install moviepy"
        ) from exc


# ─────────────────────────── Subtitle helpers ───────────────────────────

def _build_subtitle_clips(sections: Dict[str, str], total_duration: float, TextClip, FadeIn, FadeOut, Margin):
    """
    Build a list of TextClip objects timed proportionally to each section's word count.

    Each section gets a short label displayed at the top-left for the duration
    of that section.
    """
    section_order = ["hook", "problem", "explanation", "example", "cta"]
    section_labels = {
        "hook": "Introduction",
        "problem": "The Problem",
        "explanation": "Deep Dive",
        "example": "Real Example",
        "cta": "Summary",
    }

    word_counts = {}
    total_words = 0
    for key in section_order:
        wc = len(sections.get(key, "").split())
        word_counts[key] = wc
        total_words += wc

    if total_words == 0:
        return []

    subtitle_clips = []
    current_start = 0.0

    for key in section_order:
        wc = word_counts[key]
        if wc == 0:
            continue
        duration = (wc / total_words) * total_duration
        label = section_labels.get(key, key.title())

        try:
            # Try common Windows fonts; fall back gracefully if none found
            font_candidates = [
                r"C:\Windows\Fonts\arialbd.ttf",
                r"C:\Windows\Fonts\arial.ttf",
                r"C:\Windows\Fonts\verdana.ttf",
            ]
            font_path = next((f for f in font_candidates if os.path.exists(f)), None)
            if font_path is None:
                logger.warning("No system font found for subtitle '%s'; skipping.", key)
                current_start += duration
                continue
            clip = (
                TextClip(
                    font=font_path,
                    text=label,
                    font_size=32,
                    color="white",
                    stroke_color="black",
                    stroke_width=2,
                    method="label",
                    duration=duration,
                )
                .with_position(("left", "top"))
                .with_start(current_start)
                .with_effects([Margin(left=20, top=20, opacity=1.0), FadeIn(0.3), FadeOut(0.3)])
            )
            subtitle_clips.append(clip)
        except Exception as exc:
            logger.warning("Could not create subtitle clip for section '%s': %s", key, exc)

        current_start += duration

    return subtitle_clips


# ─────────────────────────── Clip preparation ───────────────────────────

def _prepare_clips(clip_paths: List[str], target_duration: float, VideoFileClip, concatenate_videoclips, CrossFadeIn):
    """
    Load, resize, and concatenate B-roll clips to cover *target_duration* seconds.
    Loops the clip list if necessary.
    """
    loaded = []
    accumulated = 0.0

    # Expand clip list by looping if needed
    extended_paths = []
    while accumulated < target_duration + 5:
        extended_paths.extend(clip_paths)
        for p in clip_paths:
            try:
                temp_clip = VideoFileClip(p)
                accumulated += temp_clip.duration
                temp_clip.close()
            except Exception:
                pass
        if len(extended_paths) > len(clip_paths) * 10:
            break  # safety: never loop more than 10x

    accumulated = 0.0
    for path in extended_paths:
        if accumulated >= target_duration + 5:
            break
        try:
            clip = VideoFileClip(path)
            # Resize to target resolution maintaining aspect ratio via crop
            clip = clip.resized(height=VIDEO_HEIGHT)
            if clip.w < VIDEO_WIDTH:
                clip = clip.resized(width=VIDEO_WIDTH)
            # Centre-crop to exact 1920×1080
            x_centre = clip.w / 2
            y_centre = clip.h / 2
            clip = clip.cropped(
                x_center=x_centre,
                y_center=y_centre,
                width=VIDEO_WIDTH,
                height=VIDEO_HEIGHT,
            )
            clip = clip.without_audio()
            loaded.append(clip)
            accumulated += clip.duration
        except Exception as exc:
            logger.warning("Failed to load clip '%s': %s", path, exc)

    if not loaded:
        raise RuntimeError("No video clips could be loaded for assembly.")

    # Add crossfade transitions between clips
    for i in range(len(loaded)):
        if i == 0:
            continue
        try:
            loaded[i] = loaded[i].with_effects([CrossFadeIn(0.5)])
        except Exception:
            pass  # not fatal if crossfade fails for a particular clip

    combined = concatenate_videoclips(loaded, method="chain")

    # Trim to exactly target_duration + 1 s buffer
    if combined.duration > target_duration + 2:
        combined = combined.subclipped(0, target_duration + 1)

    return combined


# ─────────────────────────── Public API ───────────────────────────

def assemble_video(
    audio_path: str,
    clip_paths: List[str],
    script_data: Dict[str, Any],
    output_path: str,
) -> str:
    """
    Assemble the final 1920×1080 MP4 video.

    Args:
        audio_path:   Path to the narration MP3 file (from tts_service).
        clip_paths:   List of B-roll MP4 file paths (from media_service).
        script_data:  Output of script_service.generate_script() containing 'sections'.
        output_path:  Destination path for the finished MP4.

    Returns:
        Absolute path of the exported MP4.
    """
    (
        AudioFileClip, CompositeAudioClip, CompositeVideoClip,
        TextClip, VideoFileClip, concatenate_videoclips,
        AudioLoop, MultiplyVolume, CrossFadeIn, FadeIn, FadeOut, Margin,
    ) = _load_moviepy()

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    logger.info("Loading narration audio: %s", audio_path)
    narration = AudioFileClip(audio_path)
    total_duration = narration.duration
    logger.info("Audio duration: %.1f seconds (%.1f minutes)", total_duration, total_duration / 60)

    # ── 1. Prepare B-roll video ──────────────────────────────────────────
    logger.info("Preparing %d B-roll clip(s)…", len(clip_paths))
    video_track = _prepare_clips(clip_paths, total_duration, VideoFileClip, concatenate_videoclips, CrossFadeIn)

    # ── 2. Build audio track (narration ± background music) ─────────────
    bgmusic_path = str(PATHS["bgmusic"])
    if os.path.exists(bgmusic_path):
        logger.info("Mixing background music: %s", bgmusic_path)
        try:
            bgm = AudioFileClip(bgmusic_path).with_effects([AudioLoop(duration=total_duration)])
            bgm = bgm.with_volume_scaled(0.12)   # ≈ -18 dB
            audio_track = CompositeAudioClip([narration, bgm])
        except Exception as exc:
            logger.warning("Background music mixing failed (%s). Using narration only.", exc)
            audio_track = narration
    else:
        audio_track = narration

    # ── 3. Subtitle text overlays ────────────────────────────────────────
    logger.info("Building subtitle overlays…")
    subtitle_clips = _build_subtitle_clips(
        script_data.get("sections", {}),
        total_duration,
        TextClip, FadeIn, FadeOut, Margin,
    )

    # ── 4. Compose final video ───────────────────────────────────────────
    all_layers = [video_track] + subtitle_clips
    final = CompositeVideoClip(all_layers, size=(VIDEO_WIDTH, VIDEO_HEIGHT))
    final = final.with_audio(audio_track)
    final = final.with_duration(total_duration)

    # Fade in/out
    final = final.with_effects([FadeIn(1.0), FadeOut(1.0)])

    # ── 5. Export ────────────────────────────────────────────────────────
    logger.info("Exporting video → %s", output_path)
    final.write_videofile(
        output_path,
        fps=VIDEO_FPS,
        codec=VIDEO_CODEC,
        audio_codec=AUDIO_CODEC,
        bitrate=VIDEO_BITRATE,
        threads=4,
        preset="fast",
        logger=None,  # suppress MoviePy's verbose progress bar (we use our own logger)
    )

    # Clean up MoviePy objects
    try:
        final.close()
        narration.close()
        video_track.close()
    except Exception:
        pass

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(f"Video export failed — output file is missing or empty: {output_path}")

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    logger.info("Video export complete. Size: %.1f MB → %s", size_mb, output_path)
    return output_path
