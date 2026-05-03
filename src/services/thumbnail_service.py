"""
Thumbnail creation service.

Workflow:
  1. Fetch an abstract/concept landscape photo from Pexels (no people).
  2. Smart-crop to 1280×720 using ImageOps.fit (centered, no stretch/headless).
  3. Apply a cinematic dark overlay for contrast.
  4. Render a bold, large-font centered title (max 3 lines) with thick outline.
  5. Left accent bar + channel badge bottom bar.
  6. Save as high-quality JPEG (quality=95).

Design: modern YouTube finance style — dramatic, clean, high-contrast.
"""
import io
import os
import re
import textwrap
from pathlib import Path
from typing import List, Optional

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from src.config import (
    CHANNEL_BRANDING,
    PEXELS_API_KEY,
    PEXELS_PHOTO_API,
    THUMBNAIL_HEIGHT,
    THUMBNAIL_WIDTH,
)
from src.utils.logger import get_logger
from src.utils.retry import retry

logger = get_logger(__name__)

_HEADERS = {"Authorization": PEXELS_API_KEY}

# Appending this to Pexels queries steers results away from people/portraits
_ABSTRACT_SUFFIX = " finance abstract concept"

# Accent color: electric gold (stands out on dark backgrounds)
_ACCENT = (255, 185, 0)

# Words that get highlighted in gold for emotional impact
_HOOK_WORDS = {
    "why", "how", "never", "always", "secret", "truth",
    "instantly", "rich", "poor", "broke", "lose", "win",
    "stop", "best", "worst", "simple", "dead", "zero",
    "must", "million", "billion", "free", "trap", "lie",
    "real", "shocking", "exposed", "hidden",
}

# ─────────────────────────── Pexels photo fetch ───────────────────────────

