#!/usr/bin/env python3
"""Generate CAVY's app icon (navy rounded square, amber "C") as a PNG.

Run once (or whenever the brand colors/mark change) from the project root:

    python scripts/generate_icon.py

The result is checked into ``assets/icon.png`` — this script isn't run at
app startup, only when regenerating the icon. ``app.py`` loads that PNG at
runtime (via pyobjc on macOS) to set the Dock icon, since the app isn't
packaged as a real ``.app`` bundle with its own ``Info.plist`` yet.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_ROOT / "src" / "eaal_platform" / "assets"
FONT_PATH = ASSETS_DIR / "fonts" / "Lora-Variable.ttf"
OUTPUT_PATH = ASSETS_DIR / "icon.png"

_SIZE = 512
_NAVY = (15, 27, 46, 255)
_AMBER = (224, 164, 74, 255)
_CORNER_RADIUS = 108


def main() -> int:
    image = Image.new("RGBA", (_SIZE, _SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, _SIZE - 1, _SIZE - 1), radius=_CORNER_RADIUS, fill=_NAVY)

    font = ImageFont.truetype(str(FONT_PATH), size=320)
    text = "C"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    position = ((_SIZE - text_width) / 2 - bbox[0], (_SIZE - text_height) / 2 - bbox[1])
    draw.text(position, text, font=font, fill=_AMBER)

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT_PATH)
    print(f"Wrote {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
