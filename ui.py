#!/usr/bin/env python3
"""
ui.py - design system for the Vault app: palette, fonts, hand-drawn vector
icons (no emoji), avatars, and styled buttons. Kept separate so app.py stays
about behaviour and this stays about looks.
"""
from __future__ import annotations

import hashlib
import math
from functools import lru_cache
from pathlib import Path

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont

# --------------------------------------------------------------------------- #
# Palette — layered graphite + electric blue / teal
# --------------------------------------------------------------------------- #
BG0 = "#0a0e15"   # app background
BG1 = "#0e1420"   # sidebar
BG2 = "#131b28"   # cards
BG3 = "#1a2433"   # hover / inputs
BG4 = "#212e40"   # elevated / pressed
LINE = "#1f2a3a"  # hairline
LINE2 = "#2c3b50"  # brighter border / focus
ACCENT = "#4f8cff"
ACCENT_HI = "#74a4ff"
ACCENT_LO = "#3a72e0"
TEAL = "#2dd4bf"
GOOD = "#34d399"
WARN = "#f87171"
TEXT = "#e9eef6"
MUTED = "#93a2b8"
FAINT = "#5f6f84"
INK = "#06101f"   # text on bright buttons

FONT = "Segoe UI"
MONO = "Consolas"


def font(size=13, weight="normal"):
    return ctk.CTkFont(family=FONT, size=size, weight=weight)


def mono(size=13, weight="normal"):
    return ctk.CTkFont(family=MONO, size=size, weight=weight)


# --------------------------------------------------------------------------- #
# Vector icon factory (anti-aliased line icons, drawn at 4x then downscaled)
# --------------------------------------------------------------------------- #
SS = 4  # supersample factor


def _rline(d, pts, w, fill):
    d.line(pts, fill=fill, width=w, joint="curve")
    r = w / 2
    for x, y in (pts[0], pts[-1]):
        d.ellipse((x - r, y - r, x + r, y + r), fill=fill)


@lru_cache(maxsize=256)
def _icon_image(name: str, size: int, color: str, stroke: float) -> Image.Image:
    S = size * SS
    sw = max(1, round(size * stroke)) * SS
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    def P(fx, fy):
        return (fx * S, fy * S)

    if name == "gamepad":
        d.rounded_rectangle([P(.1, .34), P(.9, .72)], radius=.19 * S, outline=color, width=sw)
        _rline(d, [P(.24, .53), P(.36, .53)], sw, color)       # d-pad h
        _rline(d, [P(.30, .47), P(.30, .59)], sw, color)       # d-pad v
        r = .045 * S
        for cx, cy in ((.66, .46), (.76, .56)):                # buttons
            d.ellipse([cx * S - r, cy * S - r, cx * S + r, cy * S + r], fill=color)
    elif name == "key":
        R = .17 * S
        cx, cy = .35 * S, .35 * S
        d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=color, width=sw)
        _rline(d, [P(.45, .45), P(.82, .82)], sw, color)       # shaft
        _rline(d, [P(.66, .70), P(.74, .62)], sw, color)       # tooth 1
        _rline(d, [P(.78, .82), P(.86, .74)], sw, color)       # tooth 2
    elif name == "lock":
        d.rounded_rectangle([P(.26, .46), P(.74, .84)], radius=.09 * S, outline=color, width=sw)
        d.arc([P(.33, .26), P(.67, .62)], 180, 360, fill=color, width=sw)   # shackle
        d.ellipse([P(.47, .6), P(.53, .66)], fill=color)                     # keyhole
    elif name == "unlock":
        d.rounded_rectangle([P(.26, .46), P(.74, .84)], radius=.09 * S, outline=color, width=sw)
        d.arc([P(.33, .26), P(.67, .62)], 210, 360, fill=color, width=sw)
        d.ellipse([P(.47, .6), P(.53, .66)], fill=color)
    elif name == "plus":
        _rline(d, [P(.5, .28), P(.5, .72)], sw, color)
        _rline(d, [P(.28, .5), P(.72, .5)], sw, color)
    elif name == "search":
        R = .24 * S
        cx, cy = .44 * S, .44 * S
        d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=color, width=sw)
        _rline(d, [P(.62, .62), P(.82, .82)], sw, color)
    elif name == "copy":
        d.rounded_rectangle([P(.36, .22), P(.8, .64)], radius=.06 * S, outline=color, width=sw)
        d.rounded_rectangle([P(.2, .38), P(.64, .8)], radius=.06 * S, outline=color, width=sw)
    elif name == "user":
        R = .15 * S
        d.ellipse([.5 * S - R, .36 * S - R, .5 * S + R, .36 * S + R], outline=color, width=sw)
        d.arc([P(.27, .58), P(.73, 1.02)], 180, 360, fill=color, width=sw)
    elif name == "chevron":
        _rline(d, [P(.44, .3), P(.62, .5), P(.44, .7)], sw, color)
    elif name == "trash":
        _rline(d, [P(.26, .32), P(.74, .32)], sw, color)
        _rline(d, [P(.42, .32), P(.42, .25), P(.58, .25), P(.58, .32)], sw, color)
        _rline(d, [P(.31, .36), P(.35, .8), P(.65, .8), P(.69, .36)], sw, color)
    elif name == "download":
        _rline(d, [P(.5, .24), P(.5, .6)], sw, color)
        _rline(d, [P(.37, .48), P(.5, .62), P(.63, .48)], sw, color)
        _rline(d, [P(.28, .78), P(.72, .78)], sw, color)
    elif name == "shield":
        _rline(d, [P(.5, .2), P(.8, .32), P(.8, .56), P(.5, .82),
                   P(.2, .56), P(.2, .32), P(.5, .2)], sw, color)
    elif name == "dot":
        d.ellipse([P(.3, .3), P(.7, .7)], fill=color)
    else:
        d.rounded_rectangle([P(.2, .2), P(.8, .8)], radius=.1 * S, outline=color, width=sw)

    return im.resize((size, size), Image.LANCZOS)


