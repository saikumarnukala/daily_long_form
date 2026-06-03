"""Quick Deepgram Aura-2 TTS smoke test. Requires DEEPGRAM_API_KEY in .env."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.config import DEEPGRAM_API_KEY, PATHS
from src.services.tts_service import generate_tts

SAMPLE = (
    "Welcome! Today we are talking about money. "
    "Ha! The solution is simple. "
    "But here is where I have to pause. It made me sad. "
    "Well, that changes right now."
)


def main() -> None:
    if not DEEPGRAM_API_KEY:
        print("ERROR: Set DEEPGRAM_API_KEY in .env")
        sys.exit(1)

    out = PATHS["temp"] / "deepgram_tts_test.mp3"
    out.parent.mkdir(parents=True, exist_ok=True)
    print(f"Testing Deepgram TTS -> {out}")
    result = generate_tts(SAMPLE, str(out), voice_index=0)
    size_kb = os.path.getsize(result["audio_path"]) // 1024
    print(f"OK: {result['audio_path']} ({size_kb} KB, ~{result['duration_seconds']:.0f}s est.)")


if __name__ == "__main__":
    main()
