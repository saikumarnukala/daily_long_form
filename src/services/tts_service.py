"""
TTS (Text-to-Speech) service using Microsoft Edge TTS.

Uses the edge-tts library (free, no API key required).
Voice selection alternates between female and male Indian English voices
based on the weekday parity to add variety across videos.

Long scripts are split into ~400-word chunks to avoid WebSocket timeouts,
then concatenated with ffmpeg.
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import edge_tts

from src.config import TTS_PITCH, TTS_RATE, TTS_VOICES, TTS_VOLUME, TTS_WORDS_PER_MINUTE
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Windows requires SelectorEventLoop for aiohttp WebSockets used by edge-tts
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Maximum words per TTS chunk — keeps each WebSocket connection short enough
# to avoid mid-stream disconnects from speech.platform.bing.com.
_CHUNK_WORDS = 400


def _split_text(text: str, max_words: int = _CHUNK_WORDS) -> List[str]:
    """
    Split *text* into chunks of at most *max_words* words, breaking only at
    sentence boundaries ('. ', '? ', '! ') to avoid cutting words mid-sentence.
    """
    # Normalise whitespace
    text = " ".join(text.split())
    words = text.split(" ")
    chunks: List[str] = []
    current: List[str] = []

    for word in words:
        current.append(word)
        if len(current) >= max_words and word.endswith((".", "?", "!", "...", '."', '?"', '!"')):
            chunks.append(" ".join(current))
            current = []

    if current:
        chunks.append(" ".join(current))

    return chunks


async def _synthesise_chunk(text: str, output_path: str, voice: str) -> List[Dict]:
    """
    Synthesise *text* via the streaming edge-tts API.
    Returns audio to *output_path* and a list of word-boundary events
    ``{offset_ms, duration_ms, text}`` for subtitle generation.
    """
    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=TTS_RATE,
        volume=TTS_VOLUME,
        pitch=TTS_PITCH,
    )
    word_events: List[Dict] = []
    audio_buf = bytearray()
    async for event in communicate.stream():
        if event["type"] == "audio":
            audio_buf.extend(event["data"])
        elif event["type"] == "WordBoundary":
            word_events.append({
                "offset_ms": event["offset"] // 10000,       # 100 ns → ms
                "duration_ms": max(event["duration"] // 10000, 80),
                "text": event["text"],
            })
    if not audio_buf:
        raise RuntimeError("edge-tts stream returned no audio data")
    with open(output_path, "wb") as fh:
        fh.write(bytes(audio_buf))
    return word_events


def _run_synthesis(text: str, output_path: str, voice: str) -> List[Dict]:
    """Blocking wrapper for _synthesise_chunk with up to 3 retries."""
    for attempt in range(1, 4):
        try:
            return asyncio.run(_synthesise_chunk(text, output_path, voice))
        except Exception as exc:
            if attempt < 3:
                wait = 2 ** attempt
                logger.warning("TTS attempt %d failed (%s). Retrying in %ds…", attempt, exc, wait)
                time.sleep(wait)
            else:
                raise
    return []  # unreachable


# ─────────────────────────── Subtitle helpers ───────────────────────────

def _probe_audio_ms(path: str) -> int:
    """Return audio duration in milliseconds using ffprobe. Returns 0 on failure."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_entries", "format=duration", path],
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(result.stdout)
        return int(float(data["format"]["duration"]) * 1000)
    except Exception:
        return 0


def _words_to_srt_entries(
    word_events: List[Dict],
    offset_ms: int = 0,
    max_words: int = 7,
    max_ms: int = 3200,
) -> List[Dict]:
    """Group word boundary events into subtitle line entries."""
    entries: List[Dict] = []
    buf: List[str] = []
    buf_start: Optional[int] = None
    for ev in word_events:
        start = ev["offset_ms"]
        end = start + ev["duration_ms"]
        if buf_start is None:
            buf_start = start
        buf.append(ev["text"])
        if len(buf) >= max_words or (end - buf_start) >= max_ms:
            entries.append({
                "start": buf_start + offset_ms,
                "end": end + offset_ms,
                "text": " ".join(buf),
            })
            buf, buf_start = [], None
    if buf and buf_start is not None:
        last = word_events[-1]
        end = last["offset_ms"] + last["duration_ms"]
        entries.append({
            "start": buf_start + offset_ms,
            "end": end + offset_ms,
            "text": " ".join(buf),
        })
    return entries


