#!/usr/bin/env python3
"""Draw a synthetic frame to render the GUI over.

`panel_render.py` is happy to grab the real screen, which is the right thing
while working on layout and the wrong thing to publish: the picker displays the
frame it is given, so a live grab puts whatever else is open on the desktop -
terminals, browsers, chat windows - into the screenshot.

This draws a stand-in instead: an abstract dark scene with a dialogue box where
the default region sits, carrying the same synthetic two-line passage the demo
translations use. Nothing here is from a game.

    .venv/bin/python probe/make_render_frame.py [out.png]
    LINTRANSLATOR_RENDER_SOURCE=data/render_frame.png .venv/bin/python probe/panel_render.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

APP_DIR = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else APP_DIR / "data" / "render_frame.png"

W, H = 1500, 980

# The app's default region, so the picker's box lands where a real one would.
REGION = (0.10, 0.78, 0.80, 0.12)

LINE_ONE = "The committee has resolved that this entry warrants retention as a"
LINE_TWO = "standing record. The material below is the file concerning today's submission."
SPEAKER = "Archivist"


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def background() -> Image.Image:
    """A dark scene with a soft bloom, drawn rather than photographed."""
    im = Image.new("RGB", (W, H), (14, 15, 22))
    draw = ImageDraw.Draw(im, "RGBA")
    # A few large translucent discs, so the frame has some structure for the
    # selection rectangle to sit on without being a picture of anything.
    for (cx, cy, r, colour) in (
        (300, 300, 380, (44, 58, 96, 90)),
        (1150, 240, 300, (96, 52, 68, 80)),
        (760, 700, 520, (30, 40, 70, 90)),
    ):
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=colour)
    return im


def dialogue_box(im: Image.Image) -> None:
    draw = ImageDraw.Draw(im, "RGBA")
    x, y, w, h = (
        round(REGION[0] * W),
        round(REGION[1] * H),
        round(REGION[2] * W),
        round(REGION[3] * H),
    )
    draw.rounded_rectangle((x, y, x + w, y + h), radius=10, fill=(10, 10, 14, 210))

    # Speaker plate, offset up and to the left of the box, the way most of these
    # games do it.
    name_font = font(22)
    tw = draw.textlength(SPEAKER, font=name_font)
    draw.rounded_rectangle(
        (x - 8, y - 40, x + tw + 30, y - 4), radius=6, fill=(92, 62, 74, 235)
    )
    draw.text((x + 11, y - 34), SPEAKER, font=name_font, fill=(245, 238, 230))

    body = font(26)
    draw.text((x + 22, y + 26), LINE_ONE, font=body, fill=(232, 230, 226))
    draw.text((x + 22, y + 62), LINE_TWO, font=body, fill=(232, 230, 226))


def main() -> int:
    im = background()
    dialogue_box(im)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    im.save(OUT)
    print(f"wrote {OUT} ({W}x{H})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
