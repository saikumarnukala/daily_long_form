"""
TTS (Text-to-Speech) service using Microsoft Edge TTS.

Uses the edge-tts library (free, no API key required).
Voice selection alternates between female and male English voices
based on the weekday parity to add variety across videos.

Long scripts are split into ~400-word chunks to avoid WebSocket timeouts,
then concatenated with ffmpeg.

Subtitles are generated from the text + audio duration (time-based, always works).
"""
import asyncio
import os
import re
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

_CHUNK_WORDS = 400

# ─── SSML emotion injection ──────────────────────────────────────────────────
# Voices confirmed to support mstts:express-as cheerful + sad styles.
_SSML_CAPABLE = {
    "en-US-AriaNeural",
    "en-US-GuyNeural",
    "en-US-EricNeural",
    "en-US-JennyNeural",
    "en-US-AvaNeural",
    "en-US-RogerNeural",
    "en-GB-SoniaNeural",
}


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


def _build_ssml(text: str, voice: str) -> str:
    """Return an SSML document with emotion tags injected around emotional phrases.

    For non-SSML-capable voices the original plain text is returned unchanged.
    The function works on already-flattened single-line text (after _split_text).
    """
    if voice not in _SSML_CAPABLE:
        return text

    # Derive xml:lang from the voice short-name (e.g. en-US-AriaNeural → en-US)
    m = re.match(r"(en-[A-Z]{2})", voice)
    xml_lang = m.group(1) if m else "en-US"

    body = _xml_escape(text)

    # Cheerful: the "Ha!" laugh sentence up to the sad pivot
    body = re.sub(
        r"(Ha!.*?)(?=But here is where)",
        r'<mstts:express-as style="cheerful">\1</mstts:express-as>',
        body,
    )
    # Sad: from "But here is where I have to pause" through "clearly."
    body = re.sub(
        r"(But here is where.*?clearly\.)",
        r'<mstts:express-as style="sad">\1</mstts:express-as>',
        body,
    )

    return (
        f"<speak version='1.0' "
        f"xmlns='http://www.w3.org/2001/10/synthesis' "
        f"xmlns:mstts='http://www.w3.org/2001/mstts' "
        f"xml:lang='{xml_lang}'>"
        f"<voice name='{voice}'>{body}</voice>"
        f"</speak>"
    )


def _split_text(text: str, max_words: int = _CHUNK_WORDS) -> List[str]:
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
    content = _build_ssml(text, voice)  # returns SSML or plain text
    communicate = edge_tts.Communicate(text=content, voice=voice, rate=TTS_RATE, volume=TTS_VOLUME, pitch=TTS_PITCH)
    await communicate.save(output_path)


def _run_synthesis(text: str, output_path: str, voice: str) -> None:
    for attempt in range(1, 4):
        try:
            asyncio.run(_synthesise_chunk(text, output_path, voice))
            return
        except Exception as exc:
            if attempt < 3:
                wait = 2 ** attempt
                logger.warning("TTS attempt %d failed (%s). Retrying in %ds...", attempt, exc, wait)
                time.sleep(wait)
            else:
                raise


def _concat_mp3s(chunk_paths: List[str], output_path: str) -> None:
    list_fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="tts_concat_")
    try:
        with os.fdopen(list_fd, "w") as fh:
            for p in chunk_paths:
                fh.write(f"file '{p.replace(chr(92), '/')}'\n")
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", output_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed:\n{result.stderr}")
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass


def _ms_to_srt_ts(ms: int) -> str:
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _generate_timed_srt(text: str, audio_duration_s: float, srt_path: str) -> None:
    clean = text.replace("\n", " ")
    words = clean.split()
    if not words or audio_duration_s <= 0:
        return
    GROUP = 7
    words_per_ms = len(words) / (audio_duration_s * 1000)
    lines: List[str] = []
    for idx in range(0, len(words), GROUP):
        group = words[idx: idx + GROUP]
        start_ms = int(idx / words_per_ms)
        end_ms = int((idx + len(group)) / words_per_ms)
        n = idx // GROUP + 1
        lines.append(f"{n}\n{_ms_to_srt_ts(start_ms)} --> {_ms_to_srt_ts(end_ms)}\n{' '.join(group)}\n")
    with open(srt_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    logger.info("Subtitles written: %d lines -> %s", len(lines), srt_path)


def generate_tts(text: str, output_path: str, voice_index: int = 0) -> Dict[str, Any]:
    voice = TTS_VOICES[voice_index % len(TTS_VOICES)]
    logger.info("Generating TTS with voice '%s' -> %s", voice, output_path)
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    chunks = _split_text(text)
    logger.info("Script split into %d TTS chunk(s) of ~%d words each.", len(chunks), _CHUNK_WORDS)

    if len(chunks) == 1:
        _run_synthesis(chunks[0], str(out_path), voice)
    else:
        tmp_dir = out_path.parent
        chunk_paths: List[str] = []
        try:
            for i, chunk in enumerate(chunks):
                chunk_path = str(tmp_dir / f"_tts_chunk_{i:03d}.mp3")
                logger.info("  Chunk %d/%d (%d words)...", i + 1, len(chunks), len(chunk.split()))
                _run_synthesis(chunk, chunk_path, voice)
                chunk_paths.append(chunk_path)
            logger.info("Concatenating %d chunks -> %s", len(chunk_paths), output_path)
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

    srt_path: Optional[str] = None
    try:
        srt_path = str(out_path.with_suffix(".srt"))
        _generate_timed_srt(text, duration_estimate, srt_path)
    except Exception as exc:
        logger.warning("Subtitle generation failed (%s) -- continuing without.", exc)
        srt_path = None

    logger.info("TTS complete. Words: %d | Estimated duration: %.0fs", word_count, duration_estimate)
    return {"audio_path": str(out_path), "duration_seconds": duration_estimate, "subtitle_path": srt_path}