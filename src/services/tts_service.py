"""
TTS (Text-to-Speech) service using Microsoft Edge TTS.

Uses the edge-tts library (free, no API key required).
Voice selection alternates between female and male Indian English voices
based on the weekday parity to add variety across videos.

Long scripts are split into ~400-word chunks to avoid WebSocket timeouts,
then concatenated with ffmpeg.
"""
import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

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


async def _synthesise_chunk(text: str, output_path: str, voice: str) -> None:
    """Synthesise a single text chunk to *output_path* (MP3)."""
    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=TTS_RATE,
        volume=TTS_VOLUME,
        pitch=TTS_PITCH,
    )
    await communicate.save(output_path)


def _run_synthesis(text: str, output_path: str, voice: str) -> None:
    """Run TTS synthesis for a chunk, retrying up to 3 times on network errors."""
    for attempt in range(1, 4):
        try:
            asyncio.run(_synthesise_chunk(text, output_path, voice))
            return
        except Exception as exc:
            if attempt < 3:
                wait = 2 ** attempt
                logger.warning("TTS attempt %d failed (%s). Retrying in %ds…", attempt, exc, wait)
                time.sleep(wait)
            else:
                raise


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
        _run_synthesis(chunks[0], str(out_path), voice)
    else:
        # Multi-chunk path: synthesise each to a temp file, then concat
        tmp_dir = out_path.parent
        chunk_paths: List[str] = []
        try:
            for i, chunk in enumerate(chunks):
                chunk_path = str(tmp_dir / f"_tts_chunk_{i:03d}.mp3")
                logger.info("  Chunk %d/%d (%d words)…", i + 1, len(chunks), len(chunk.split()))
                _run_synthesis(chunk, chunk_path, voice)
                chunk_paths.append(chunk_path)

            logger.info("Concatenating %d chunks → %s", len(chunk_paths), output_path)
            _concat_mp3s(chunk_paths, str(out_path))
        finally:
            for p in chunk_paths:
                try:
                    os.remove(p)
                except OSError:
                    pass

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
    }
