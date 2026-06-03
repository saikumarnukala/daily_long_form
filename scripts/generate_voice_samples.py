"""
Generate sample MP3 files for all 7 Deepgram Aura-2 voices.
Output: assets/voice_samples/
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.config import DEEPGRAM_API_KEY, TTS_VOICES
from src.services.tts_service import generate_tts

SAMPLE_TEXT = (
    "Welcome! Today we are talking about something that could change how you think about money. "
    "Ha! And you know what made me laugh? The solution is simple. "
    "But here is where I have to pause. Because millions of people are missing this every day. "
    "Well, that changes right now. Let me walk you through it step by step."
)

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "voice_samples")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def main() -> None:
    if not DEEPGRAM_API_KEY:
        print("ERROR: Set DEEPGRAM_API_KEY in .env")
        sys.exit(1)

    print(f"Output folder: {OUTPUT_DIR}")
    print("TTS backend:   Deepgram Aura-2\n")
    for idx, (day, model) in enumerate(zip(DAYS, TTS_VOICES)):
        out_path = os.path.join(OUTPUT_DIR, f"{day}_{model}.mp3")
        if os.path.exists(out_path):
            size_kb = os.path.getsize(out_path) // 1024
            print(f"[SKIP] {day} | {model:<22} | already exists ({size_kb} KB)")
            continue
        print(f"[GEN ] {day} | {model}...")
        generate_tts(SAMPLE_TEXT, out_path, voice_index=idx)
        print(f"       -> {out_path}")


if __name__ == "__main__":
    main()
