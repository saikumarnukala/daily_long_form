# Finance Decoded — Daily YouTube Automation

A fully automated system that creates and uploads one long-form finance video to YouTube every day using only free tools.

---

## Architecture

```
GitHub Actions (cron: 2am UTC daily)
         │
         ▼
┌─────────────────────────────────────────────────────┐
│                  Pipeline Orchestrator               │
│                                                     │
│  Step 1 → Topic Selection   (weekday + history)     │
│  Step 2 → Script Generation (template-based)        │
│  Step 3 → TTS Audio         (Edge TTS, Indian EN)   │
│  Step 4 → B-roll Clips      (Pexels Videos API)     │
│  Step 5 → Thumbnail         (Pexels Photos + Pillow)│
│  Step 6 → Video Assembly    (MoviePy, 1080p MP4)    │
│  Step 7 → Metadata          (title, desc, tags)     │
│  Step 8 → YouTube Upload    (OAuth2 + Data API v3)  │
│  Step 9 → History Update    (data/history.json)     │
└─────────────────────────────────────────────────────┘
         │
         ▼
   Commit history.json back to repo
```

### 7-Day Content Schedule

| Day | Category |
|-----|----------|
| Monday | Stock Market Basics |
| Tuesday | Mutual Funds |
| Wednesday | Personal Finance |
| Thursday | Tax Planning |
| Friday | Cryptocurrency |
| Saturday | Real Estate |
| Sunday | Budgeting |

---

## Tech Stack

| Component | Tool | Cost |
|-----------|------|------|
| TTS | Edge TTS (`en-IN-NeerjaNeural` / `en-IN-PrabhatNeural`) | Free |
| B-roll video | Pexels Videos API | Free |
| Thumbnail image | Pexels Photos API | Free |
| Video processing | MoviePy + FFmpeg | Free |
| Image processing | Pillow | Free |
| CI/CD | GitHub Actions | Free (2000 min/month) |
| Upload | YouTube Data API v3 | Free |

---

## Repository Structure

```
long_videos_daily/
├── .github/
│   └── workflows/
│       └── daily_pipeline.yml    ← Cron job (2am UTC daily)
├── src/
│   ├── main.py                   ← Entry point
│   ├── config.py                 ← All settings + topic schedule
│   ├── pipeline/
│   │   ├── orchestrator.py       ← Step sequencer
│   │   └── scheduler.py          ← Local cron alternative
│   ├── services/
│   │   ├── topic_service.py      ← Day → topic + subtopic
│   │   ├── script_service.py     ← 7 finance script templates
│   │   ├── tts_service.py        ← Edge TTS async narration
│   │   ├── media_service.py      ← Pexels video fetch + download
│   │   ├── thumbnail_service.py  ← Pexels photo + Pillow overlay
│   │   ├── video_service.py      ← MoviePy assembly
│   │   └── youtube_service.py    ← OAuth2 upload
│   └── utils/
│       ├── logger.py             ← Rotating file + console logger
│       ├── retry.py              ← Exponential backoff decorator
│       └── file_manager.py       ← Path helpers
├── assets/
│   ├── temp/                     ← Runtime downloads (gitignored)
│   ├── output/                   ← Finished MP4s (gitignored)
│   └── bgmusic.mp3               ← Optional background music (user-supplied)
├── data/
│   └── history.json              ← Deduplication store (committed)
├── scripts/
│   └── get_youtube_token.py      ← One-time OAuth2 token helper
├── requirements.txt
├── .env.example
└── README.md
```

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/long_videos_daily.git
cd long_videos_daily
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Get a Pexels API Key

