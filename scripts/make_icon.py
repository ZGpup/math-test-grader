"""Generate Grader.icns, the Mac app's icon.

A placeholder: the accent colour from static/css/style.css with "MG" in the same KaTeX face the
exported PDFs use. Square, like the rest of the UI. Replace the drawing here, not the .icns.

    uv run python scripts/make_icon.py
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
FONT = ROOT / "static" / "vendor" / "katex" / "fonts" / "KaTeX_Main-Bold.ttf"
OUT = ROOT / "Grader.icns"

TEXT = "MG"
ACCENT = (0.122, 0.431, 0.820)  # --accent #1f6ed1
PAPER = (1, 1, 1)
INSET = 0.10  # macOS icon art sits inside the canvas rather than bleeding to its edge

# Every size macOS asks for, as (pixels, iconset filename).
SIZES = [
    (16, "icon_16x16.png"),
    (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"),
    (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"),
    (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"),
    (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"),
    (1024, "icon_512x512@2x.png"),
]


def render(size: int) -> bytes:
    """One square PNG, `size` pixels a side. A page point is a pixel at the default 72 dpi."""
    doc = pymupdf.open()
    page = doc.new_page(width=size, height=size)
    pad = size * INSET
    page.draw_rect(pymupdf.Rect(pad, pad, size - pad, size - pad), color=None, fill=ACCENT)

    art = size - 2 * pad
    font = pymupdf.Font(fontfile=str(FONT))
    page.insert_font(fontname="katexbold", fontfile=str(FONT))
    # Fit the text to the art box rather than guessing a point size.
    font_size = art * 0.62 / (font.text_length(TEXT, fontsize=1.0) or 1)
    width = font.text_length(TEXT, fontsize=font_size)
    page.insert_text(
        (pad + (art - width) / 2, size / 2 + font_size * 0.35),
        TEXT,
        fontname="katexbold",
        fontsize=font_size,
        color=PAPER,
    )
    png = page.get_pixmap(alpha=False).tobytes("png")
    doc.close()
    return png


def main() -> None:
    if not FONT.is_file():
        sys.exit(f"missing font: {FONT}")
    if not shutil.which("iconutil"):
        sys.exit("iconutil not found (it ships with macOS)")

    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Grader.iconset"
        iconset.mkdir()
        for size, name in SIZES:
            (iconset / name).write_bytes(render(size))
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT)], check=True)
    print(f"{OUT}  ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
