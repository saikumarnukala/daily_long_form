"""Generate a short MP3 for each weekday voice (Mon–Sun). Requires DEEPGRAM_API_KEY."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.config import DEEPGRAM_API_KEY, PATHS, TTS_VOICE_BY_WEEKDAY, TTS_WEEKDAY_NAMES
from src.services.tts_service import generate_tts

LINE = "This is the Finance Decoded voice for {day}."


def main() -> None:
    if not DEEPGRAM_API_KEY:
        print("ERROR: Set DEEPGRAM_API_KEY in .env")
        sys.exit(1)

    out_dir = PATHS["temp"] / "weekday_voices"
    out_dir.mkdir(parents=True, exist_ok=True)
    print("Weekday -> Deepgram Aura-2 model:\n")
    for wd in range(7):
        voice = TTS_VOICE_BY_WEEKDAY[wd]
        print(f"  {TTS_WEEKDAY_NAMES[wd]:9} (weekday {wd}) -> {voice}")

    print(f"\nGenerating samples in {out_dir}\n")
    for wd in range(7):
        voice = TTS_VOICE_BY_WEEKDAY[wd]
        day = TTS_WEEKDAY_NAMES[wd]
        path = out_dir / f"{wd}_{day.lower()}_{voice}.mp3"
        print(f"[{wd}] {day} / {voice}...")
        generate_tts(LINE.format(day=day), str(path), voice_index=wd)
        kb = path.stat().st_size // 1024
        print(f"     OK {path.name} ({kb} KB)")

    voices_used = [TTS_VOICE_BY_WEEKDAY[i] for i in range(7)]
    unique = len(set(voices_used))
    print(f"\nDone. {unique} unique Deepgram model(s) across 7 days.")


if __name__ == "__main__":
    main()
