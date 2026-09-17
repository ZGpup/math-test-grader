"""Generate fake test data: a blank test, scan batches, and a roster.

    uv run python scripts/make_fixture.py [--out data/fixtures]

Writes:
    blank.pdf         5 pages: a cover plus 4 problems
    scans.pdf         3 students x 5 pages = 15 pages
    scans_duplex.pdf  the same with a blank back after every page = 30 pages
    roster.txt        the 3 students, in both accepted formats
"""

import argparse
import random
from pathlib import Path

import pymupdf

W, H = 612, 792  # US Letter
INK = (0.12, 0.2, 0.55)

STUDENTS = [("Alice", "Example"), ("Bob", "Sample"), ("Carol", "Testcase")]

PROBLEMS = [
    ("Solve for x:  2x + 3 = 11.", ["2x = 8", "x = 4"]),
    ("Expand  (x + 2)(x - 5).", ["x^2 - 5x + 2x - 10", "= x^2 - 3x - 10"]),
    ("Find the slope of the line through (1, 2) and (3, 8).", ["m = (8 - 2)/(3 - 1)", "m = 3"]),
    ("Evaluate  1 + 2 + ... + 10.", ["10 * 11 / 2", "= 55"]),
]

# Per student, the answer lines that differ from the correct ones (problem index -> lines).
MISTAKES = [
    {},
    {1: ["x^2 - 5x + 2x + 10", "= x^2 - 3x + 10"], 3: ["= 50"]},
    {0: ["2x = 14", "x = 7"], 2: ["m = 6/2", "m = 2"]},
]


def _draw_blank_page(page: pymupdf.Page, index: int) -> None:
    if index == 0:
        page.insert_text((72, 120), "Algebra Quiz 3", fontname="tibo", fontsize=28)
        page.insert_text((72, 190), "Name:", fontname="tiro", fontsize=16)
        page.draw_line((125, 192), (400, 192), width=0.8)
        page.insert_text((72, 250), "Show all work. 4 problems, 10 points each.", fontname="tiro", fontsize=12)
        page.insert_text((72, 272), "Calculators are not allowed.", fontname="tiro", fontsize=12)
        return
    question, _ = PROBLEMS[index - 1]
    page.insert_text((72, 90), f"Problem {index}  (10 points)", fontname="tibo", fontsize=16)
    page.insert_text((72, 120), question, fontname="tiro", fontsize=13)
    page.draw_rect(pymupdf.Rect(72, 150, W - 72, H - 90), width=0.6)
    page.insert_text((W / 2 - 10, H - 50), str(index + 1), fontname="tiro", fontsize=10)


def make_blank(path: Path) -> Path:
    doc = pymupdf.open()
    for i in range(1 + len(PROBLEMS)):
        _draw_blank_page(doc.new_page(width=W, height=H), i)
    doc.save(path)
    doc.close()
    return path


def _rasterize(doc: pymupdf.Document) -> pymupdf.Document:
    """Turn vector pages into image-only pages, like a scanner does."""
    out = pymupdf.open()
    for page in doc:
        pix = page.get_pixmap(dpi=100, alpha=False)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=pix.tobytes("jpeg"))
    return out


def make_scans(path: Path, duplex: bool = False) -> Path:
    rng = random.Random(7)
    doc = pymupdf.open()
    for s, (first, last) in enumerate(STUDENTS):
        for i in range(1 + len(PROBLEMS)):
            page = doc.new_page(width=W, height=H)
            _draw_blank_page(page, i)
            dx, dy = rng.uniform(-6, 6), rng.uniform(-6, 6)
            if i == 0:
                page.insert_text((140 + dx, 186 + dy), f"{first} {last}", fontname="tiit", fontsize=22, color=INK)
            else:
                lines = MISTAKES[s].get(i - 1, PROBLEMS[i - 1][1])
                for k, line in enumerate(lines):
                    page.insert_text(
                        (100 + dx + 8 * k, 210 + dy + 48 * k), line, fontname="tiit", fontsize=22, color=INK
                    )
            if duplex:
                doc.new_page(width=W, height=H)
    scanned = _rasterize(doc)
    scanned.save(path, garbage=3, deflate=True)
    scanned.close()
    doc.close()
    return path


def make_roster(path: Path) -> Path:
    first, last = STUDENTS[1]
    lines = [f"{STUDENTS[0][0]} {STUDENTS[0][1]}", f"{last}, {first}", f"{STUDENTS[2][0]} {STUDENTS[2][1]}"]
    path.write_text("\n".join(lines) + "\n")
    return path


def make_all(out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    return {
        "blank": make_blank(out / "blank.pdf"),
        "scans": make_scans(out / "scans.pdf"),
        "duplex": make_scans(out / "scans_duplex.pdf", duplex=True),
        "roster": make_roster(out / "roster.txt"),
    }


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=root / "data" / "fixtures")
    args = parser.parse_args()
    for name, path in make_all(args.out).items():
        print(f"{name:7} {path}")


if __name__ == "__main__":
    main()
