"""
Generate sample MP3 files for all 7 TTS voices with mstts:express-as emotions.
Output: assets/voice_samples/  (one file per voice/day)

Uses Azure Cognitive Services TTS REST API (via tts_service._azure_synthesise)
when AZURE_SPEECH_KEY is set; falls back to edge-tts with rate/pitch prosody.
"""
import os
import subprocess
import sys
import tempfile
import time

# Force SelectorEventLoop for edge-tts fallback path
import asyncio
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import edge_tts
from src.services.tts_service import _split_by_emotion, _run_synthesis
from src.config import TTS_VOICES, AZURE_SPEECH_KEY

SAMPLE_TEXT = (
    "Welcome! Today we are talking about something that could genuinely change the way you think about money. "
    "Ha! And you know what genuinely made me laugh the first time I understood this properly? "
    "The solution is not complicated. It is actually really simple. "
    "So simple that once you see it, you will wonder why nobody explained it to you years ago. "
    "But here is where I have to pause for a second. Because this is also the part "
    "that honestly... made me sad. Genuinely sad. "
    "Because millions of people are missing this every single day. "
    "Not because they are not smart. Not because they do not care about their money. "
    "Simply because nobody ever sat down and explained it to them clearly. "
    "Well, that changes right now. Let me walk you through it step by step."
)

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "voice_samples")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def synthesise(voice: str, out_path: str) -> None:
    """Synthesise all emotion sections and concatenate into one MP3."""
    sections = _split_by_emotion(SAMPLE_TEXT)
    if len(sections) == 1:
        chunk_text, style = sections[0]
        _run_synthesis(chunk_text, out_path, voice, style=style)
        return

    tmp_dir = tempfile.mkdtemp()
    chunk_paths = []
    for i, (chunk_text, style) in enumerate(sections):
        cp = os.path.join(tmp_dir, f"chunk_{i:03d}.mp3")
        _run_synthesis(chunk_text, cp, voice, style=style)
        chunk_paths.append(cp)
        time.sleep(1)

    list_path = os.path.join(tmp_dir, "list.txt")
    with open(list_path, "w") as fh:
        for cp in chunk_paths:
            fh.write(f"file '{cp.replace(chr(92), '/')}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", out_path],
        check=True, capture_output=True,
    )


def main() -> None:
    backend = "Azure Cognitive Services" if AZURE_SPEECH_KEY else "edge-tts (no Azure key)"
    print(f"Output folder: {OUTPUT_DIR}")
    print(f"TTS backend:   {backend}\n")
    for idx, (day, voice) in enumerate(zip(DAYS, TTS_VOICES)):
        filename = f"{day}_{voice}.mp3"
        out_path = os.path.join(OUTPUT_DIR, filename)

        if os.path.exists(out_path):
            size_kb = os.path.getsize(out_path) // 1024
            print(f"[SKIP] {day} | {voice:<25} | already exists ({size_kb} KB)")
            continue

        if idx > 0:
            time.sleep(5)  # pause between voices to avoid endpoint throttling

        for attempt in range(5):
            try:
                synthesise(voice, out_path)
                size_kb = os.path.getsize(out_path) // 1024
                print(f"[OK] {day} | {voice:<25} | {size_kb} KB -> {filename}")
                break
            except Exception as exc:
                if attempt == 4:
                    print(f"[FAIL] {day} | {voice} | {exc!s:.120}")
                else:
                    wait = 10 * (attempt + 1)
                    print(f"  retry {attempt + 1}/4 for {voice} (wait {wait}s)...")
                    time.sleep(wait)

    print(f"\nDone. Open the folder to listen:\n  {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