@retry(max_attempts=3, backoff=2.0, exceptions=(requests.RequestException,))
def _fetch_pexels_image(keyword: str, used_ids: Optional[List[int]] = None) -> Optional[Image.Image]:
    """
    Search Pexels for a concept/abstract landscape photo matching *keyword*.
    Returns a PIL Image, or None if nothing suitable found (triggers fallback).
    """
    used = set(used_ids or [])
    for query in [keyword + _ABSTRACT_SUFFIX, keyword]:
        params = {
            "query": query,
            "per_page": 15,
            "orientation": "landscape",
            "size": "large",
        }
        resp = requests.get(PEXELS_PHOTO_API, headers=_HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        photos = resp.json().get("photos", [])
        for photo in photos:
            if photo.get("id") in used:
                continue
            src = photo.get("src", {})
            url = src.get("large2x") or src.get("large") or src.get("original")
            if not url:
                continue
            img_resp = requests.get(url, timeout=60)
            img_resp.raise_for_status()
            img = Image.open(io.BytesIO(img_resp.content)).convert("RGB")
            logger.debug("Pexels photo fetched (id=%s, size=%s)", photo["id"], img.size)
            return img
    return None


# ─────────────────────────── Drawing helpers ───────────────────────────

def _make_gradient_background() -> Image.Image:
    """Generate a dark navy-to-black gradient as fallback background."""
    img = Image.new("RGB", (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT))
    draw = ImageDraw.Draw(img)
    for y in range(THUMBNAIL_HEIGHT):
        t = y / THUMBNAIL_HEIGHT
        r = int(5 + t * 8)
        g = int(10 + t * 12)
        b = int(60 + t * 20)
        draw.line([(0, y), (THUMBNAIL_WIDTH, y)], fill=(r, g, b))
    return img


def _apply_cinematic_overlay(img: Image.Image) -> Image.Image:
    """
    Dramatic two-zone overlay:
      - Top 45%: almost transparent — photo stays vivid and eye-catching.
      - Bottom 55%: aggressive dark fade to near-black — title is always readable.
    """
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    h = img.height
    for y in range(h):
        t = y / h
        if t < 0.45:
            alpha = int(35 * t / 0.45)                          # 0 → 35
        else:
            alpha = int(35 + 218 * (t - 0.45) / 0.55)          # 35 → 253
        draw.line([(0, y), (img.width, y)], fill=(0, 0, 0, min(alpha, 253)))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load a TrueType font; falls back to Pillow default if none found."""
    candidates = []
    if bold:
        candidates = [
            "arialbd.ttf", "Arial_Bold.ttf", "DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
    else:
        candidates = [
            "arial.ttf", "Arial.ttf", "DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    logger.warning("No TrueType font found; using Pillow default bitmap font.")
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple:
    """Return (width, height) — compatible with old and new Pillow."""
    try:
        bb = draw.textbbox((0, 0), text, font=font)
        return bb[2] - bb[0], bb[3] - bb[1]
    except AttributeError:
        return draw.textsize(text, font=font)


def _draw_text_outlined(
    draw: ImageDraw.ImageDraw,
    xy: tuple,
    text: str,
    font,
    fill: tuple = (255, 255, 255),
    outline: tuple = (0, 0, 0),
    outline_width: int = 5,
) -> None:
    """Draw text with a thick outline — legible on any background."""
    x, y = xy
    for dx in range(-outline_width, outline_width + 1):
        for dy in range(-outline_width, outline_width + 1):
            if dx != 0 or dy != 0:
                draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text((x, y), text, font=font, fill=fill)


def _draw_mixed_line(
    draw: ImageDraw.ImageDraw,
    xy: tuple,
    text: str,
    font,
    fill_normal: tuple = (255, 255, 255),
    fill_hook: tuple = _ACCENT,
    outline: tuple = (0, 0, 0),
    outline_width: int = 6,
) -> None:
    """
    Render a line of text word-by-word.  Words that are numbers/percentages
    or match _HOOK_WORDS are rendered in the gold accent color; all others
    are rendered in white.  Each word gets a thick black outline.
    """
    x, y = xy
    space_w, _ = _text_size(draw, " ", font)
    for word in text.split():
        clean = re.sub(r"[^a-z0-9]", "", word.lower())
        is_hook = bool(re.search(r"\d", clean)) or clean in _HOOK_WORDS
        color = fill_hook if is_hook else fill_normal
        _draw_text_outlined(draw, (x, y), word, font,
                            fill=color, outline=outline, outline_width=outline_width)
        ww, _ = _text_size(draw, word, font)
        x += ww + space_w


# ─────────────────────────── Public API ───────────────────────────

def create_thumbnail(
    keyword: str,
    title: str,
    output_path: str,
    used_image_ids: Optional[List[int]] = None,
) -> str:
    """
    Create a YouTube thumbnail at *output_path*.

    Args:
        keyword:         Pexels search term (e.g. "stock market").
        title:           Video title text to overlay on the image.
        output_path:     Destination path for the JPEG thumbnail.
        used_image_ids:  Pexels photo IDs already used (to avoid duplicates).

    Returns:
        Absolute path of the saved thumbnail.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # ── 1. Background ────────────────────────────────────────────────────
    img: Optional[Image.Image] = None
    try:
        img = _fetch_pexels_image(keyword, used_ids=used_image_ids)
    except Exception as exc:
        logger.warning("Pexels fetch failed (%s). Using gradient fallback.", exc)

    if img is None:
        logger.info("Using gradient fallback background for thumbnail.")
        img = _make_gradient_background()
    else:
        # Smart center-crop — no headless people, no stretching
        img = ImageOps.fit(img, (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), Image.LANCZOS)
        # Subtle blur so text always pops off the background
        img = img.filter(ImageFilter.GaussianBlur(radius=1.8))

    # ── 2. Dramatic two-zone cinematic overlay ───────────────────────────
    img = _apply_cinematic_overlay(img)
    draw = ImageDraw.Draw(img)

    # ── 3. Thin gold separator line (marks where title zone begins) ──────
    SEP_Y = int(THUMBNAIL_HEIGHT * 0.43)
    draw.rectangle([50, SEP_Y, THUMBNAIL_WIDTH - 50, SEP_Y + 4], fill=_ACCENT)

    # ── 4. Title block — left-aligned, hook words in gold ────────────────
    title_font = _load_font(90, bold=True)
    MAX_CHARS = 20
    lines = textwrap.wrap(title, width=MAX_CHARS)
    if len(lines) > 3:
        lines = lines[:3]
        lines[-1] = lines[-1].rstrip(" ,.:;")[:MAX_CHARS - 3] + "..."

    LINE_H = 108
    LEFT = 56
    BOTTOM_CLEAR = 58      # room for channel name at very bottom
    total_text_h = len(lines) * LINE_H
    title_zone_top = SEP_Y + 18
    title_zone_h = THUMBNAIL_HEIGHT - title_zone_top - BOTTOM_CLEAR
    start_y = title_zone_top + max(0, (title_zone_h - total_text_h) // 2)

    for i, line in enumerate(lines):
        cy = start_y + i * LINE_H
        _draw_mixed_line(draw, (LEFT, cy), line, title_font)

    # ── 5. Channel name — bottom-right, minimal & elegant ────────────────
    ch_font = _load_font(27, bold=True)
    ch_text = CHANNEL_BRANDING.upper()
    ch_w, ch_h = _text_size(draw, ch_text, ch_font)
    draw.text(
        (THUMBNAIL_WIDTH - ch_w - 28, THUMBNAIL_HEIGHT - ch_h - 16),
        ch_text, font=ch_font, fill=_ACCENT,
    )

    # ── 6. Save ───────────────────────────────────────────────────────────
    img.save(output_path, "JPEG", quality=96, optimize=True)
    logger.info("Thumbnail saved → %s", output_path)
    return output_path
