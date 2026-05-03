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
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


# ─────────────────────────── Ken Burns per-clip zoom ───────────────────────

def _apply_kenburns(src: str, dst: str, w: int, h: int, fps: int) -> None:
    """
    Pre-process a single B-roll clip: scale to 115% overscan, then apply a
    slow diagonal pan that runs from the top-left corner of the overscan to
    the center over 60 s.  Because `t` in the filter expression is the clip's
    own PTS (starting at 0), each clip gets its own independent motion —
    no more "stuck" / frozen-looking frames.

    For a typical 10-20 s clip the pan covers ~15-30 % of the overscan range,
    which is clearly visible but not distracting.
    """
    zoom = 1.15                    # 15 % overscan on each axis
    W_Z = int(w * zoom)            # e.g. 2208 for 1920
    H_Z = int(h * zoom)            # e.g. 1242 for 1080
    max_x = W_Z - w               # pixels of horizontal overscan
    max_y = H_Z - h               # pixels of vertical overscan
    # Pan from (0, 0) → (max_x, max_y/2) over 60 s
    vf = (
        f"scale={W_Z}:{H_Z}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h}"
        f":x='min({max_x}*t/60,{max_x})'"
        f":y='min({max_y//2}*t/60,{max_y//2})',"
        f"fps={fps}"
    )
    _run_ffmpeg(
        [
            "-i", src,
            "-vf", vf,
            "-c:v", VIDEO_CODEC,
            "-preset", "ultrafast",
            "-b:v", "2500k",
            "-threads", "2",
            "-max_muxing_queue_size", "9999",
            "-an",
            dst,
        ],
        step=f"kb_{Path(dst).stem}",
    )



# ─────────────────────────── SRT overlay builder ───────────────────────────

# Section display labels (shown as a lower-third when each section begins)
_SECTION_LABELS: Dict[str, str] = {
    "hook":        "📌 Did You Know?",
    "problem":     "⚠️  The Problem",
    "explanation": "💡 How It Works",
    "example":     "📊 Real Example",
    "cta":         "✅  Take Action",
}

# Pattern that matches standalone financial values:
# ₹1,20,000 / 90% / 3.5x / ₹500 / 12 lakh / $1,000 etc.
_VALUE_RE = re.compile(
    r"(?:"
    r"(?:Rs\.?|₹|\$)\s?\d[\d,\.]*(?:\s?(?:lakh|crore|thousand|million|billion|k|L|Cr))?|"
    r"\d[\d,\.]*\s?(?:lakh|crore|thousand|million|billion|k|L|Cr)|"
    r"\d+(?:\.\d+)?\s?(?:%|percent|x|times)"
    r")",
    re.IGNORECASE,
)


def _srt_ts_to_seconds(ts: str) -> float:
    """Convert SRT timestamp '00:01:23,456' to float seconds."""
    ts = ts.replace(",", ".")
    parts = ts.split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def _parse_srt(srt_path: str) -> List[Tuple[float, float, str]]:
    """
    Parse an SRT file into a list of (start_s, end_s, text) tuples.
    """
    entries: List[Tuple[float, float, str]] = []
    try:
        with open(srt_path, encoding="utf-8") as fh:
            content = fh.read()
        # Each block is separated by blank lines
        for block in re.split(r"\n{2,}", content.strip()):
            lines = block.strip().splitlines()
            if len(lines) < 3:
                continue
            # line 0: index, line 1: timestamps, line 2+: text
            ts_line = lines[1]
            if " --> " not in ts_line:
                continue
            start_s, end_s = (
                _srt_ts_to_seconds(t.strip()) for t in ts_line.split(" --> ")
            )
            text = " ".join(lines[2:])
            entries.append((start_s, end_s, text))
    except Exception as exc:
        logger.warning("SRT parse failed: %s", exc)
    return entries


