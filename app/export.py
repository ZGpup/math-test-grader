"""Results table, CSV output, and annotated PDF export."""

import csv
import io
import re
import shutil
import sqlite3
import zipfile
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pymupdf
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.mathtext import MathTextParser

from app import db

TEXT_FONT = db.ROOT / "static" / "vendor" / "katex" / "fonts" / "KaTeX_Main-Regular.ttf"
ACCENT = (0.12, 0.43, 0.82)  # --accent in static/css/style.css
INK = (0.11, 0.14, 0.16)  # --text

# Annotation boxes scale with page width so exports match the grading view
# (static/css/style.css .ann: font-size 1.6cqw, max-width 35cqw).
ANN_FONT = 0.016
ANN_MAX_WIDTH = 0.35
ANN_PAD = 0.3  # in font sizes
SUMMARY_FONT = 0.018
LABEL_DPI = 300


# ---------------------------------------------------------------- numbers


def fmt_num(value: float) -> str:
    """7.0 -> "7", 7.5 -> "7.5", 7.125 -> "7.13"."""
    return f"{value:.2f}".rstrip("0").rstrip(".")


def grade_percent(earned: float, possible: float) -> float:
    if possible <= 0:
        return 0.0
    return float(Decimal(repr(earned / possible * 100)).quantize(Decimal("0.1"), ROUND_HALF_UP))


# ---------------------------------------------------------------- results


def results(conn: sqlite3.Connection, assignment_id: int) -> dict:
    """One row per roster student (absent students have no cells), then unmatched submissions."""
    course_id = conn.execute("SELECT course_id FROM assignments WHERE id = ?", (assignment_id,)).fetchone()[0]
    probs = db.problems(conn, assignment_id)
    possible = sum(p["max_points"] for p in probs)
    scores = db.score_table(conn, assignment_id)
    graded = db.graded_pairs(conn, assignment_id)
    subs = db.submissions(conn, assignment_id, roster_order=True)
    by_student = {s["student_id"]: s for s in subs if s["student_id"] is not None}

    def row(first: str, last: str, sub: dict | None) -> dict:
        base = {"first_name": first, "last_name": last}
        if sub is None:
            return base | {"submission_id": None, "status": "absent", "cells": [], "total": None, "percent": None}
        cells = [
            {"points": scores[(sub["id"], p["id"])], "graded": (sub["id"], p["id"]) in graded} for p in probs
        ]
        total = round(sum(c["points"] for c in cells), 6)
        return base | {
            "submission_id": sub["id"],
            "status": "matched" if sub["student_id"] is not None else "unmatched",
            "cells": cells,
            "total": total,
            "percent": grade_percent(total, possible),
        }

    rows = [row(st["first_name"], st["last_name"], by_student.get(st["id"])) for st in db.students(conn, course_id)]
    rows += [row("", "", s) for s in subs if s["student_id"] is None]
    return {"problems": probs, "total_possible": possible, "rows": rows}


