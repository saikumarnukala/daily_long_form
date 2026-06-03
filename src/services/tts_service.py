"""
TTS service using Deepgram Aura-2.

Scripts are split by emotion markers, then by Aura's per-request character limit.
Emotion is conveyed via speaking speed and punctuation (Aura-2 is context-aware).
"""
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from src.config import (
    DEEPGRAM_API_KEY,
    DEEPGRAM_TTS_API_URL,
    DEEPGRAM_TTS_MAX_CHARS,
    TTS_WEEKDAY_NAMES,
    TTS_WORDS_PER_MINUTE,
    voice_for_weekday,
)
from src.utils.logger import get_logger

logger = get_logger(__name__)

_EMOTION_SPLITS = [
    (r"Ha!", r"But here is where", "cheerful"),
    (r"But here is where", r"Well, that changes right now", "sad"),
    (r"Well, that changes right now", None, "engaged"),  # uplifting close
]

# Aura-2 speed (0.7–1.5) + phrasing — see Deepgram Aura-2 formatting docs
_STYLE_CONFIG = {
    "cheerful": {"speed": 1.18},
    "sad": {"speed": 0.82},
    "engaged": {"speed": 1.06},
}


def _split_by_emotion(text: str) -> List[Tuple[str, Optional[str]]]:
    flat = " ".join(text.split())

    boundaries = []
    for start_pat, end_pat, style in _EMOTION_SPLITS:
        m_start = re.search(start_pat, flat)
        if not m_start:
            continue
        if end_pat:
            m_end = re.search(end_pat, flat)
            if m_end and m_start.start() < m_end.start():
                boundaries.append((m_start.start(), m_end.start(), style))
        else:
            boundaries.append((m_start.start(), len(flat), style))

    if not boundaries:
        return [(flat, None)]

    boundaries.sort(key=lambda x: x[0])
    parts: List[Tuple[str, Optional[str]]] = []
    cursor = 0
    for seg_start, seg_end, style in boundaries:
        if seg_start > cursor:
            parts.append((flat[cursor:seg_start].strip(), None))
        parts.append((flat[seg_start:seg_end].strip(), style))
        cursor = seg_end
    if cursor < len(flat):
        parts.append((flat[cursor:].strip(), None))

    return [(p, s) for p, s in parts if p]


def _split_by_char_limit(text: str, max_chars: int = DEEPGRAM_TTS_MAX_CHARS) -> List[str]:
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)
    segments: List[str] = []
    current = ""

    for sentence in sentences:
        if not sentence:
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            segments.append(current)
            current = ""
        if len(sentence) <= max_chars:
            current = sentence
            continue
        words = sentence.split()
        buf: List[str] = []
        for word in words:
            trial = (" ".join(buf + [word])).strip()
            if len(trial) <= max_chars:
                buf.append(word)
            else:
                if buf:
                    segments.append(" ".join(buf))
                buf = [word] if len(word) <= max_chars else []
                if len(word) > max_chars:
                    for i in range(0, len(word), max_chars):
                        segments.append(word[i : i + max_chars])
        if buf:
            current = " ".join(buf)

    if current:
        segments.append(current)

    return [s for s in segments if s]


def _style_speed(style: Optional[str]) -> float:
    if not style:
        return 1.0
    return _STYLE_CONFIG.get(style, {}).get("speed", 1.0)


def _prepare_text_for_style(text: str, style: Optional[str]) -> str:
    """Tune punctuation and pacing so Aura-2 sounds more expressive."""
    text = " ".join(text.split())
    if style == "cheerful":
        text = text.replace("properly?", "properly!")
        text = text.replace("years ago.", "years ago!")
        # Short beats help energetic delivery
        text = text.replace("really simple.", "really simple!")
        return text
    if style == "sad":
        text = text.replace("pause for a second.", "pause for a second...")
        text = text.replace("Genuinely sad.", "Genuinely sad...")
        text = text.replace(
            "every single day.",
            "every single day...",
        )
        text = text.replace(
            "explained it to them clearly.",
            "explained it to them clearly...",
        )
        return text
    if style == "engaged":
        text = text.replace(
            "right now.",
            "right now!",
        )
        text = text.replace(
            "step by step.",
            "step by step!",
        )
        return text
    return text