def _build_overlay_filters(
    srt_entries: List[Tuple[float, float, str]],
    script_data: Dict[str, Any],
    audio_duration: float,
) -> List[str]:
    """
    Return a list of ffmpeg drawtext filter strings that:
      1. Show a section label (lower-third) when each script section starts.
      2. Highlight key financial values in a pop-up card when the narrator
         speaks them (detected by matching SRT text against _VALUE_RE).

    All filters use `enable='between(t,START,END)'` so they are precisely
    timed to the narration.
    """
    filters: List[str] = []
    if not srt_entries:
        return filters

    sections = script_data.get("sections", {})
    total_words = sum(len(s.split()) for s in sections.values())
    words_per_second = total_words / max(audio_duration, 1.0)

    # ── 1. Section label overlays ──────────────────────────────────────────
    # Estimate when each section starts based on cumulative word count.
    section_order = ["hook", "problem", "explanation", "example", "cta"]
    elapsed_words = 0
    for section_key in section_order:
        label = _SECTION_LABELS.get(section_key, "")
        text = sections.get(section_key, "")
        if not text or not label:
            elapsed_words += len(text.split())
            continue
        start_s = elapsed_words / max(words_per_second, 0.01)
        show_dur = 3.5  # seconds the label stays visible
        end_s = min(start_s + show_dur, audio_duration - 1)
        # Escape special chars for ffmpeg drawtext
        safe_label = label.replace("'", "\\'").replace(":", "\\:")
        filters.append(
            f"drawtext=text='{safe_label}'"
            f":enable='between(t,{start_s:.2f},{end_s:.2f})'"
            ":fontsize=38:fontcolor=white:bordercolor=black:borderw=3"
            ":box=1:boxcolor=0x1a1a2e@0.82:boxborderw=16"
            ":x=60:y=h-160"
        )
        elapsed_words += len(text.split())

    # ── 2. Value pop-up overlays ───────────────────────────────────────────
    # Walk SRT entries; when a value token is found, display it in a gold card.
    shown_values: set = set()  # avoid duplicating the same value
    for start_s, end_s, text in srt_entries:
        matches = _VALUE_RE.findall(text)
        for raw in matches:
            value = " ".join(raw.split())   # normalise whitespace
            if value.lower() in shown_values:
                continue
            shown_values.add(value.lower())
            show_dur = min(end_s - start_s + 1.5, 4.0)
            disp_end = min(start_s + show_dur, audio_duration - 1)
            safe_val = value.replace("'", "\\'").replace(":", "\\:")
            # Gold card, centred horizontally, in the upper-right quadrant
            filters.append(
                f"drawtext=text='{safe_val}'"
                f":enable='between(t,{start_s:.2f},{disp_end:.2f})'"
                ":fontsize=68:fontcolor=0xFFB900:bordercolor=black:borderw=4"
                ":box=1:boxcolor=black@0.72:boxborderw=20"
                ":x=w-text_w-60:y=h*0.22"
            )
            if len(filters) > 80:   # cap: ffmpeg filter_complex has limits
                break
        if len(filters) > 80:
            break

    logger.info("Overlay filters built: %d total.", len(filters))
    return filters


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

    # ── 2. Pre-process each unique clip with Ken Burns zoom ─────────────────
    logger.info("Applying Ken Burns zoom to %d clip(s)…", len(clip_paths))
    zoomed_paths: List[str] = []
    for src in clip_paths:
        stem = Path(src).stem
        dst = str(tmp / f"_kb_{stem}.mp4")
        if not Path(dst).exists():
            try:
                _apply_kenburns(src, dst, VIDEO_WIDTH, VIDEO_HEIGHT, VIDEO_FPS)
            except Exception as exc:
                logger.warning("Ken Burns failed for %s (%s) — using raw clip.", src, exc)
                dst = src
        zoomed_paths.append(dst)

    # ── 3. Build B-roll concat list (from zoomed clips) ─────────────────────
    logger.info("Preparing concat list from zoomed clips…")
    concat_list = _build_concat_list(zoomed_paths, audio_duration, str(tmp))

    # ── 4. Concatenate zoomed B-roll → silent intermediate video ────────────
    # Clips are already scaled/fps-matched; just concat + trim.
    silent_video = str(tmp / "_broll_silent.mp4")
    _run_ffmpeg(
        [
            "-f", "concat", "-safe", "0", "-i", concat_list,
            "-t", str(audio_duration + 1),
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

    # ── Build SRT-synced value + section overlays ─────────────────────────
    srt_entries: List[Tuple[float, float, str]] = []
    if subtitle_path and os.path.exists(subtitle_path):
        srt_entries = _parse_srt(subtitle_path)
        logger.info("Parsed %d SRT entries for overlay sync.", len(srt_entries))

    overlay_filters = _build_overlay_filters(srt_entries, script_data, audio_duration)

    # ── Outro text overlay — appears in final 8 seconds ───────────────────
    outro_drawtext = (
        f"drawtext=text='Subscribe to {CHANNEL_BRANDING}'"
        f":enable='gte(t,{outro_start:.1f})'"
        ":fontsize=52:fontcolor=white:bordercolor=black:borderw=3"
        ":box=1:boxcolor=black@0.65:boxborderw=18"
        ":x=(w-text_w)/2:y=h*0.44,"
        f"drawtext=text='Tap the bell for daily finance videos'"
        f":enable='gte(t,{outro_start:.1f})'"
        ":fontsize=30:fontcolor=yellow:bordercolor=black:borderw=2"
        ":box=1:boxcolor=black@0.65:boxborderw=10"
        ":x=(w-text_w)/2:y=h*0.56"
    )

    # Chain: fade → overlay filters → outro
    all_vf_parts = (
        [f"fade=t=in:st=0:d=1,fade=t=out:st={fade_out_start:.3f}:d=1"]
        + overlay_filters
        + [outro_drawtext]
    )
    vf = "[0:v]" + ",".join(all_vf_parts) + "[v]"
    af = f"[1:a]afade=t=in:st=0:d=1,afade=t=out:st={fade_out_start:.3f}:d=1[a]"

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
            "Final export with overlay filters failed (%s). "
            "Retrying without overlays.", exc
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
    for tmp_file in [silent_video, concat_list] + [p for p in zoomed_paths if "_kb_" in p]:
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
