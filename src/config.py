"""
Central configuration module.
All settings are loaded from environment variables via python-dotenv.
"""
import datetime
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_YEAR = datetime.date.today().year

# ─────────────────────────── Base Paths ───────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = BASE_DIR / "assets"
TEMP_DIR = ASSETS_DIR / "temp"
OUTPUT_DIR = ASSETS_DIR / "output"
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

PATHS = {
    "base": BASE_DIR,
    "assets": ASSETS_DIR,
    "temp": TEMP_DIR,
    "output": OUTPUT_DIR,
    "data": DATA_DIR,
    "logs": LOGS_DIR,
    "history": DATA_DIR / "history.json",
    "bgmusic": ASSETS_DIR / "bgmusic.mp3",
}

# ─────────────────────────── API Keys ───────────────────────────
PEXELS_API_KEY: str = os.getenv("PEXELS_API_KEY", "")
YOUTUBE_CLIENT_ID: str = os.getenv("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET: str = os.getenv("YOUTUBE_CLIENT_SECRET", "")
YOUTUBE_REFRESH_TOKEN: str = os.getenv("YOUTUBE_REFRESH_TOKEN", "")

# Deepgram Aura TTS — https://developers.deepgram.com/docs/text-to-speech
DEEPGRAM_API_KEY: str = os.getenv("DEEPGRAM_API_KEY", "")
DEEPGRAM_TTS_API_URL: str = "https://api.deepgram.com/v1/speak"
DEEPGRAM_TTS_MAX_CHARS: int = 1900  # Aura-2 allows up to 2000 chars per request

# ─────────────────────────── API Endpoints ───────────────────────────
PEXELS_VIDEO_API = "https://api.pexels.com/videos/search"
PEXELS_PHOTO_API = "https://api.pexels.com/v1/search"

# ─────────────────────────── TTS (Deepgram Aura-2) ───────────────────────────
# One Aura-2 model per weekday (0=Mon … 6=Sun).
TTS_WEEKDAY_NAMES = [
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
]
TTS_VOICE_BY_WEEKDAY: dict[int, str] = {
    0: "aura-2-thalia-en",      # Mon — feminine, energetic
    1: "aura-2-apollo-en",      # Tue — masculine, confident
    2: "aura-2-odysseus-en",    # Wed — masculine, professional
    3: "aura-2-andromeda-en",   # Thu — feminine, expressive
    4: "aura-2-helena-en",      # Fri — feminine, warm
    5: "aura-2-arcas-en",       # Sat — masculine, smooth
    6: "aura-2-draco-en",        # Sun — masculine, British
}
TTS_VOICES = [TTS_VOICE_BY_WEEKDAY[i] for i in range(7)]


def voice_for_weekday(weekday: int) -> str:
    """Return the Deepgram Aura-2 model ID for weekday 0–6."""
    return TTS_VOICE_BY_WEEKDAY[weekday % 7]


TTS_WORDS_PER_MINUTE = 175

# ─────────────────────────── Video Specs ───────────────────────────
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_FPS = 30
VIDEO_BITRATE = "8000k"
VIDEO_CODEC = "libx264"
AUDIO_CODEC = "aac"

THUMBNAIL_WIDTH = 1280
THUMBNAIL_HEIGHT = 720

# ─────────────────────────── 7-Day Topic Schedule ───────────────────────────
TOPIC_SCHEDULE = {
    0: {
        "category": "Stock Market Basics",
        "keywords": ["stock market", "trading", "equity investment"],
        "subtopics": [
            "How to Start Investing in Nifty 50",
            "Understanding Smallcap Stocks",
            "Sectoral Funds vs Diversified Funds",
            "How to Pick Bluechip Stocks",
        ],
    },
    1: {
        "category": "Mutual Funds",
        "keywords": ["mutual fund", "SIP investment", "fund management"],
        "subtopics": [
            "How SIP Works: A Beginner's Guide",
            f"Best Mutual Fund Categories for {_YEAR}",
            "Direct vs Regular Mutual Funds Explained",
            "How to Exit Mutual Funds Smartly",
        ],
    },
    2: {
        "category": "Personal Finance",
        "keywords": ["personal finance", "savings tips", "money management"],
        "subtopics": [
            "How to Build an Emergency Fund",
            "Smart Ways to Save Money Every Month",
            "Financial Mistakes to Avoid in Your 30s",
            "How to Achieve Financial Independence",
        ],
    },
    3: {
        "category": "Tax Planning",
        "keywords": ["income tax", "tax saving", "financial documents"],
        "subtopics": [
            "Section 80C Tax Saving Investments Explained",
            "How to File ITR for Salaried Employees",
            "HRA and Home Loan Tax Benefits",
            "New Tax Regime vs Old Tax Regime",
        ],
    },
    4: {
        "category": "Cryptocurrency",
        "keywords": ["cryptocurrency", "bitcoin", "blockchain technology"],
        "subtopics": [
            "Bitcoin Basics: What Every Indian Should Know",
            "How Blockchain Technology Works",
            f"Crypto Tax Rules in India {_YEAR}",
            f"Top Altcoins to Research in {_YEAR}",
        ],
    },
    5: {
        "category": "Real Estate",
        "keywords": ["real estate investment", "property", "housing market"],
        "subtopics": [
            "Rent vs Buy: What Makes Sense in India",
            "How to Invest in REITs in India",
            "Affordable Housing Investment Strategies",
            "How to Evaluate Property Value",
        ],
    },
    6: {
        "category": "Budgeting",
        "keywords": ["budgeting tips", "financial planning", "expense tracking"],
        "subtopics": [
            "The 50-30-20 Budgeting Rule for Indians",
            "Zero-Based Budgeting Explained Simply",
            "How to Track Every Rupee You Spend",
            "Building a 1-Year Financial Plan",
        ],
    },
}

# ─────────────────────────── YouTube Metadata ───────────────────────────
YOUTUBE_CATEGORY_ID = "22"
YOUTUBE_DEFAULT_LANGUAGE = "en"
YOUTUBE_PRIVACY_STATUS = "public"
CHANNEL_BRANDING = "Daksha Luma"

# ─────────────────────────── Pipeline Settings ───────────────────────────
MAX_HISTORY_LOOKBACK = 4
PEXELS_CLIPS_BUFFER_FACTOR = 1.3
PEXELS_MAX_RESULTS_PER_QUERY = 15
RETRY_MAX_ATTEMPTS = 3
RETRY_BACKOFF = 2.0
