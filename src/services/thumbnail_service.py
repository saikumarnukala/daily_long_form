"""
Thumbnail creation service — high-CTR YouTube finance style.

Workflow:
  1. Fetch a bold landscape photo from Pexels.
  2. Crop, boost contrast/saturation, vignette.
  3. Dark lower-third for readable title.
  4. Large title with gold + red hook words, category pill, accent bar.
"""
import io
import re
import textwrap
from pathlib import Path
from typing import List, Optional, Tuple

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

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
_ABSTRACT_SUFFIX = " money finance dramatic"

_ACCENT_GOLD = (255, 200, 0)
_ACCENT_RED = (255, 45, 45)
_ACCENT_WHITE = (255, 255, 255)

_HOOK_WORDS = {
    "why", "how", "never", "always", "secret", "truth",
    "instantly", "rich", "poor", "broke", "lose", "win", "money",
    "stop", "best", "worst", "simple", "dead", "zero", "free",
    "must", "million", "billion", "trap", "lie", "real", "shocking",
    "exposed", "hidden", "mistake", "mistakes", "avoid", "save",
    "debt", "sip", "tax", "profit", "loss", "crash", "invest",
}


@retry(max_attempts=3, backoff=2.0, exceptions=(requests.RequestException,))
def _fetch_pexels_image(keyword: str, used_ids: Optional[List[int]] = None) -> Optional[Image.Image]:
    used = set(used_ids or [])
    queries = [
        f"{keyword} {_ABSTRACT_SUFFIX}",
        f"{keyword} stock market chart",
        keyword,
    ]
    for query in queries:
        params = {
            "query": query,
            "per_page": 20,
            "orientation": "landscape",
            "size": "large",
        }
        resp = requests.get(PEXELS_PHOTO_API, headers=_HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        for photo in resp.json().get("photos", []):
            if photo.get("id") in used:
                continue
            src = photo.get("src", {})
            url = src.get("large2x") or src.get("large") or src.get("original")
            if not url:
                continue
            img_resp = requests.get(url, timeout=60)
            img_resp.raise_for_status()
            return Image.open(io.BytesIO(img_resp.content)).convert("RGB")
    return None


def _make_gradient_background() -> Image.Image:
    img = Image.new("RGB", (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT))
    draw = ImageDraw.Draw(img)
    for y in range(THUMBNAIL_HEIGHT):
        t = y / THUMBNAIL_HEIGHT
        r = int(12 + t * 18)
        g = int(18 + t * 22)
        b = int(55 + t * 35)
        draw.line([(0, y), (THUMBNAIL_WIDTH, y)], fill=(r, g, b))
    return img


def _boost_background(img: Image.Image) -> Image.Image:
    img = ImageEnhance.Contrast(img).enhance(1.22)
    img = ImageEnhance.Color(img).enhance(1.18)
    img = ImageEnhance.Brightness(img).enhance(1.05)
    return img


def _apply_vignette_and_overlay(img: Image.Image) -> Image.Image:
    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Edge vignette (fast line-based darkening)
    for y in range(h):
        edge = min(y, h - y) / (h * 0.35)
        edge = min(1.0, max(0.0, 1.0 - edge))
        alpha = int(55 * edge)
        if alpha:
            draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))

    # Strong lower-third for title
    for y in range(h):
        t = y / h
        if t < 0.38:
            alpha = int(25 * t / 0.38)
        else:
            alpha = int(25 + 228 * (t - 0.38) / 0.62)
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, min(alpha, 240)))

    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = (
        [
            "arialbd.ttf", "Arial_Bold.ttf", "DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
        if bold
        else [
            "arial.ttf", "DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> Tuple[int, int]:
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
    fill: tuple = _ACCENT_WHITE,
    outline: tuple = (0, 0, 0),
    outline_width: int = 6,
) -> None:
    x, y = xy
    for dx in range(-outline_width, outline_width + 1):
        for dy in range(-outline_width, outline_width + 1):
            if dx or dy:
                draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text((x, y), text, font=font, fill=fill)


def _hook_color(word: str) -> tuple:
    clean = re.sub(r"[^a-z0-9%]", "", word.lower())
    if re.search(r"\d", clean) or "%" in word:
        return _ACCENT_RED
    if clean in _HOOK_WORDS:
        return _ACCENT_GOLD
    return _ACCENT_WHITE


def _draw_mixed_line(
    draw: ImageDraw.ImageDraw,
    xy: tuple,
    text: str,
    font,
    outline_width: int = 7,
) -> None:
    x, y = xy
    space_w, _ = _text_size(draw, " ", font)
    for word in text.split():
        color = _hook_color(word)
        _draw_text_outlined(draw, (x, y), word, font, fill=color, outline_width=outline_width)
        ww, _ = _text_size(draw, word, font)
        x += ww + space_w


def _extract_stat_line(title: str) -> Tuple[Optional[str], str]:
    """Pull a leading number/stat for a huge CTR line (e.g. '80C', '10%')."""
    m = re.search(
        r"(\d+\s*%?|\d{1,3}(?:,\d{3})*\+?|\bRs\.?\s*\d[\d,]*|\$\d[\d,]*)",
        title,
        re.I,
    )
    if not m:
        return None, title
    stat = m.group(1).strip()
    rest = (title[: m.start()] + title[m.end() :]).strip(" -:|,")
    return stat, rest or title


def _draw_category_pill(draw: ImageDraw.ImageDraw, category: str) -> None:
    label = category.upper()[:22]
    font = _load_font(26, bold=True)
    pad_x, pad_y = 14, 8
    tw, th = _text_size(draw, label, font)
    x0, y0 = 42, 28
    x1, y1 = x0 + tw + pad_x * 2, y0 + th + pad_y * 2
    draw.rounded_rectangle([x0, y0, x1, y1], radius=10, fill=_ACCENT_RED)
    draw.text((x0 + pad_x, y0 + pad_y), label, font=font, fill=_ACCENT_WHITE)


def create_thumbnail(
    keyword: str,
    title: str,
    output_path: str,
    used_image_ids: Optional[List[int]] = None,
    category: Optional[str] = None,
) -> str:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    img: Optional[Image.Image] = None
    try:
        img = _fetch_pexels_image(keyword, used_ids=used_image_ids)
    except Exception as exc:
        logger.warning("Pexels fetch failed (%s). Using gradient fallback.", exc)

    if img is None:
        img = _make_gradient_background()
    else:
        img = ImageOps.fit(img, (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), Image.LANCZOS)
        img = _boost_background(img)
        img = img.filter(ImageFilter.GaussianBlur(radius=1.2))

    img = _apply_vignette_and_overlay(img)
    draw = ImageDraw.Draw(img)

    # Left accent bar (brand strip)
    draw.rectangle([0, 0, 14, THUMBNAIL_HEIGHT], fill=_ACCENT_GOLD)

    if category:
        _draw_category_pill(draw, category)

    stat, title_rest = _extract_stat_line(title)
    SEP_Y = int(THUMBNAIL_HEIGHT * 0.40)
    draw.rectangle([48, SEP_Y, THUMBNAIL_WIDTH - 48, SEP_Y + 5], fill=_ACCENT_GOLD)

    LEFT = 52
    y_cursor = SEP_Y + 22

    if stat:
        stat_font = _load_font(118, bold=True)
        _draw_text_outlined(draw, (LEFT, y_cursor), stat, stat_font, fill=_ACCENT_RED, outline_width=8)
        _, sh = _text_size(draw, stat, stat_font)
        y_cursor += sh + 8

    title_font = _load_font(82, bold=True)
    lines = textwrap.wrap(title_rest, width=18)
    if len(lines) > 3:
        lines = lines[:3]
        lines[-1] = lines[-1][:15] + "..."

    for line in lines:
        _draw_mixed_line(draw, (LEFT, y_cursor), line, title_font)
        _, lh = _text_size(draw, line, title_font)
        y_cursor += lh + 14

    # Channel + watch cue
    ch_font = _load_font(30, bold=True)
    cue_font = _load_font(24, bold=True)
    ch_text = CHANNEL_BRANDING.upper()
    cue = "WATCH NOW"
    ch_w, ch_h = _text_size(draw, ch_text, ch_font)
    cue_w, cue_h = _text_size(draw, cue, cue_font)
    draw.rounded_rectangle(
        [
            THUMBNAIL_WIDTH - cue_w - 36,
            THUMBNAIL_HEIGHT - max(ch_h, cue_h) - 44,
            THUMBNAIL_WIDTH - 22,
            THUMBNAIL_HEIGHT - 18,
        ],
        radius=8,
        fill=_ACCENT_RED,
    )
    draw.text(
        (THUMBNAIL_WIDTH - cue_w - 28, THUMBNAIL_HEIGHT - cue_h - 38),
        cue,
        font=cue_font,
        fill=_ACCENT_WHITE,
    )
    draw.text(
        (THUMBNAIL_WIDTH - ch_w - 28, THUMBNAIL_HEIGHT - ch_h - 14),
        ch_text,
        font=ch_font,
        fill=_ACCENT_GOLD,
    )

    img.save(output_path, "JPEG", quality=97, optimize=True)
    logger.info("Thumbnail saved -> %s", output_path)
    return output_path
