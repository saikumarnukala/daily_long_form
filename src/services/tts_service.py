"""
TTS service using Deepgram Aura-2.

Scripts are split by emotion markers, then by Aura's per-request character limit.
Emotion is conveyed via speaking speed and punctuation (Aura-2 is context-aware).
"""
import os
import json
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


def _audio_duration(path: str) -> float:
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'json', path], capture_output=True, text=True, check=True, timeout=30,
    )
    duration = float(json.loads(result.stdout)['format']['duration'])
    if duration <= 0:
        raise ValueError(f'Invalid audio duration for {path}')
    return duration


def generate_tts(
    text: str, output_path: str, voice_index: int = 0,
    scenes: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Synthesize caption-sized phrases and measure their actual audio intervals."""
    model = voice_for_weekday(voice_index % 7)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    scenes = scenes or [{'heading': 'Narration', 'narration': text}]
    if ' '.join(text.split()) != ' '.join(' '.join(s['narration'] for s in scenes).split()):
        raise ValueError('Scene narration must match the complete spoken script.')
    captions = []
    scene_timings = []
    chunk_paths = []
    cursor = 0.0
    # A separate directory prevents stale chunks from a previous failed run.
    with tempfile.TemporaryDirectory(prefix='narration_', dir=out.parent) as tmp:
        for scene in scenes:
            scene_start = cursor
            for phrase in _split_by_char_limit(scene['narration'], max_chars=160):
                path = str(Path(tmp) / f'{len(chunk_paths):04d}.mp3')
                _run_synthesis(phrase, path, model)
                duration = _audio_duration(path)
                captions.append((cursor, cursor + duration, phrase))
                cursor += duration
                chunk_paths.append(path)
            scene_timings.append({'heading': scene['heading'], 'start': scene_start, 'end': cursor})
        if not chunk_paths:
            raise ValueError('Cannot narrate an empty script.')
        _concat_mp3s(chunk_paths, str(out))
    actual_duration = _audio_duration(str(out))
    # MP3 container padding may slightly change the concatenated duration.
    scale = actual_duration / cursor
    srt_path = str(out.with_suffix('.srt'))
    with open(srt_path, 'w', encoding='utf-8') as fh:
        for index, (start, end, phrase) in enumerate(captions, 1):
            fh.write(
                f'{index}\n{_ms_to_srt_ts(round(start * scale * 1000))} --> '
                f'{_ms_to_srt_ts(round(end * scale * 1000))}\n{phrase}\n\n'
            )
    for timing in scene_timings:
        timing['start'] *= scale
        timing['end'] *= scale
    return {'audio_path': str(out), 'duration_seconds': actual_duration,
            'subtitle_path': srt_path, 'scene_timings': scene_timings}
