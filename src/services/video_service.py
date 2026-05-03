"""
Video assembly service — FFmpeg-based (memory-efficient, no MoviePy needed).

Pipeline:
  1. Probe clip durations with ffprobe.
  2. Build an ffmpeg concat list, looping clips as needed.
  3. FFmpeg pass 1: concatenate/loop B-roll, scale to 1920x1080, trim to audio length.
  4. FFmpeg pass 2 (optional): mix background music under narration at -18 dB.
  5. FFmpeg pass 3: combine video + audio with 1 s fade-in/out, export MP4.

FFmpeg streams everything — no clips are loaded into RAM — so this works even
on 7 GB GitHub Actions runners handling 36+ HD clips.
"""
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import (
    AUDIO_CODEC,
    CHANNEL_BRANDING,
    PATHS,
    VIDEO_BITRATE,
    VIDEO_CODEC,
    VIDEO_FPS,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)


# ─────────────────────────── ffprobe helpers ────────────────────────────────

def _probe_duration(path: str) -> float:
    """Return media duration in seconds using ffprobe. Returns 0.0 on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_entries", "format=duration",
                path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except Exception as exc:
        logger.warning("ffprobe failed for '%s': %s", path, exc)
        return 0.0


# ─────────────────────────── Concat list builder ────────────────────────────

def _build_concat_list(clip_paths: List[str], target_duration: float, temp_dir: str) -> str:
    """
    Write an ffmpeg concat-demuxer list file, looping clips until they cover
    *target_duration* seconds. Returns the path to the list file.
    """
    durations = [_probe_duration(p) for p in clip_paths]
    valid = [(p, d) for p, d in zip(clip_paths, durations) if d > 0]
    if not valid:
        raise RuntimeError("No valid B-roll clips found (all have zero duration).")

    list_path = Path(temp_dir) / "_broll_concat.txt"
    total = 0.0
    lines: List[str] = []
    idx = 0

    while total < target_duration + 5 and len(lines) < 300:
        path, dur = valid[idx % len(valid)]
        safe_path = path.replace("\\", "/")
        lines.append(f"file '{safe_path}'")
        total += dur
        idx += 1

    with open(list_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    logger.info("Concat list: %d entries covering %.0fs (need %.0fs)", len(lines), total, target_duration)
    return str(list_path)


# ─────────────────────────── ffmpeg runner ──────────────────────────────────

def _run_ffmpeg(args: List[str], step: str) -> None:
    """Run ffmpeg -y <args>, raising RuntimeError on non-zero exit."""
    cmd = ["ffmpeg", "-y"] + args
    logger.info("FFmpeg [%s] running…", step)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # Include last 3000 chars of stderr for diagnosis
        raise RuntimeError(
            f"FFmpeg failed at step '{step}' (exit {result.returncode}):\n"
            + result.stderr[-3000:]
        )


# ─────────────────────────── Public API ─────────────────────────────────────

def assemble_video(
    audio_path: str,
    clip_paths: List[str],
    script_data: Dict[str, Any],
    output_path: str,
    subtitle_path: Optional[str] = None,
) -> str:
    """
    Assemble the final 1920x1080 MP4 video using ffmpeg.

    Args:
        audio_path:   Path to the narration MP3 (from tts_service).
        clip_paths:   List of B-roll MP4 file paths (from media_service).
        script_data:  Output of script_service (not used in ffmpeg path).
        output_path:  Destination path for the finished MP4.

    Returns:
        Absolute path of the exported MP4.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.parent  # reuse the same temp dir

    # ── 1. Measure audio duration ─────────────────────────────────────────
    audio_duration = _probe_duration(audio_path)
    if audio_duration == 0.0:
        raise RuntimeError(f"Could not probe audio duration: {audio_path}")
    logger.info(
        "Audio duration: %.1f seconds (%.1f minutes)", audio_duration, audio_duration / 60
    )

    # ── 2. Build B-roll concat list ──────────────────────────────────────
    logger.info("Preparing %d B-roll clip(s)…", len(clip_paths))
    concat_list = _build_concat_list(clip_paths, audio_duration, str(tmp))

    # ── 3. Concatenate + scale B-roll → silent intermediate video ────────
    silent_video = str(tmp / "_broll_silent.mp4")
    scale_filter = (
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},"
        f"fps={VIDEO_FPS}"
    )
    _run_ffmpeg(
        [
            "-f", "concat", "-safe", "0", "-i", concat_list,
            "-t", str(audio_duration + 1),
            "-vf", scale_filter,
            "-c:v", VIDEO_CODEC,
            "-b:v", "3500k",
            "-preset", "ultrafast",
            "-threads", "2",
            "-max_muxing_queue_size", "9999",
            "-an",
            silent_video,
        ],
        step="concat_broll",
    )

    # ── 4. Prepare audio (optionally mix bgmusic) ─────────────────────────
    bgmusic_path = str(PATHS["bgmusic"])
    if os.path.exists(bgmusic_path):
        logger.info("Mixing background music: %s", bgmusic_path)
        mixed_audio = str(tmp / "_mixed_audio.aac")
        try:
            _run_ffmpeg(
                [
                    "-i", audio_path,
                    "-stream_loop", "-1", "-i", bgmusic_path,
                    "-filter_complex",
                    f"[1:a]volume=0.12,atrim=0:{audio_duration}[bgm];"
                    f"[0:a][bgm]amix=inputs=2:duration=first[aout]",
                    "-map", "[aout]",
                    "-c:a", AUDIO_CODEC,
                    "-t", str(audio_duration),
                    mixed_audio,
                ],
                step="mix_audio",
            )
            final_audio = mixed_audio
        except Exception as exc:
            logger.warning("Background music mix failed (%s). Using narration only.", exc)
            final_audio = audio_path
    else:
        final_audio = audio_path

    # ── 5. Combine video + audio with subtitles, outro, and fade in/out ──────
    fade_out_start = max(0.0, audio_duration - 1.0)
    outro_start = max(0.0, audio_duration - 8.0)
    logger.info("Exporting video → %s", output_path)

    # Build subtitle filter (optional — requires libass in ffmpeg)
    sub_filter = ""
    if subtitle_path and os.path.exists(subtitle_path):
        safe_sub = subtitle_path.replace("\\", "/")
        if os.name == "nt":
            # Escape Windows drive-letter colon for ffmpeg filter parser
            safe_sub = safe_sub.replace(":", "\\\\:")
        sub_filter = (
            f",subtitles={safe_sub}"
            ":force_style='Fontsize=22,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H00000000,Outline=2,Shadow=1,"
            "Alignment=2,MarginV=45,Bold=1'"
        )
        logger.info("Subtitles enabled: %s", subtitle_path)

    # Outro text overlay — appears in final 8 seconds
    outro_filter = (
        f",drawtext=text='Subscribe to {CHANNEL_BRANDING}'"
        f":enable='gte(t,{outro_start:.1f})'"
        ":fontsize=52:fontcolor=white:bordercolor=black:borderw=3"
        ":box=1:boxcolor=black@0.65:boxborderw=18"
        ":x=(w-text_w)/2:y=h*0.44"
        f",drawtext=text='Tap the bell for daily finance videos'"
        f":enable='gte(t,{outro_start:.1f})'"
        ":fontsize=30:fontcolor=yellow:bordercolor=black:borderw=2"
        ":box=1:boxcolor=black@0.65:boxborderw=10"
        ":x=(w-text_w)/2:y=h*0.56"
    )

    vf = (
        f"[0:v]fade=t=in:st=0:d=1,fade=t=out:st={fade_out_start:.3f}:d=1"
        f"{sub_filter}{outro_filter}[v]"
    )
    af = (
        f"[1:a]afade=t=in:st=0:d=1,afade=t=out:st={fade_out_start:.3f}:d=1[a]"
    )

    _common_encode = [
        "-c:v", VIDEO_CODEC, "-b:v", "3500k", "-preset", "ultrafast",
        "-threads", "2", "-max_muxing_queue_size", "9999",
        "-c:a", AUDIO_CODEC, "-t", str(audio_duration),
    ]

    try:
        _run_ffmpeg(
            [
                "-i", silent_video, "-i", final_audio,
                "-filter_complex", f"{vf};{af}",
                "-map", "[v]", "-map", "[a]",
            ] + _common_encode + [output_path],
            step="final_export",
        )
    except RuntimeError as exc:
        logger.warning(
            "Final export with subtitle/outro filters failed (%s). "
            "Retrying without overlay filters.", exc
        )
        plain_vf = f"[0:v]fade=t=in:st=0:d=1,fade=t=out:st={fade_out_start:.3f}:d=1[v]"
        plain_af = f"[1:a]afade=t=in:st=0:d=1,afade=t=out:st={fade_out_start:.3f}:d=1[a]"
        _run_ffmpeg(
            [
                "-i", silent_video, "-i", final_audio,
                "-filter_complex", f"{plain_vf};{plain_af}",
                "-map", "[v]", "-map", "[a]",
            ] + _common_encode + [output_path],
            step="final_export_fallback",
        )

    # ── 6. Cleanup intermediates ──────────────────────────────────────────
    for tmp_file in [silent_video, concat_list]:
        try:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
        except OSError:
            pass
    bgmusic_path = str(PATHS["bgmusic"])
    if os.path.exists(bgmusic_path) and "mixed_audio" in locals():
        try:
            os.remove(mixed_audio)
        except OSError:
            pass

    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(
            f"Video export failed — output file missing or empty: {output_path}"
        )

    size_mb = out.stat().st_size / (1024 * 1024)
    logger.info("Video export complete. Size: %.1f MB → %s", size_mb, output_path)
    return str(out)
