"""
Thumbnail creation service.

Workflow:
  1. Fetch a landscape photograph from Pexels Photos API.
  2. Resize to 1280×720.
  3. Apply a semi-transparent dark gradient overlay (bottom 50%).
  4. Render bold white title text with drop-shadow (Pillow ImageDraw).
  5. Add a channel badge ("Finance Decoded") in the top-left corner.
  6. Save as high-quality JPEG (quality=95).

Fallback: If Pexels fetch fails, generate a solid gradient background so the
          pipeline never blocks on a network error.
"""
import io
import os
import textwrap
from pathlib import Path
from typing import List, Optional

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

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

# ─────────────────────────── Pexels photo fetch ───────────────────────────

@retry(max_attempts=3, backoff=2.0, exceptions=(requests.RequestException,))
def _fetch_pexels_image(keyword: str, used_ids: Optional[List[int]] = None) -> Optional[Image.Image]:
    """
    Search Pexels for a landscape photo matching *keyword* and return a PIL Image.
    Returns None if no suitable photo is found (triggers fallback).
    """
    params = {
        "query": keyword,
        "per_page": 15,
        "orientation": "landscape",
    }
    resp = requests.get(PEXELS_PHOTO_API, headers=_HEADERS, params=params, timeout=30)
    resp.raise_for_status()
    photos = resp.json().get("photos", [])

    used = set(used_ids or [])
    for photo in photos:
        if photo.get("id") in used:
            continue
        # Prefer the "large2x" or "large" src for quality
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
    """Generate a dark blue-to-black gradient as fallback background."""
    img = Image.new("RGB", (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT))
    draw = ImageDraw.Draw(img)
    for y in range(THUMBNAIL_HEIGHT):
        ratio = y / THUMBNAIL_HEIGHT
        r = int(10 + ratio * 5)
        g = int(20 + ratio * 10)
        b = int(80 - ratio * 60)
        draw.line([(0, y), (THUMBNAIL_WIDTH, y)], fill=(r, g, b))
    return img


def _apply_gradient_overlay(img: Image.Image) -> Image.Image:
    """
    Apply a cinematic dark gradient: subtle top vignette + heavy bottom darkening.
    This ensures title text is always legible regardless of background photo.
    """
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    # Top vignette (subtle)
    for y in range(0, int(img.height * 0.25)):
        alpha = int(80 * (1 - y / (img.height * 0.25)))
        draw.line([(0, y), (img.width, y)], fill=(0, 0, 0, alpha))
    # Bottom gradient (heavy — covers bottom 65% for text readability)
    overlay_start = int(img.height * 0.35)
    for y in range(overlay_start, img.height):
        alpha = int(220 * (y - overlay_start) / (img.height - overlay_start))
        draw.line([(0, y), (img.width, y)], fill=(0, 0, 0, alpha))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """
    Attempt to load a bold/regular system font.  Falls back gracefully to
    Pillow's built-in bitmap font if no TrueType fonts are available.
    """
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
    # Final fallback — Pillow's default bitmap font (no size scaling)
    logger.warning("No TrueType font found; using Pillow default bitmap font.")
    return ImageFont.load_default()


