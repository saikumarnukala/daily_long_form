"""Quick test: which voices accept SSML emotion styles."""
import asyncio
import os
import re
import sys
import tempfile
import time

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import edge_tts

TEXT = (
    "Ha! And you know what genuinely made me laugh the first time I understood this properly? "
    "The solution is not complicated. It is actually really simple. "
    "But here is where I have to pause for a second. Because this is also the part that honestly... "
    "made me sad. Genuinely sad. "
    "Simply because nobody ever sat down and explained it to them clearly. "
    "Well, that changes right now."
)


def make_ssml(voice: str, text: str) -> str:
    lang = "en-US" if "en-US" in voice else "en-GB"
    body = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    body = re.sub(
        r"(Ha!.*?)(?=But here is where)",
        r'<mstts:express-as style="cheerful">\1</mstts:express-as>',
        body,
    )
    body = re.sub(
        r"(But here is where.*?clearly\.)",
        r'<mstts:express-as style="sad">\1</mstts:express-as>',
        body,
    )
    return (
        f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' "
        f"xmlns:mstts='http://www.w3.org/2001/mstts' xml:lang='{lang}'>"
        f"<voice name='{voice}'>{body}</voice></speak>"
    )


async def test(voice: str) -> int:
    ssml = make_ssml(voice, TEXT)
    out = os.path.join(tempfile.mkdtemp(), "test.mp3")
    c = edge_tts.Communicate(text=ssml, voice=voice, rate="+0%", volume="+0%", pitch="+0Hz")
    await c.save(out)
    return os.path.getsize(out)


candidates = [
    "en-US-EricNeural",
    "en-US-AvaNeural",
    "en-US-RogerNeural",
    "en-US-EmmaNeural",
    "en-US-MichelleNeural",
    "en-US-SteffanNeural",
]

for voice in candidates:
    for attempt in range(3):
        try:
            size = asyncio.run(test(voice))
            print(f"{voice}: SSML OK {size}b")
            break
        except Exception as e:
            if attempt == 2:
                print(f"{voice}: SSML FAIL - {e!s:.70}")
            else:
                time.sleep(4)