def _deepgram_synthesise(
    text: str,
    output_mp3_path: str,
    model: str,
    style: Optional[str] = None,
) -> None:
    if not DEEPGRAM_API_KEY:
        raise RuntimeError(
            "DEEPGRAM_API_KEY is not set. Get a key at https://console.deepgram.com/"
        )
    if len(text) > DEEPGRAM_TTS_MAX_CHARS:
        raise ValueError(
            f"TTS segment too long ({len(text)} chars, max {DEEPGRAM_TTS_MAX_CHARS})"
        )

    speed = _style_speed(style)
    params = {
        "model": model,
        "encoding": "mp3",
        "speed": str(speed),
    }
    resp = requests.post(
        DEEPGRAM_TTS_API_URL,
        headers={
            "Authorization": f"Token {DEEPGRAM_API_KEY}",
            "Content-Type": "application/json",
        },
        params=params,
        json={"text": text},
        timeout=180,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Deepgram TTS HTTP {resp.status_code}: {resp.text[:300]}"
        )

    with open(output_mp3_path, "wb") as fh:
        fh.write(resp.content)


def _run_synthesis(
    text: str,
    output_path: str,
    model: str,
    style: Optional[str] = None,
) -> None:
    prepared = _prepare_text_for_style(text, style)
    for attempt in range(1, 4):
        try:
            _deepgram_synthesise(prepared, output_path, model, style=style)
            return
        except Exception as exc:
            if attempt < 3:
                wait = 2 ** attempt
                logger.warning(
                    "TTS attempt %d failed (%s). Retrying in %ds...", attempt, exc, wait
                )
                time.sleep(wait)
            else:
                raise


def _concat_mp3s(chunk_paths: List[str], output_path: str) -> None:
    list_fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="tts_concat_")
    try:
        with os.fdopen(list_fd, "w") as fh:
            for p in chunk_paths:
                fh.write(f"file '{p.replace(chr(92), '/')}'\n")
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_path, "-c", "copy", output_path,
        ]
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
    group = 7
    words_per_ms = len(words) / (audio_duration_s * 1000)
    lines: List[str] = []
    for idx in range(0, len(words), group):
        chunk_words = words[idx : idx + group]
        start_ms = int(idx / words_per_ms)
        end_ms = int((idx + len(chunk_words)) / words_per_ms)
        n = idx // group + 1
        lines.append(
            f"{n}\n{_ms_to_srt_ts(start_ms)} --> {_ms_to_srt_ts(end_ms)}\n"
            f"{' '.join(chunk_words)}\n"
        )
    with open(srt_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    logger.info("Subtitles written: %d lines -> %s", len(lines), srt_path)


def generate_tts(text: str, output_path: str, voice_index: int = 0) -> Dict[str, Any]:
    weekday = voice_index % 7
    model = voice_for_weekday(weekday)
    day_name = TTS_WEEKDAY_NAMES[weekday]
    logger.info(
        "Generating TTS | %s (weekday %d) | model '%s' -> %s",
        day_name, weekday, model, output_path,
    )
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("TTS backend: Deepgram Aura-2")

    emotion_sections = _split_by_emotion(text)
    all_chunks: List[Tuple[str, Optional[str]]] = []
    for section_text, style in emotion_sections:
        for segment in _split_by_char_limit(section_text):
            all_chunks.append((segment, style))

    logger.info(
        "Script: %d emotion section(s) -> %d TTS segment(s) (max %d chars each).",
        len(emotion_sections),
        len(all_chunks),
        DEEPGRAM_TTS_MAX_CHARS,
    )

    tmp_dir = out_path.parent
    chunk_paths: List[str] = []
    try:
        for i, (chunk_text, style) in enumerate(all_chunks):
            chunk_path = str(tmp_dir / f"_tts_chunk_{i:03d}.mp3")
            logger.info(
                "  Segment %d/%d (%d chars, style=%s, speed=%s)...",
                i + 1,
                len(all_chunks),
                len(chunk_text),
                style or "default",
                _style_speed(style),
            )
            _run_synthesis(chunk_text, chunk_path, model, style=style)
            chunk_paths.append(chunk_path)

        if len(chunk_paths) == 1:
            shutil.move(chunk_paths[0], str(out_path))
            chunk_paths.clear()
        else:
            logger.info("Concatenating %d segments -> %s", len(chunk_paths), output_path)
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
    return {
        "audio_path": str(out_path),
        "duration_seconds": duration_estimate,
        "subtitle_path": srt_path,
    }