def _draw_text_with_shadow(
    draw: ImageDraw.ImageDraw,
    position: tuple,
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple = (255, 255, 255),
    shadow_color: tuple = (0, 0, 0),
    shadow_offset: int = 3,
) -> None:
    """Draw text with a drop shadow for legibility."""
    sx, sy = position[0] + shadow_offset, position[1] + shadow_offset
    draw.text((sx, sy), text, font=font, fill=shadow_color)
    draw.text(position, text, font=font, fill=fill)


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

    # 1. Get background image
    img: Optional[Image.Image] = None
    try:
        img = _fetch_pexels_image(keyword, used_ids=used_image_ids)
    except Exception as exc:
        logger.warning("Pexels photo fetch failed (%s). Using gradient fallback.", exc)

    if img is None:
        logger.info("Using gradient fallback background for thumbnail.")
        img = _make_gradient_background()
    else:
        img = img.resize((THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), Image.LANCZOS)

    # 2. Apply dark gradient overlay
    img = _apply_gradient_overlay(img)

    draw = ImageDraw.Draw(img)

    # ── 3. Topic keyword badge (top-left, dark navy + gold text) ──────────
    badge_font = _load_font(22, bold=True)
    badge_text = keyword.upper()[:22]
    badge_padding = 10
    badge_x, badge_y = 24, 24
    try:
        bbox = draw.textbbox((badge_x, badge_y), badge_text, font=badge_font)
        bx1, by1, bx2, by2 = bbox
    except AttributeError:
        bw, bh = draw.textsize(badge_text, font=badge_font)
        bx1, by1, bx2, by2 = badge_x, badge_y, badge_x + bw, badge_y + bh
    draw.rectangle(
        [bx1 - badge_padding, by1 - badge_padding // 2,
         bx2 + badge_padding, by2 + badge_padding // 2],
        fill=(13, 33, 55),  # dark navy
    )
    draw.text((badge_x, badge_y), badge_text, font=badge_font, fill=(255, 179, 0))  # gold

    # ── 4. Main title text (large, centered in lower 60%) ─────────────────
    BOTTOM_BAR_HEIGHT = 88
    title_font = _load_font(72, bold=True)
    max_chars_per_line = 26
    wrapped_lines = textwrap.wrap(title, width=max_chars_per_line)
    if len(wrapped_lines) > 3:
        wrapped_lines = wrapped_lines[:3]
        wrapped_lines[-1] = wrapped_lines[-1][:-3] + "..."

    line_height = 84
    total_text_height = len(wrapped_lines) * line_height
    # Leave space for bottom bar + gold accent line
    start_y = THUMBNAIL_HEIGHT - total_text_height - BOTTOM_BAR_HEIGHT - 28

    for i, line in enumerate(wrapped_lines):
        y = start_y + i * line_height
        _draw_text_with_shadow(draw, (40, y), line, font=title_font, shadow_offset=4)

    # ── 5. Gold accent divider line above bottom bar ───────────────────────
    accent_y = THUMBNAIL_HEIGHT - BOTTOM_BAR_HEIGHT - 3
    draw.rectangle([0, accent_y, THUMBNAIL_WIDTH, accent_y + 5], fill=(255, 179, 0))

    # ── 6. Bottom channel branding bar (semi-transparent) ─────────────────
    bar_overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    bar_draw = ImageDraw.Draw(bar_overlay)
    bar_draw.rectangle(
        [0, THUMBNAIL_HEIGHT - BOTTOM_BAR_HEIGHT, THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT],
        fill=(5, 5, 15, 218),
    )
    img = Image.alpha_composite(img.convert("RGBA"), bar_overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    # Channel name in gold (left side of bar)
    ch_font = _load_font(40, bold=True)
    ch_text = CHANNEL_BRANDING.upper()
    ch_y = THUMBNAIL_HEIGHT - BOTTOM_BAR_HEIGHT + (BOTTOM_BAR_HEIGHT - 40) // 2
    _draw_text_with_shadow(
        draw, (40, ch_y), ch_text,
        font=ch_font, fill=(255, 179, 0), shadow_color=(0, 0, 0), shadow_offset=2,
    )

    # "New video every day" tagline (right side, subtle)
    tag_font = _load_font(24, bold=False)
    tag_text = "New video every day"
    try:
        tag_bbox = draw.textbbox((0, 0), tag_text, font=tag_font)
        tag_w = tag_bbox[2] - tag_bbox[0]
    except AttributeError:
        tag_w, _ = draw.textsize(tag_text, font=tag_font)
    tag_x = THUMBNAIL_WIDTH - tag_w - 36
    tag_y = ch_y + 8
    draw.text((tag_x, tag_y), tag_text, font=tag_font, fill=(190, 190, 190))

    # ── 7. Save ────────────────────────────────────────────────────────────
    img.save(output_path, "JPEG", quality=95, optimize=True)
    logger.info("Thumbnail saved → %s", output_path)
    return output_path