def _ms_to_srt_ts(ms: int) -> str:
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _write_srt(entries: List[Dict], srt_path: str) -> None:
    with open(srt_path, "w", encoding="utf-8") as fh:
        for i, e in enumerate(entries, 1):
            fh.write(
                f"{i}\n"
                f"{_ms_to_srt_ts(e['start'])} --> {_ms_to_srt_ts(e['end'])}\n"
                f"{e['text']}\n\n"
            )


def _concat_mp3s(chunk_paths: List[str], output_path: str) -> None:
    """Concatenate MP3 files using ffmpeg concat demuxer (lossless, no re-encode)."""
    # Write a temporary concat list file
    list_fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="tts_concat_")
    try:
        with os.fdopen(list_fd, "w") as fh:
            for p in chunk_paths:
                # ffmpeg concat list requires forward-slash paths
                fh.write(f"file '{p.replace(chr(92), '/')}'\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed:\n{result.stderr}")
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass


def generate_tts(
    text: str,
    output_path: str,
    voice_index: int = 0,
) -> Dict[str, Any]:
    """
    Synthesise *text* to an MP3 file at *output_path*.

    Long scripts are split into chunks to avoid WebSocket timeouts, then
    concatenated with ffmpeg.

    Returns:
        dict with:
            audio_path (str): Absolute path to the saved MP3.
            duration_seconds (float): Estimated audio duration.
    """
    voice = TTS_VOICES[voice_index % len(TTS_VOICES)]
    logger.info("Generating TTS with voice '%s' → %s", voice, output_path)

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    chunks = _split_text(text)
    logger.info("Script split into %d TTS chunk(s) of ~%d words each.", len(chunks), _CHUNK_WORDS)

    if len(chunks) == 1:
        # Single-chunk path — no concat needed
        all_word_events = _run_synthesis(chunks[0], str(out_path), voice)
    else:
        # Multi-chunk path: synthesise each to a temp file, then concat
        tmp_dir = out_path.parent
        chunk_paths: List[str] = []
        all_word_events: List[Dict] = []
        chunk_offset_ms = 0
        try:
            for i, chunk in enumerate(chunks):
                chunk_path = str(tmp_dir / f"_tts_chunk_{i:03d}.mp3")
                logger.info("  Chunk %d/%d (%d words)…", i + 1, len(chunks), len(chunk.split()))
                events = _run_synthesis(chunk, chunk_path, voice)
                chunk_paths.append(chunk_path)

                # Probe actual audio duration for accurate subtitle offsets
                chunk_ms = _probe_audio_ms(chunk_path)
                if chunk_ms == 0:
                    chunk_ms = int(len(chunk.split()) / TTS_WORDS_PER_MINUTE * 60_000)

                for ev in events:
                    all_word_events.append({
                        "offset_ms": ev["offset_ms"] + chunk_offset_ms,
                        "duration_ms": ev["duration_ms"],
                        "text": ev["text"],
                    })
                chunk_offset_ms += chunk_ms

            logger.info("Concatenating %d chunks → %s", len(chunk_paths), output_path)
            _concat_mp3s(chunk_paths, str(out_path))
        finally:
            for p in chunk_paths:
                try:
                    os.remove(p)
                except OSError:
                    pass

    # ── Generate SRT subtitle file ────────────────────────────────────────
    srt_path: Optional[str] = None
    if all_word_events:
        srt_path = str(out_path.with_suffix(".srt"))
        entries = _words_to_srt_entries(all_word_events)
        _write_srt(entries, srt_path)
        logger.info("Subtitles generated → %s (%d lines)", srt_path, len(entries))
    else:
        logger.warning("No word boundary events captured — subtitles not generated.")

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(f"TTS output file is missing or empty: {output_path}")

    word_count = len(text.split())
    duration_estimate = (word_count / TTS_WORDS_PER_MINUTE) * 60.0

    logger.info(
        "TTS complete. Words: %d | Estimated duration: %.0fs",
        word_count,
        duration_estimate,
    )
    return {
        "audio_path": str(out_path),
        "duration_seconds": duration_estimate,
        "subtitle_path": srt_path,
    }
