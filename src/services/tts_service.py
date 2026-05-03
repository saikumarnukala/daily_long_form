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
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

import edge_tts

from src.config import (
    AZURE_SPEECH_KEY, AZURE_SPEECH_REGION,
    TTS_PITCH, TTS_RATE, TTS_VOICES, TTS_VOLUME, TTS_WORDS_PER_MINUTE,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Windows requires SelectorEventLoop for aiohttp WebSockets used by edge-tts
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_CHUNK_WORDS = 400

# ─── Emotion via mstts:express-as (Azure) or rate/pitch fallback ────────────
# Azure Cognitive Services TTS supports real mstts:express-as styles.
# When AZURE_SPEECH_KEY is not set, prosody rate/pitch is used instead.
#
# (regex_start, regex_end_exclusive, ssml_style)
_EMOTION_SPLITS = [
    (r"Ha!", r"But here is where", "cheerful"),
    (r"But here is where", r"Well, that changes right now", "sad"),
]

# Prosody fallback values when Azure is not configured
_STYLE_PROSODY = {
    "cheerful": ("+20%", "+15Hz"),
    "sad":      ("-10%", "-10Hz"),
}


def _split_by_emotion(text: str):
    """Return list of (chunk_text, style) tuples.

    style is an mstts:express-as style string ('cheerful', 'sad') or None for
    default delivery.  Falls back to [(text, None)] if markers are not found.
    """
    flat = " ".join(text.split())

    boundaries = []
    for start_pat, end_pat, style in _EMOTION_SPLITS:
        m_start = re.search(start_pat, flat)
        m_end = re.search(end_pat, flat)
        if m_start and m_end and m_start.start() < m_end.start():
            boundaries.append((m_start.start(), m_end.start(), style))

    if not boundaries:
        return [(flat, None)]

    boundaries.sort(key=lambda x: x[0])
    parts = []
    cursor = 0
    for seg_start, seg_end, style in boundaries:
        if seg_start > cursor:
            parts.append((flat[cursor:seg_start].strip(), None))
        parts.append((flat[seg_start:seg_end].strip(), style))
        cursor = seg_end
    if cursor < len(flat):
        parts.append((flat[cursor:].strip(), None))

    return [(p, s) for p, s in parts if p]


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


def _build_azure_ssml(text: str, voice: str, style: str = None) -> str:
    """Build SSML for Azure Cognitive Services TTS REST API."""
    from xml.sax.saxutils import escape
    body = escape(text)
    if style:
        body = f'<mstts:express-as style="{style}">{body}</mstts:express-as>'
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="en-US">'
        f'<voice name="{voice}">'
        f'{body}'
        '</voice>'
        '</speak>'
    )


def _azure_synthesise(text: str, output_path: str, voice: str, style: str = None) -> None:
    """Synthesise via Azure Cognitive Services TTS REST API."""
    url = f"https://{AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
        "User-Agent": "long_videos_daily",
    }
    ssml = _build_azure_ssml(text, voice, style)
    resp = requests.post(url, headers=headers, data=ssml.encode("utf-8"), timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Azure TTS HTTP {resp.status_code}: {resp.text[:200]}")
    with open(output_path, "wb") as fh:
        fh.write(resp.content)


async def _synthesise_chunk_edge(text: str, output_path: str, voice: str,
                                 rate: str = TTS_RATE, pitch: str = TTS_PITCH) -> None:
    communicate = edge_tts.Communicate(
        text=text, voice=voice, rate=rate, volume=TTS_VOLUME, pitch=pitch
    )
    await communicate.save(output_path)


def _run_synthesis(text: str, output_path: str, voice: str, style: str = None) -> None:
    """Synthesise one chunk. Uses Azure TTS if key is configured, else edge-tts."""
    use_azure = bool(AZURE_SPEECH_KEY)
    for attempt in range(1, 4):
        try:
            if use_azure:
                _azure_synthesise(text, output_path, voice, style=style)
            else:
                rate, pitch = _STYLE_PROSODY.get(style, (TTS_RATE, TTS_PITCH))
                asyncio.run(_synthesise_chunk_edge(text, output_path, voice, rate=rate, pitch=pitch))
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

    use_azure = bool(AZURE_SPEECH_KEY)
    logger.info("TTS backend: %s", "Azure Cognitive Services" if use_azure else "edge-tts (fallback)")

    # Split by emotional section first, then by word-count within each section
    emotion_sections = _split_by_emotion(text)
    all_chunks: List[tuple] = []  # (text, style)
    for section_text, style in emotion_sections:
        for chunk in _split_text(section_text):
            all_chunks.append((chunk, style))

    logger.info("Script: %d emotion section(s) -> %d TTS chunk(s) total.",
                len(emotion_sections), len(all_chunks))

    tmp_dir = out_path.parent
    chunk_paths: List[str] = []
    try:
        for i, (chunk_text, style) in enumerate(all_chunks):
            chunk_path = str(tmp_dir / f"_tts_chunk_{i:03d}.mp3")
            logger.info("  Chunk %d/%d (%d words, style=%s)...",
                        i + 1, len(all_chunks), len(chunk_text.split()), style or "default")
            _run_synthesis(chunk_text, chunk_path, voice, style=style)
            chunk_paths.append(chunk_path)

        if len(chunk_paths) == 1:
            shutil.move(chunk_paths[0], str(out_path))
            chunk_paths.clear()
        else:
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