@lru_cache(maxsize=256)
def icon(name: str, size: int = 20, color: str = TEXT, stroke: float = 0.085) -> ctk.CTkImage:
    im = _icon_image(name, size, color, stroke)
    return ctk.CTkImage(light_image=im, dark_image=im, size=(size, size))


# --------------------------------------------------------------------------- #
# Brand mark + avatars
# --------------------------------------------------------------------------- #
def _gradient(w, h, c1, c2):
    base = Image.new("RGB", (w, h))
    px = base.load()
    r1, g1, b1 = Image.new("RGB", (1, 1), c1).getpixel((0, 0))
    r2, g2, b2 = Image.new("RGB", (1, 1), c2).getpixel((0, 0))
    for y in range(h):
        t = y / max(1, h - 1)
        row = (int(r1 + (r2 - r1) * t), int(g1 + (g2 - g1) * t), int(b1 + (b2 - b1) * t))
        for x in range(w):
            px[x, y] = row
    return base


@lru_cache(maxsize=8)
def brand_mark(size: int = 30) -> ctk.CTkImage:
    S = size * SS
    grad = _gradient(S, S, ACCENT, TEAL).convert("RGBA")
    mask = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(mask)
    c = S / 2
    d.polygon([(c, S * .08), (S * .92, c), (c, S * .92), (S * .08, c)], fill=255)
    grad.putalpha(mask)
    grad = grad.resize((size, size), Image.LANCZOS)
    return ctk.CTkImage(light_image=grad, dark_image=grad, size=(size, size))


_PALETTE = ["#4f8cff", "#2dd4bf", "#b388ff", "#ffb74d", "#4dd0e1",
            "#f06292", "#9ccc65", "#ff8a65", "#7e8cff", "#4db6ac"]


@lru_cache(maxsize=4)
def _mask(size: int) -> Image.Image:
    m = Image.new("L", (size * SS, size * SS), 0)
    ImageDraw.Draw(m).ellipse((0, 0, size * SS, size * SS), fill=255)
    return m.resize((size, size), Image.LANCZOS)


def avatar(name: str, size: int = 48, img_path: Path | None = None, ring: str | None = None):
    """Circular avatar (real image or initials disc) with an optional ring."""
    pad = 3 if ring else 0
    canvas = Image.new("RGBA", (size + pad * 2, size + pad * 2), (0, 0, 0, 0))
    if img_path and Path(img_path).exists():
        try:
            disc = Image.open(img_path).convert("RGBA").resize((size, size), Image.LANCZOS)
        except Exception:
            disc = None
    else:
        disc = None
    if disc is None:
        initials = "".join(w[0] for w in name.split()[:2]).upper() or (name[:1].upper() or "?")
        color = _PALETTE[int(hashlib.md5(name.encode()).hexdigest(), 16) % len(_PALETTE)]
        big = size * SS
        disc = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        dd = ImageDraw.Draw(disc)
        grad = _gradient(big, big, color, _shade(color, -40)).convert("RGBA")
        disc.paste(grad, (0, 0))
        try:
            f = ImageFont.truetype("seguisb.ttf", int(big * 0.4))
        except Exception:
            try:
                f = ImageFont.truetype("DejaVuSans-Bold.ttf", int(big * 0.4))
            except Exception:
                f = ImageFont.load_default()
        box = dd.textbbox((0, 0), initials, font=f)
        dd.text(((big - (box[2] - box[0])) / 2, (big - (box[3] - box[1])) / 2 - box[1]),
                initials, font=f, fill="#0a0e15")
        disc = disc.resize((size, size), Image.LANCZOS)
    disc.putalpha(_mask(size))
    if ring:
        rd = ImageDraw.Draw(canvas)
        rd.ellipse([1, 1, size + pad * 2 - 2, size + pad * 2 - 2], outline=ring, width=2)
    canvas.paste(disc, (pad, pad), disc)
    s = size + pad * 2
    return ctk.CTkImage(light_image=canvas, dark_image=canvas, size=(s, s))


def _shade(hex_color: str, delta: int) -> str:
    r, g, b = Image.new("RGB", (1, 1), hex_color).getpixel((0, 0))
    f = lambda v: max(0, min(255, v + delta))
    return f"#{f(r):02x}{f(g):02x}{f(b):02x}"


# --------------------------------------------------------------------------- #
# Buttons
# --------------------------------------------------------------------------- #
def primary(master, text, command, width=130, icon_name=None):
    img = icon(icon_name, 16, INK) if icon_name else None
    return ctk.CTkButton(master, text=text, command=command, width=width, height=40,
                         font=font(13, "bold"), corner_radius=10, fg_color=ACCENT,
                         hover_color=ACCENT_LO, text_color=INK, image=img, compound="right")


def ghost(master, text, command, width=100, color=MUTED, icon_name=None):
    img = icon(icon_name, 16, color) if icon_name else None
    return ctk.CTkButton(master, text=text, command=command, width=width, height=36,
                         font=font(12, "bold"), corner_radius=9, fg_color="transparent",
                         border_width=1, border_color=LINE2, text_color=color,
                         hover_color=BG3, image=img, compound="left")


def icon_button(master, name, command, color=MUTED, tip=None, size=38):
    b = ctk.CTkButton(master, text="", command=command, width=size, height=size,
                      corner_radius=9, fg_color=BG3, hover_color=BG4,
                      image=icon(name, 18, color))
    return b