1. Go to [https://www.pexels.com/api/](https://www.pexels.com/api/)
2. Create a free account → click **"Your API Key"**
3. Copy the key

### 3. Set up YouTube API

#### 3a. Enable YouTube Data API

1. Go to [https://console.cloud.google.com/](https://console.cloud.google.com/)
2. Create a new project (e.g. `finance-decoded`)
3. Go to **APIs & Services → Library**
4. Search for **"YouTube Data API v3"** → Enable it
5. Go to **APIs & Services → OAuth consent screen**
   - User type: **External**
   - Fill in app name (`Finance Decoded`), your email
   - Scopes: add `youtube.upload` and `youtube`
   - Add your Google account as a test user
6. Go to **Credentials → Create Credentials → OAuth 2.0 Client ID**
   - Application type: **Desktop app**
   - Download the JSON file

#### 3b. Get your refresh token (one-time, local)

```bash
# Place the downloaded JSON as client_secret.json in project root
python scripts/get_youtube_token.py
```

A browser window opens. Log in with the YouTube channel account. Copy the printed `YOUTUBE_REFRESH_TOKEN` value.

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```env
PEXELS_API_KEY=your_pexels_api_key
YOUTUBE_CLIENT_SECRET={"installed":{"client_id":"...","client_secret":"...",...}}
YOUTUBE_REFRESH_TOKEN=your_refresh_token
```

> **YOUTUBE_CLIENT_SECRET** — paste the *entire contents* of `client_secret.json` as a single line (no newlines).

### 5. (Optional) Add background music

Place a royalty-free MP3 at `assets/bgmusic.mp3`. It will be mixed at −18 dB under the narration. Good sources:
- [Pixabay Audio](https://pixabay.com/music/) — free, no attribution required
- [Free Music Archive](https://freemusicarchive.org/)

If the file is absent, videos are exported with narration only.

---

## Running Locally

```bash
# Full pipeline run
python src/main.py

# Skip YouTube upload (for testing)
python src/main.py --dry-run

# Override today's subtopic
python src/main.py --dry-run --topic-override "How to Build a ₹1 Crore Portfolio"

# Clean temp files after run
python src/main.py --cleanup-temp

# Run on a local daily schedule (alternative to GitHub Actions)
python -m src.pipeline.scheduler --time 07:30
python -m src.pipeline.scheduler --time 07:30 --run-now
```

---

## GitHub Actions Setup

### Add Secrets

In your GitHub repository → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret Name | Value |
|-------------|-------|
| `PEXELS_API_KEY` | Your Pexels API key |
| `YOUTUBE_CLIENT_SECRET` | Full contents of `client_secret.json` (one line) |
| `YOUTUBE_REFRESH_TOKEN` | Value from `get_youtube_token.py` output |

### Workflow Trigger

The pipeline runs automatically at **2:00 AM UTC (7:30 AM IST)** every day.

To trigger manually:
1. Go to **Actions** tab in your GitHub repo
2. Select **"Daily YouTube Video Pipeline"**
3. Click **"Run workflow"**
4. Optionally set **dry_run = true** to test without uploading

### What the workflow does

1. Checks out the repo
2. Installs Python 3.10 + dependencies (pip cached for speed)
3. Installs system packages: `ffmpeg`, `imagemagick`, `fonts-dejavu-core`
4. Runs `python src/main.py --cleanup-temp`
5. Uploads the finished MP4 as a GitHub Actions artifact (7-day retention)
6. Commits updated `data/history.json` back to the repo

---

## How the Pipeline Works

### Topic Selection
- Detects the current weekday (Monday=0, Sunday=6)
- Maps it to one of 7 finance categories
- Each category has 4 subtopics that rotate; the last 4 uses are checked to avoid repetition
- History is stored in `data/history.json`

### Script Generation
- No external API or LLM is used — scripts are generated from rich, parameterized templates
- Each template follows: **Hook → Problem → Core Explanation → ₹ Indian Example → CTA**
- Target: ~1800–2200 words ≈ 9–11 minutes of narration

### TTS (Text-to-Speech)
- Uses `edge-tts` (Microsoft Edge TTS — completely free)
- Alternates between `en-IN-NeerjaNeural` (female) and `en-IN-PrabhatNeural` (male) by day parity
- Falls back to the other voice on failure

### Media (B-roll)
- Searches Pexels Videos API using category-specific keywords
- Downloads HD clips (≥1280px wide) until total footage ≥ audio duration × 1.3
- Skips previously-used Pexels video IDs (tracked in `history.json`)

### Thumbnail
- Fetches a landscape photo from Pexels Photos API
- Resizes to 1280×720 with Pillow
- Applies a dark gradient overlay on the lower half
- Renders bold white title text with drop shadow
- Adds a red "FINANCE DECODED" channel badge (top-left)
- Falls back to a programmatic gradient background if Pexels fails

### Video Assembly (MoviePy)
- Concatenates B-roll clips, looping if needed, cropped to 1920×1080
- Attaches narration audio (+ optional background music at −18 dB)
- Adds section-label text overlays timed to word-count proportions
- 0.5 s crossfade transitions between clips; 1 s fade-in/out
- Exports: libx264 / AAC / 8 Mbps / 30 fps

### YouTube Upload
- Authenticates via OAuth2 refresh token (no browser in CI)
- Resumable upload with 10 MB chunks
- Sets title, description with chapter timestamps, tags, categoryId, privacyStatus=public
- Uploads custom thumbnail after video is live

---

## Customisation

### Change upload time
Edit `.github/workflows/daily_pipeline.yml`:
```yaml
schedule:
  - cron: "0 2 * * *"   # Change 2 to any UTC hour
```

### Change privacy status
Edit `src/config.py`:
```python
YOUTUBE_PRIVACY_STATUS = "private"   # or "unlisted"
```

### Change video resolution / quality
Edit `src/config.py`:
```python
VIDEO_BITRATE = "12000k"   # Higher = better quality, larger file
VIDEO_FPS = 24
```

### Add more subtopics
Edit the `TOPIC_SCHEDULE` dict in `src/config.py` — each category accepts any number of subtopics in its list.

### Modify script templates
Edit `src/services/script_service.py`. Each `_template_*` function returns a `sections` dict with keys: `hook`, `problem`, `explanation`, `example`, `cta`.

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `PEXELS_API_KEY not set` | Add key to `.env` or GitHub Secrets |
| `YOUTUBE_REFRESH_TOKEN not set` | Run `python scripts/get_youtube_token.py` |
| `edge-tts connection error` | Check internet; some ISPs block IPv6. Try setting `EDGETTS_TRUST_ENV=1` |
| `MoviePy TextClip error` | Ensure ImageMagick is installed: `sudo apt-get install imagemagick` |
| `No clips downloaded` | Check `PEXELS_API_KEY` and Pexels quota (25,000 req/month free) |
| `Token expired` | Re-run `get_youtube_token.py` and update `YOUTUBE_REFRESH_TOKEN` secret |
| Video export takes too long | Reduce `VIDEO_BITRATE` in `config.py` or upgrade GitHub Actions runner |

---

## Logs

Logs are written to `logs/pipeline.log` (rotating, 5 MB max, 3 backup files).

In GitHub Actions, logs are visible in the workflow run console output.

---

## License

MIT — free to use, modify, and distribute.