def results_csv(res: dict) -> str:
    """Roster students in roster order; absent students get empty cells; unmatched tests are omitted."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    labels = [p["label"] for p in res["problems"]]
    writer.writerow(["First name", "Last name", "Grade %", *labels, "Total points earned"])
    for row in res["rows"]:
        if row["status"] == "unmatched":
            continue
        if row["status"] == "absent":
            writer.writerow([row["first_name"], row["last_name"], "", *[""] * len(labels), ""])
            continue
        writer.writerow(
            [
                row["first_name"],
                row["last_name"],
                f"{row['percent']:.1f}",
                *[fmt_num(c["points"]) for c in row["cells"]],
                fmt_num(row["total"]),
            ]
        )
    return buf.getvalue()


# ---------------------------------------------------------------- label rendering


_MATH = re.compile(r"(\$[^$]+\$)")
_parser = MathTextParser("path")


def _prop(size: float) -> FontProperties:
    return FontProperties(fname=TEXT_FONT, size=size, math_fontfamily="cm")


def _parses(s: str, prop: FontProperties) -> bool:
    try:
        _parser.parse(s, dpi=72, prop=prop)
        return True
    except Exception:
        return False


def _tokens(text: str, prop: FontProperties) -> list[tuple[str, str, bool]]:
    """Split into wrap units: (raw, escaped, is_math) with a leading space kept in both forms.

    Math that mathtext cannot parse becomes raw text. The same $...$ split is used
    by the browser (static/js/common.js renderTex).
    """
    tokens = []
    pending_space = ""
    for part in _MATH.split(text):
        if not part:
            continue
        if part.startswith("$") and part.endswith("$") and len(part) > 2 and _parses(part, prop):
            tokens.append((pending_space + part, pending_space + part, True))
            pending_space = ""
            continue
        for word in re.split(r"(\s+)", part):
            if not word:
                continue
            if word.isspace():
                pending_space = " "
            else:
                raw = pending_space + word
                tokens.append((raw, raw.replace("$", r"\$"), False))
                pending_space = ""
    return tokens


def _line_width(tokens: list[tuple[str, str, bool]], prop: FontProperties) -> float:
    s = "".join(t[1] for t in tokens)
    return _parser.parse(s, dpi=72, prop=prop)[0]


def _wrap(tokens: list[tuple[str, str, bool]], prop: FontProperties, max_width: float) -> list[list]:
    lines: list[list] = []
    for token in tokens:
        if lines and _line_width(lines[-1] + [token], prop) <= max_width:
            lines[-1].append(token)
        else:
            first = (token[0].lstrip(), token[1].lstrip(), token[2])
            lines.append([first])
    return lines


def _render_line(tokens: list[tuple[str, str, bool]], prop: FontProperties) -> tuple[bytes, float, float]:
    """PNG bytes plus width and height in points. Lines without math are drawn as plain text."""
    is_math = any(t[2] for t in tokens)
    escaped = "".join(t[1] for t in tokens)
    width, height, depth, *_ = _parser.parse(escaped, dpi=72, prop=prop)
    width = max(width, 1.0)
    height = max(height, prop.get_size() * 1.15)
    fig = Figure(figsize=(width / 72, height / 72))
    FigureCanvasAgg(fig)
    text = escaped if is_math else "".join(t[0] for t in tokens)
    fig.text(0, depth / height, text, fontproperties=prop, color=INK, parse_math=is_math)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=LABEL_DPI, transparent=True)
    return buf.getvalue(), width, height


def render_label(text: str, deduction: float, font_size: float, max_width: float) -> list[tuple[bytes, float, float]]:
    prop = _prop(font_size)
    tokens = _tokens(text, prop)
    if deduction:
        # A superscript with its unit, like the screen (static/css/style.css .ann .ded),
        # so it does not read as part of the comment's math.
        d = f" $^{{{'-' if deduction > 0 else '+'}{fmt_num(abs(deduction))}\\mathrm{{pts}}}}$"
        tokens.append((d, d, True))
    if not tokens:
        tokens = [(" ", " ", False)]
    return [_render_line(line, prop) for line in _wrap(tokens, prop, max_width)]


# ---------------------------------------------------------------- PDF export


def safe_filename(s: str) -> str:
    return re.sub(r"[^\w.-]+", "_", s).strip("._")


def _mirror_page(doc: pymupdf.Document, page: pymupdf.Page) -> None:
    """Flip a page left to right by transforming its contents, leaving its box alone.

    Anything drawn afterwards (annotations, the score box) is placed normally.
    """
    box = page.mediabox
    matrix = f"-1 0 0 1 {box.x0 + box.x1} 0 cm\n".encode()
    xref = page.get_contents()[0]
    doc.update_stream(xref, b"q\n" + matrix + page.read_contents() + b"\nQ\n")
    page.set_contents(xref)


def _build_submission(source: pymupdf.Document, pages: list[dict]) -> pymupdf.Document:
    """One test as its own document: the chosen scan pages, in order, each the right way up."""
    out = pymupdf.open()
    for p in pages:
        out.insert_pdf(source, from_page=p["scan_page"], to_page=p["scan_page"])
    for page, p in zip(out, pages):
        if p["upside_down"]:
            page.set_rotation((page.rotation + 180) % 360)
        if page.rotation:
            page.remove_rotation()
        if p["mirrored"]:
            _mirror_page(out, page)
    return out


def _draw_annotation(page: pymupdf.Page, x: float, y: float, lines: list[tuple[bytes, float, float]]) -> None:
    W, H = page.rect.width, page.rect.height
    pad = ANN_PAD * ANN_FONT * W
    box_w = max(w for _, w, _ in lines) + 2 * pad
    box_h = sum(h for _, _, h in lines) + 2 * pad
    x0 = min(max(0.0, x * W), max(0.0, W - box_w))
    y0 = min(max(0.0, y * H), max(0.0, H - box_h))
    page.draw_rect(
        pymupdf.Rect(x0, y0, x0 + box_w, y0 + box_h), color=ACCENT, fill=(1, 1, 1), width=0.8, fill_opacity=0.9
    )
    top = y0 + pad
    for png, w, h in lines:
        page.insert_image(pymupdf.Rect(x0 + pad, top, x0 + pad + w, top + h), stream=png)
        top += h


def _draw_summary(page: pymupdf.Page, rows: list[tuple[str, str]]) -> None:
    """Score box in the top-right corner: one row per problem, the last row is the total."""
    W = page.rect.width
    fs = SUMMARY_FONT * W
    font = pymupdf.Font(fontfile=str(TEXT_FONT))
    page.insert_font(fontname="katexmain", fontfile=str(TEXT_FONT))
    row_h = fs * 1.45
    pad = fs * 0.6
    left_w = max(font.text_length(a, fontsize=fs) for a, _ in rows)
    right_w = max(font.text_length(b, fontsize=fs) for _, b in rows)
    total_gap = fs * 0.4
    box_w = left_w + fs * 1.5 + right_w + 2 * pad
    box_h = row_h * len(rows) + total_gap + 2 * pad
    margin = 0.03 * W
    x0, y0 = W - margin - box_w, margin
    page.draw_rect(
        pymupdf.Rect(x0, y0, x0 + box_w, y0 + box_h), color=ACCENT, fill=(1, 1, 1), width=1.2, fill_opacity=0.95
    )
    for i, (a, b) in enumerate(rows):
        baseline = y0 + pad + row_h * i + fs
        if i == len(rows) - 1:
            line_y = baseline - fs * 0.95
            page.draw_line((x0 + pad, line_y), (x0 + box_w - pad, line_y), color=ACCENT, width=0.6)
            baseline += total_gap
        page.insert_text((x0 + pad, baseline), a, fontname="katexmain", fontsize=fs, color=INK)
        page.insert_text(
            (x0 + box_w - pad - font.text_length(b, fontsize=fs), baseline),
            b,
            fontname="katexmain",
            fontsize=fs,
            color=INK,
        )


def export_pdfs(conn: sqlite3.Connection, assignment_id: int) -> tuple[list[Path], Path]:
    """Write exports/<assignment>/<Last>_<First>.pdf for every matched submission, plus a zip."""
    a = conn.execute("SELECT name, cover_page FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
    data = db.data_dir()
    folder = safe_filename(a["name"]) or f"assignment_{assignment_id}"
    out_dir = data / "exports" / folder
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    probs = db.problems(conn, assignment_id)
    possible = sum(p["max_points"] for p in probs)
    scores = db.score_table(conn, assignment_id)
    comments = {
        r["id"]: r
        for r in conn.execute(
            """SELECT c.id, c.text, c.deduction FROM comments c
               JOIN problems p ON p.id = c.problem_id WHERE p.assignment_id = ?""",
            (assignment_id,),
        )
    }
    labels: dict[tuple, list] = {}
    sources: dict[str, pymupdf.Document] = {}
    written: list[Path] = []
    try:
        for sub in db.submissions(conn, assignment_id, roster_order=True):
            if sub["student_id"] is None:
                continue
            name = safe_filename(f"{sub['last_name']}_{sub['first_name']}") or f"submission_{sub['id']}"
            path = out_dir / f"{name}.pdf"
            n = 2
            while path in written:
                path = out_dir / f"{name}_{n}.pdf"
                n += 1
            if sub["key"] not in sources:
                sources[sub["key"]] = pymupdf.open(data / "uploads" / f"{sub['key']}.pdf")
            out = _build_submission(sources[sub["key"]], sub["pages"])
            annotations = conn.execute(
                "SELECT comment_id, page, x, y FROM annotations WHERE submission_id = ? ORDER BY id", (sub["id"],)
            )
            for ann in annotations:
                if ann["page"] >= out.page_count:
                    continue
                page = out[ann["page"]]
                comment = comments[ann["comment_id"]]
                W = page.rect.width
                key = (comment["id"], round(W, 1))
                if key not in labels:
                    labels[key] = render_label(
                        comment["text"],
                        comment["deduction"],
                        ANN_FONT * W,
                        ANN_MAX_WIDTH * W - 2 * ANN_PAD * ANN_FONT * W,
                    )
                _draw_annotation(page, ann["x"], ann["y"], labels[key])
            earned = sum(scores[(sub["id"], p["id"])] for p in probs)
            summary = [(p["label"], f"{fmt_num(scores[(sub['id'], p['id'])])} / {fmt_num(p['max_points'])}") for p in probs]
            summary.append(("Total", f"{fmt_num(earned)} / {fmt_num(possible)}"))
            cover = min(sub["page_map"][a["cover_page"]], out.page_count - 1)
            _draw_summary(out[cover], summary)
            out.save(path, garbage=3, deflate=True)
            out.close()
            written.append(path)
    finally:
        for doc in sources.values():
            doc.close()

    zip_path = data / "exports" / f"{folder}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in written:
            zf.write(path, arcname=f"{folder}/{path.name}")
    return written, zip_path
