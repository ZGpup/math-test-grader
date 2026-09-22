"""PDF helpers: page counts, PNG rendering, splitting scans into tests."""

from pathlib import Path

import pymupdf

RENDER_DPI = 150
THUMB_SHRINK = 2  # thumbnails are 1/4 of the full render


def page_count(pdf_path: Path) -> int:
    with pymupdf.open(pdf_path) as doc:
        return doc.page_count


def validate_pdf(pdf_path: Path) -> int:
    """Return the page count, or raise ValueError if the file is not a usable PDF."""
    try:
        with pymupdf.open(pdf_path) as doc:
            if not doc.is_pdf or doc.page_count == 0:
                raise ValueError("not a PDF")
            return doc.page_count
    except (RuntimeError, pymupdf.FileDataError) as e:
        raise ValueError("not a PDF") from e


def page_png(out_dir: Path, index: int, thumb: bool = False) -> Path:
    return out_dir / (f"{index:04d}_t.png" if thumb else f"{index:04d}.png")


def _render_range(doc: pymupdf.Document, out_dir: Path, start: int, stop: int, dpi: int) -> None:
    for i in range(start, stop):
        pix = doc[i].get_pixmap(dpi=dpi, alpha=False)
        pix.save(page_png(out_dir, i))
        pix.shrink(THUMB_SHRINK)
        pix.save(page_png(out_dir, i, thumb=True))


def render_pages(pdf_path: Path, out_dir: Path, dpi: int = RENDER_DPI) -> int:
    """Render every page to <out_dir>/NNNN.png plus a NNNN_t.png thumbnail."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(pdf_path) as doc:
        _render_range(doc, out_dir, 0, doc.page_count, dpi)
        return doc.page_count


def append_pdf(pdf_path: Path, extra: Path, out_dir: Path, dpi: int = RENDER_DPI) -> int:
    """Add another PDF to the end of a scan, render only its pages, and return the new page count.

    Pages already rendered keep their numbers, so their cached images stay good.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = pdf_path.with_name(f"{pdf_path.name}.tmp")
    with pymupdf.open(pdf_path) as doc, pymupdf.open(extra) as more:
        start = doc.page_count
        doc.insert_pdf(more)
        total = doc.page_count
        doc.save(tmp, garbage=3, deflate=True)
    tmp.replace(pdf_path)
    with pymupdf.open(pdf_path) as doc:
        _render_range(doc, out_dir, start, total, dpi)
    return total


def split_ranges(total_pages: int, pages_per_test: int) -> tuple[list[tuple[int, int]], int]:
    """Split a scan into consecutive tests.

    Returns ([(first_page, page_count), ...], leftover_pages). Only full chunks
    become tests; leftover pages at the end are reported and ignored.
    """
    if pages_per_test < 1:
        raise ValueError("pages_per_test must be >= 1")
    tests = total_pages // pages_per_test
    ranges = [(i * pages_per_test, pages_per_test) for i in range(tests)]
    return ranges, total_pages % pages_per_test


def default_page_map(blank_pages: int, pages_per_test: int) -> list[int]:
    """Default blank-page -> scan-page-offset mapping for one test.

    Equal length: one-to-one. Exactly double: odd scan pages (duplex scans with
    blank backs), i.e. offsets 0, 2, 4, ... Otherwise: one-to-one, clamped to the
    last scan page, for the user to adjust.
    """
    if pages_per_test == 2 * blank_pages:
        return [2 * i for i in range(blank_pages)]
    return [min(i, pages_per_test - 1) for i in range(blank_pages)]


def cover_offset(page_map: list[int], cover_page: int, page_count: int) -> int:
    """Which page of a test carries the student's name, as an offset within the test.

    The mapped cover page, or the first page when the assignment has no cover page at all
    (cover_page is -1). static/js/common.js coverOffset does the same for the screens.
    """
    at = page_map[cover_page] if 0 <= cover_page < len(page_map) else 0
    return min(max(at, 0), max(0, page_count - 1))


def mapping_mode(blank_pages: int, pages_per_test: int, page_map: list[int]) -> str:
    """'identity', 'odd', or 'custom' -- used by the UI to decide whether to show the table."""
    if pages_per_test == blank_pages and page_map == list(range(blank_pages)):
        return "identity"
    if pages_per_test == 2 * blank_pages and page_map == [2 * i for i in range(blank_pages)]:
        return "odd"
    return "custom"
