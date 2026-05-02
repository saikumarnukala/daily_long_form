"""
Central configuration module.
All settings are loaded from environment variables via python-dotenv.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

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
YOUTUBE_CLIENT_SECRET: str = os.getenv("YOUTUBE_CLIENT_SECRET", "")
YOUTUBE_REFRESH_TOKEN: str = os.getenv("YOUTUBE_REFRESH_TOKEN", "")

# ─────────────────────────── API Endpoints ───────────────────────────
PEXELS_VIDEO_API = "https://api.pexels.com/videos/search"
PEXELS_PHOTO_API = "https://api.pexels.com/v1/search"

# ─────────────────────────── TTS ───────────────────────────
TTS_VOICES = [
    "en-IN-NeerjaNeural",   # Female
    "en-IN-PrabhatNeural",  # Male
]
TTS_RATE = "+0%"
TTS_VOLUME = "+0%"
TTS_PITCH = "+0Hz"
TTS_WORDS_PER_MINUTE = 175  # approx. speed for timing calculations

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
# Keyed by weekday integer: 0 = Monday … 6 = Sunday
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
            "Best Mutual Fund Categories for 2025",
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
        "keywords": ["income tax India", "tax saving", "ITR filing"],
        "subtopics": [
            "Section 80C Tax Saving Investments Explained",
            "How to File ITR for Salaried Employees",
            "HRA and Home Loan Tax Benefits",
            "New Tax Regime vs Old Tax Regime",
        ],
    },
    4: {
        "category": "Cryptocurrency",
        "keywords": ["cryptocurrency India", "bitcoin", "blockchain technology"],
        "subtopics": [
            "Bitcoin Basics: What Every Indian Should Know",
            "How Blockchain Technology Works",
            "Crypto Tax Rules in India 2025",
            "Top Altcoins to Research in 2025",
        ],
    },
    5: {
        "category": "Real Estate",
        "keywords": ["real estate investment India", "property", "housing market"],
        "subtopics": [
            "Rent vs Buy: What Makes Sense in India",
            "How to Invest in REITs in India",
            "Affordable Housing Investment Strategies",
            "How to Evaluate Property Value",
        ],
    },
    6: {
        "category": "Budgeting",
        "keywords": ["budgeting tips India", "financial planning", "expense tracking"],
        "subtopics": [
            "The 50-30-20 Budgeting Rule for Indians",
            "Zero-Based Budgeting Explained Simply",
            "How to Track Every Rupee You Spend",
            "Building a 1-Year Financial Plan",
        ],
    },
}

# ─────────────────────────── YouTube Metadata ───────────────────────────
YOUTUBE_CATEGORY_ID = "22"        # People & Blogs
YOUTUBE_DEFAULT_LANGUAGE = "en"
YOUTUBE_PRIVACY_STATUS = "public"
CHANNEL_BRANDING = "Finance Decoded"

# ─────────────────────────── Pipeline Settings ───────────────────────────
MAX_HISTORY_LOOKBACK = 4           # avoid repeating a subtopic within 4 cycles
PEXELS_CLIPS_BUFFER_FACTOR = 1.3   # download 30% more clip duration than needed
PEXELS_MAX_RESULTS_PER_QUERY = 15  # results per Pexels API call
RETRY_MAX_ATTEMPTS = 3
RETRY_BACKOFF = 2.0
