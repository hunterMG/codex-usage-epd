"""Render the usage snapshot to a 400x300 three-colour dashboard and encode it
into the two bitplanes the EPD-nRF5 display expects.

Bitplane semantics mirror EPD-nRF5/html/js/dithering.js (threeColor):
  - BW plane : grayscale >= 140 -> bit=1 (white), else 0 (black)
  - RED plane: red if r > 160 and r > g and r > b -> bit=0 (red), else 1
  - byteWidth = ceil(width / 8), MSB-first per row, 8 pixels per byte.
"""

from __future__ import annotations

from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from .model import Balance, Window

# dashboard geometry (matches 4.2" 400x300 BWR panel)
WIDTH = 400
HEIGHT = 300

# threeColor thresholds from dithering.js
BW_THRESHOLD = 140
RED_MIN_R = 160

BAR_H = 14
BAR_X = 100

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
    return box[2] - box[0]


def _fmt_time(epoch: int | None) -> str:
    if not epoch:
        return "--:--"
    return datetime.fromtimestamp(epoch).strftime("%H:%M")


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
    parts = name.split("-")
    for token in ("spark", "mini", "realtime", "lite"):
        if token in parts:
            idx = parts.index(token)
            keep = parts[idx - 1] if idx > 0 else name
            return keep.upper()
    if len(name) > 18:
        return name[:17]
    return name


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
    draw.text((12, 8), "CODEX USAGE", font=f_title, fill=BLACK)
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
        draw.text((12, y - 3), w.label, font=f_big, fill=BLACK)
        pct = f"{w.remaining_percent:.0f}%"
        draw.text((width - 12 - _text_w(draw, pct, f_body), y - 1), pct, font=f_body, fill=BLACK)
        reset = _fmt_time(w.resets_at)
        draw.text((BAR_X, y + BAR_H + 3), f"resets {reset}", font=f_small, fill=GRAY)
        y += 62

    # divider
    draw.line((12, y + 4, width - 12, y + 4), fill=BLACK, width=1)
    y += 14

    # per-model usage
    models = balance.models[:3]
    draw.text((12, y), "PER-MODEL", font=f_small, fill=GRAY)
    y += 24
    for m in models:
        label = _short_model(m.id)
        row_bottom = y + 30
        if row_bottom > height - 26:
            break
        draw.text((12, y), label, font=f_body, fill=BLACK)
        # two mini bars (5H + weekly) on the right, pct outside the bar
        sub_w = 176
        sub_h = 10
        sub_x = width - 12 - sub_w
        label_w = _text_w(draw, label, f_body)
        bar_x = max(sub_x, 12 + label_w + 16)
        for wi, w in enumerate(m.windows[:2]):
            by = y + 4 + wi * 13
            _draw_bar(draw, bar_x, by, sub_w, sub_h, w.remaining_percent, warn_threshold)
            pct = f"{w.remaining_percent:.0f}%"
            draw.text(
                (bar_x - _text_w(draw, pct, f_small) - 6, by - 1),
                pct,
                font=f_small,
                fill=BLACK,
            )
        y += 34

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
    px = img.load()
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