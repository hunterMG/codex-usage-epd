"""Render the usage snapshot to a 400x300 three-colour dashboard and encode it
into the two bitplanes the EPD-nRF5 display expects.

Bitplane semantics mirror EPD-nRF5/html/js/dithering.js (threeColor):
  - BW plane : grayscale >= 140 -> bit=1 (white), else 0 (black)
  - RED plane: red if r > 160 and r > g and r > b -> bit=0 (red), else 1
  - byteWidth = ceil(width / 8), MSB-first per row, 8 pixels per byte.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, cast

from PIL import Image, ImageDraw, ImageFont

from .model import Balance

# dashboard geometry (matches 4.2" 400x300 BWR panel)
WIDTH = 400
HEIGHT = 300

# threeColor thresholds from dithering.js
BW_THRESHOLD = 140
RED_MIN_R = 160

BAR_H = 14
BAR_X = 100
MODEL_BAR_X = 12
MODEL_BAR_W = 220
MODEL_BAR_H = 10

# colours (pure red pixels render red on the BWR panel)
# GRAY must stay below BW_THRESHOLD in luma, otherwise it maps to white and
# becomes invisible on the panel (3-colour panels have no grey).
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (255, 0, 0)
GRAY = (100, 100, 100)

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",
    "/System/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


class FontNotFoundError(Exception):
    pass


def resolve_font_path(override: str = "") -> str:
    if override:
        p = override
        if not p.startswith("/") and not p.startswith("."):
            p = "/" + p
        from pathlib import Path

        if Path(p).exists():
            return p
        raise FontNotFoundError(f"configured font not found: {override}")
    from pathlib import Path

    for cand in FONT_CANDIDATES:
        if Path(cand).exists():
            return cand
    raise FontNotFoundError("no suitable font found; set render.font in config")


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return int(box[2] - box[0])


def _fmt_time(epoch: int | None) -> str:
    if not epoch:
        return "--.-- --:--"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone().strftime("%m.%d %H:%M")


def _draw_bar(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    remaining_percent: float,
    warn_threshold: float,
) -> None:
    draw.rectangle((x, y, x + w - 1, y + h - 1), fill=BLACK)
    draw.rectangle((x + 1, y + 1, x + w - 2, y + h - 2), fill=WHITE)
    filled = max(0, min(1.0, remaining_percent / 100.0))
    bar_w = int((w - 2) * filled)
    if bar_w > 0:
        colour = RED if remaining_percent <= warn_threshold else BLACK
        draw.rectangle((x + 1, y + 1, x + 1 + bar_w - 1, y + h - 2), fill=colour)


def _short_model(name: str) -> str:
    name = name.strip().split("/")[-1]
    name = re.sub(r"(?:-|\s)(?:\d{8}|\d{4}-\d{2}-\d{2})$", "", name)
    # name = name.upper()
    name = re.sub(r"^gpt-", "", name)
    return name or "UNKNOWN"


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if _text_w(draw, text, font) <= max_width:
        return text
    while text and _text_w(draw, text + "…", font) > max_width:
        text = text[:-1]
    return text + "…"


def _fmt_tokens(tokens: int) -> str:
    tokens = max(0, tokens)
    if tokens < 1_000_000:
        thousands = tokens / 1_000
        decimals = 3 if thousands < 1 else 2 if thousands < 10 else 1 if thousands < 100 else 0
        return f"{thousands:.{decimals}f}K"

    millions = tokens / 1_000_000
    decimals = 2 if millions < 10 else 1
    return f"{millions:.{decimals}f}M"


def render_dashboard(
    balance: Balance,
    font_path: str,
    warn_threshold: float = 20.0,
    width: int = WIDTH,
    height: int = HEIGHT,
) -> Image.Image:
    img = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(img)

    f_big = _font(font_path, 26)
    f_title = _font(font_path, 19)
    f_body = _font(font_path, 15)
    f_small = _font(font_path, 12)

    # header
    title = "Codex quota left"
    draw.text((12, 8), title, font=f_title, fill=BLACK)
    date_label = balance.fetched_at.strftime("%m.%d %a.")
    draw.text(
        (12 + _text_w(draw, title, f_title) + 55, 10),
        date_label,
        font=f_body,
        fill=BLACK,
    )
    plan = (balance.plan_type or "").upper()
    if plan:
        plan_w = _text_w(draw, plan, f_body)
        draw.text((width - 12 - plan_w, 10), plan, font=f_body, fill=BLACK)

    # global windows (5H / weekly)
    y = 44
    pct_w = _text_w(draw, "100%", f_body)
    bar_w = width - 12 - BAR_X - pct_w - 10
    for w in balance.windows:
        _draw_bar(draw, BAR_X, y, bar_w, BAR_H, w.remaining_percent, warn_threshold)
        if w.name == "weekly" or w.label == "WK":
            label = "Weekly"
            box = draw.textbbox((0, 0), label, font=f_body)
            label_y = y + (BAR_H - (box[3] - box[1])) // 2 - box[1]
            draw.text((12, label_y), label, font=f_body, fill=BLACK)
        else:
            draw.text((12, y - 3), w.label, font=f_big, fill=BLACK)
        pct = f"{w.remaining_percent:.0f}%"
        draw.text((width - 12 - _text_w(draw, pct, f_body), y - 1), pct, font=f_body, fill=BLACK)
        reset = _fmt_time(w.resets_at)
        reset_label = f"reset at  {reset}"
        if w.name == "weekly" or w.label == "WK":
            if w.reset_after_seconds is not None:
                hours_left = max(0, w.reset_after_seconds) // 3600
                days, hours = divmod(hours_left, 24)
                reset_label = f"Resets in {days}d {hours}h ({reset})."
            elif w.resets_at:
                reset_label = f"Resets at {reset}."
            else:
                reset_label = "Reset time unavailable"
        draw.text((BAR_X, y + BAR_H + 3), reset_label, font=f_small, fill=GRAY)
        y += 62

    # divider
    draw.line((12, y + 4, width - 12, y + 4), fill=BLACK, width=1)
    y += 14

    # Today's local token usage, sorted by model (top three). The largest
    # model is the reference width; the remaining bars are proportional to it.
    models = balance.models[:3]
    draw.text((12, y), "Tokens used today", font=f_small, fill=GRAY)
    y += 24
    max_tokens = max((m.tokens for m in models), default=0)
    for m in models:
        value = _fmt_tokens(m.tokens)
        value_w = _text_w(draw, value, f_body)
        label = _fit_text(draw, _short_model(m.id), f_body, MODEL_BAR_X + 82)
        row_bottom = y + MODEL_BAR_H
        if row_bottom > height - 26:
            break
        draw.text((12, y), label, font=f_body, fill=BLACK)
        draw.text((width - 12 - value_w, y), value, font=f_body, fill=BLACK)
        bar_x = MODEL_BAR_X + 86
        bar_y = y + 3
        if m.tokens >= max_tokens > 0:
            draw.rectangle(
                (bar_x, bar_y, bar_x + MODEL_BAR_W - 1, bar_y + MODEL_BAR_H - 1),
                fill=BLACK,
            )
        else:
            draw.rectangle(
                (bar_x, bar_y, bar_x + MODEL_BAR_W - 1, bar_y + MODEL_BAR_H - 1),
                outline=BLACK,
                width=1,
            )
            filled = int((MODEL_BAR_W - 2) * max(0.0, min(1.0, m.tokens / max_tokens))) if max_tokens else 0
            if filled:
                draw.rectangle(
                    (bar_x + 1, bar_y + 1, bar_x + filled, bar_y + MODEL_BAR_H - 2),
                    fill=BLACK,
                )
        y += 20
    if not models:
        draw.text((12, y), "No local usage today", font=f_body, fill=GRAY)

    # credits + footer
    credits_line = None
    if balance.has_credits:
        if balance.credits_unlimited:
            credits_line = "credits: unlimited"
        else:
            credits_line = f"credits: ${balance.credits_balance:.2f}"
    updated = f"updated {balance.fetched_at.strftime('%H:%M')}"
    footer = updated + (f"   |   {credits_line}" if credits_line else "")
    f_footer = _font(font_path, 11)
    draw.text((12, height - 18), footer, font=f_footer, fill=GRAY)

    return img


def image_to_planes(img: Image.Image) -> tuple[bytes, bytes]:
    """Return (bw_bytes, red_bytes). Mirrors threeColor() in dithering.js."""
    w, h = img.size
    byte_width = (w + 7) // 8
    bw = bytearray(byte_width * h)
    red = bytearray(byte_width * h)
    px = cast(Any, img.load())
    for yy in range(h):
        for xx in range(w):
            r, g, b = px[xx, yy][:3]
            gray = 0.299 * r + 0.587 * g + 0.114 * b
            idx = yy * byte_width + xx // 8
            bit = 7 - (xx % 8)
            if gray >= BW_THRESHOLD:
                bw[idx] |= 1 << bit
            else:
                bw[idx] &= ~(1 << bit)
            if r > RED_MIN_R and r > g and r > b:
                red[idx] &= ~(1 << bit)  # red pixel -> red bit 0
            else:
                red[idx] |= 1 << bit
    return bytes(bw), bytes(red)


def planes_to_rgb(bw: bytes, red: bytes, width: int, height: int) -> Image.Image:
    """Reference decode for self-tests (web decode loop)."""
    img = Image.new("RGB", (width, height), WHITE)
    px = img.load()
    byte_width = (width + 7) // 8
    px = cast(Any, px)
    for yy in range(height):
        for xx in range(width):
            idx = yy * byte_width + xx // 8
            bit = 7 - (xx % 8)
            red_bit = (red[idx] >> bit) & 1
            if red_bit == 0:
                px[xx, yy] = RED
            else:
                bw_bit = (bw[idx] >> bit) & 1
                px[xx, yy] = BLACK if bw_bit == 0 else WHITE
    return img
