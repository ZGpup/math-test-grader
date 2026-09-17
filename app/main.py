"""FastAPI app: REST API under /api, static frontend at /."""

import argparse
import json
import re
import secrets
import shutil
import sqlite3
import threading
import webbrowser
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import db, export, pdf

STATIC = db.ROOT / "static"
DEFAULT_POINTS = 10.0

app = FastAPI(title="Grader")


# ---------------------------------------------------------------- helpers


def fetch(conn: sqlite3.Connection, sql: str, params: tuple) -> sqlite3.Row:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise HTTPException(404, "Not found")
    return row


def uploads_dir() -> Path:
    path = db.data_dir() / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def pages_dir(key: str) -> Path:
    return db.data_dir() / "pages" / key


def remove_files(key: str | None) -> None:
    if not key:
        return
    (uploads_dir() / f"{key}.pdf").unlink(missing_ok=True)
    shutil.rmtree(pages_dir(key), ignore_errors=True)


def save_upload(file: UploadFile, key: str) -> int:
    """Store an uploaded PDF as uploads/<key>.pdf, render its pages, and return the page count."""
    path = uploads_dir() / f"{key}.pdf"
    with path.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        pdf.validate_pdf(path)
    except ValueError:
        path.unlink(missing_ok=True)
        raise HTTPException(400, "Not a PDF")
    return pdf.render_pages(path, pages_dir(key))


def assignment_row(conn: sqlite3.Connection, assignment_id: int) -> sqlite3.Row:
    return fetch(conn, "SELECT * FROM assignments WHERE id = ?", (assignment_id,))


def assignment_meta(conn: sqlite3.Connection, a: sqlite3.Row) -> dict:
    course = fetch(conn, "SELECT id, name FROM courses WHERE id = ?", (a["course_id"],))
    return {"id": a["id"], "name": a["name"], "course": dict(course)}


def batch_info(conn: sqlite3.Connection, b: sqlite3.Row, blank_pages: int) -> dict:
    page_map = json.loads(b["page_map"])
    stats = conn.execute(
        """SELECT COUNT(*) AS tests, COUNT(student_id) AS matched,
                  (SELECT COUNT(*) FROM annotations a JOIN submissions s2 ON s2.id = a.submission_id
                   WHERE s2.batch_id = ?) AS annotations
           FROM submissions WHERE batch_id = ?""",
        (b["id"], b["id"]),
    ).fetchone()
    return {
        "id": b["id"],
        "key": b["key"],
        "filename": b["filename"],
        "page_count": b["page_count"],
        "pages_per_test": b["pages_per_test"],
        "leftover": b["page_count"] % b["pages_per_test"],
        "page_map": page_map,
        "mode": pdf.mapping_mode(blank_pages, b["pages_per_test"], page_map),
        "checked": bool(b["checked"]),
        **dict(stats),
    }


def assignment_detail(conn: sqlite3.Connection, assignment_id: int) -> dict:
    a = assignment_row(conn, assignment_id)
    probs = db.problems(conn, assignment_id)
    by_page = {p["page"]: p for p in probs}
    pages = []
    for i in range(a["blank_pages"]):
        kind = "cover" if i == a["cover_page"] else "problem" if i in by_page else "none"
        pages.append({"index": i, "kind": kind, "problem": by_page.get(i)})
    batches = conn.execute("SELECT * FROM batches WHERE assignment_id = ? ORDER BY id", (assignment_id,)).fetchall()
    counts = dict(
        conn.execute(
            """SELECT c.problem_id, COUNT(*) FROM comments c JOIN problems p ON p.id = c.problem_id
               WHERE p.assignment_id = ? GROUP BY c.problem_id""",
            (assignment_id,),
        ).fetchall()
    )
    for p in probs:
        p["comments"] = counts.get(p["id"], 0)
    return assignment_meta(conn, a) | {
        "blank_key": a["blank_key"],
        "blank_pages": a["blank_pages"],
        "cover_page": a["cover_page"],
        "pages": pages,
        "problems": probs,
        "total_points": sum(p["max_points"] for p in probs),
        "batches": [batch_info(conn, b, a["blank_pages"]) for b in batches],
        "progress": db.progress(conn, assignment_id),
    }


def next_label(conn: sqlite3.Connection, assignment_id: int) -> str:
    numbers = [int(p["label"]) for p in db.problems(conn, assignment_id) if p["label"].isdigit()]
    return str(max(numbers, default=0) + 1)


def submission_assignment(conn: sqlite3.Connection, submission_id: int) -> sqlite3.Row:
    return fetch(
        conn,
        """SELECT s.id, s.page_count, b.assignment_id, a.course_id FROM submissions s
           JOIN batches b ON b.id = s.batch_id JOIN assignments a ON a.id = b.assignment_id
           WHERE s.id = ?""",
        (submission_id,),
    )


# ---------------------------------------------------------------- models


class NameIn(BaseModel):
    name: str = Field(min_length=1)


class RosterIn(BaseModel):
    text: str


class PageIn(BaseModel):
    kind: Literal["cover", "problem", "none"]
    label: str | None = None
    max_points: float | None = Field(None, ge=0)


class BatchPatch(BaseModel):
    pages_per_test: int | None = Field(None, ge=1)
    page_map: list[int] | None = None
    checked: bool | None = None


class ScanPageIn(BaseModel):
    scan_page: int = Field(ge=0)
    upside_down: bool = False
    mirrored: bool = False


class BatchPagesIn(BaseModel):
    pages: list[ScanPageIn]


class MatchIn(BaseModel):
    student_id: int | None


class CommentIn(BaseModel):
    text: str = Field(min_length=1, pattern=r"\S")
    deduction: float = Field(0, ge=0)


class CommentPatch(BaseModel):
    text: str | None = Field(None, min_length=1, pattern=r"\S")
    deduction: float | None = Field(None, ge=0)


class AnnotationIn(BaseModel):
    comment_id: int
    page: int = Field(ge=0)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class AnnotationPatch(BaseModel):
    page: int | None = Field(None, ge=0)
    x: float | None = Field(None, ge=0, le=1)
    y: float | None = Field(None, ge=0, le=1)


class GradedIn(BaseModel):
    graded: bool


# ---------------------------------------------------------------- courses


@app.get("/api/courses")
def list_courses():
    with db.session() as conn:
        rows = conn.execute(
            """SELECT c.id, c.name,
                      (SELECT COUNT(*) FROM students s WHERE s.course_id = c.id) AS students,
                      (SELECT COUNT(*) FROM assignments a WHERE a.course_id = c.id) AS assignments
               FROM courses c ORDER BY c.id"""
        )
        return [dict(r) for r in rows]


@app.post("/api/courses")
def create_course(body: NameIn):
    with db.session() as conn:
        cur = conn.execute("INSERT INTO courses (name) VALUES (?)", (body.name.strip(),))
        return {"id": cur.lastrowid, "name": body.name.strip()}


@app.get("/api/courses/{course_id}")
def get_course(course_id: int):
    with db.session() as conn:
        c = fetch(conn, "SELECT * FROM courses WHERE id = ?", (course_id,))
        assignments = []
        for a in conn.execute("SELECT id, name FROM assignments WHERE course_id = ? ORDER BY id", (course_id,)):
            probs = db.problems(conn, a["id"])
            assignments.append(
                dict(a) | {"total_points": sum(p["max_points"] for p in probs), "progress": db.progress(conn, a["id"])}
            )
        return {
            "id": c["id"],
            "name": c["name"],
            "roster_text": c["roster_text"],
            "students": db.students(conn, course_id),
            "assignments": assignments,
        }


@app.patch("/api/courses/{course_id}")
def rename_course(course_id: int, body: NameIn):
    with db.session() as conn:
        fetch(conn, "SELECT id FROM courses WHERE id = ?", (course_id,))
        conn.execute("UPDATE courses SET name = ? WHERE id = ?", (body.name.strip(), course_id))
        return {"ok": True}


@app.delete("/api/courses/{course_id}")
def delete_course(course_id: int):
    with db.session() as conn:
        keys = [r[0] for r in conn.execute("SELECT blank_key FROM assignments WHERE course_id = ?", (course_id,))]
        keys += [
            r[0]
            for r in conn.execute(
                "SELECT b.key FROM batches b JOIN assignments a ON a.id = b.assignment_id WHERE a.course_id = ?",
                (course_id,),
            )
        ]
        conn.execute("DELETE FROM courses WHERE id = ?", (course_id,))
    for key in keys:
        remove_files(key)
    return {"ok": True}


@app.put("/api/courses/{course_id}/roster")
def put_roster(course_id: int, body: RosterIn):
    with db.session() as conn:
        fetch(conn, "SELECT id FROM courses WHERE id = ?", (course_id,))
        db.set_roster(conn, course_id, body.text)
        return {"students": db.students(conn, course_id)}


@app.post("/api/courses/{course_id}/assignments")
def create_assignment(course_id: int, body: NameIn):
    with db.session() as conn:
        fetch(conn, "SELECT id FROM courses WHERE id = ?", (course_id,))
        cur = conn.execute("INSERT INTO assignments (course_id, name) VALUES (?, ?)", (course_id, body.name.strip()))
        return {"id": cur.lastrowid}


# ---------------------------------------------------------------- assignments


@app.get("/api/assignments/{assignment_id}")
def get_assignment(assignment_id: int):
    with db.session() as conn:
        return assignment_detail(conn, assignment_id)


@app.patch("/api/assignments/{assignment_id}")
def rename_assignment(assignment_id: int, body: NameIn):
    with db.session() as conn:
        assignment_row(conn, assignment_id)
        conn.execute("UPDATE assignments SET name = ? WHERE id = ?", (body.name.strip(), assignment_id))
        return {"ok": True}


@app.delete("/api/assignments/{assignment_id}")
def delete_assignment(assignment_id: int):
    with db.session() as conn:
        a = assignment_row(conn, assignment_id)
        keys = [a["blank_key"]] + [
            r[0] for r in conn.execute("SELECT key FROM batches WHERE assignment_id = ?", (assignment_id,))
        ]
        conn.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))
    for key in keys:
        remove_files(key)
    return {"ok": True}


@app.post("/api/assignments/{assignment_id}/blank")
def upload_blank(assignment_id: int, file: UploadFile = File(...)):
    with db.session() as conn:
        a = assignment_row(conn, assignment_id)
        if conn.execute("SELECT 1 FROM batches WHERE assignment_id = ?", (assignment_id,)).fetchone():
            raise HTTPException(409, "Delete the scans before replacing the blank test")
    key = f"a{assignment_id}_{secrets.token_hex(4)}"
    try:
        count = save_upload(file, key)
    except Exception:
        remove_files(key)
        raise
    with db.session() as conn:
        conn.execute("DELETE FROM problems WHERE assignment_id = ?", (assignment_id,))
        conn.execute(
            "UPDATE assignments SET blank_key = ?, blank_pages = ?, cover_page = 0 WHERE id = ?",
            (key, count, assignment_id),
        )
        conn.executemany(
            "INSERT INTO problems (assignment_id, page, label, max_points) VALUES (?, ?, ?, ?)",
            [(assignment_id, i, str(i), DEFAULT_POINTS) for i in range(1, count)],
        )
        detail = assignment_detail(conn, assignment_id)
    remove_files(a["blank_key"])
    return detail


@app.put("/api/assignments/{assignment_id}/pages/{page}")
def set_page(assignment_id: int, page: int, body: PageIn):
    """Set a blank page to be the cover, a problem (with label and points), or nothing."""
    with db.session() as conn:
        a = assignment_row(conn, assignment_id)
        if not 0 <= page < a["blank_pages"]:
            raise HTTPException(404, "No such page")
        if body.kind == "cover":
            conn.execute("DELETE FROM problems WHERE assignment_id = ? AND page = ?", (assignment_id, page))
            conn.execute("UPDATE assignments SET cover_page = ? WHERE id = ?", (page, assignment_id))
        elif page == a["cover_page"]:
            raise HTTPException(400, "Choose another cover page first")
        elif body.kind == "none":
            conn.execute("DELETE FROM problems WHERE assignment_id = ? AND page = ?", (assignment_id, page))
        else:
            existing = conn.execute(
                "SELECT label, max_points FROM problems WHERE assignment_id = ? AND page = ?", (assignment_id, page)
            ).fetchone()
            label = (body.label or "").strip() or (existing["label"] if existing else next_label(conn, assignment_id))
            points = body.max_points if body.max_points is not None else (
                existing["max_points"] if existing else DEFAULT_POINTS
            )
            conn.execute(
                """INSERT INTO problems (assignment_id, page, label, max_points) VALUES (?, ?, ?, ?)
                   ON CONFLICT (assignment_id, page) DO UPDATE SET label = excluded.label,
                   max_points = excluded.max_points""",
                (assignment_id, page, label, points),
            )
        return assignment_detail(conn, assignment_id)


# ---------------------------------------------------------------- batches


@app.post("/api/assignments/{assignment_id}/batches")
def upload_batch(assignment_id: int, file: UploadFile = File(...), pages_per_test: int | None = Form(None)):
    with db.session() as conn:
        a = assignment_row(conn, assignment_id)
    if not a["blank_pages"]:
        raise HTTPException(400, "Upload the blank test first")
    ppt = pages_per_test or a["blank_pages"]
    if ppt < 1:
        raise HTTPException(400, "Pages per test must be at least 1")
    key = f"b{assignment_id}_{secrets.token_hex(6)}"
    try:
        count = save_upload(file, key)
    except Exception:
        remove_files(key)
        raise
    ranges, _ = pdf.split_ranges(count, ppt)
    with db.session() as conn:
        cur = conn.execute(
            """INSERT INTO batches (assignment_id, key, filename, page_count, pages_per_test, page_map)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                assignment_id,
                key,
                file.filename or "scan.pdf",
                count,
                ppt,
                json.dumps(pdf.default_page_map(a["blank_pages"], ppt)),
            ),
        )
        db.create_submissions(conn, cur.lastrowid, ranges)
        return assignment_detail(conn, assignment_id)


@app.patch("/api/batches/{batch_id}")
def patch_batch(batch_id: int, body: BatchPatch):
    """Change pages per test (re-splits the file, discarding matches and grading), the page mapping,
    or whether the page order has been checked."""
    with db.session() as conn:
        b = fetch(conn, "SELECT * FROM batches WHERE id = ?", (batch_id,))
        a = assignment_row(conn, b["assignment_id"])
        ppt = b["pages_per_test"]
        if body.pages_per_test is not None and body.pages_per_test != ppt:
            ppt = body.pages_per_test
            ranges, _ = pdf.split_ranges(b["page_count"], ppt)
            conn.execute("DELETE FROM submissions WHERE batch_id = ?", (batch_id,))
            db.create_submissions(conn, batch_id, ranges)
            # Re-splitting puts the pages back in scan order, so the order needs checking again.
            conn.execute(
                "UPDATE batches SET pages_per_test = ?, page_map = ?, checked = 0 WHERE id = ?",
                (ppt, json.dumps(pdf.default_page_map(a["blank_pages"], ppt)), batch_id),
            )
        if body.page_map is not None:
            if len(body.page_map) != a["blank_pages"] or not all(0 <= v < ppt for v in body.page_map):
                raise HTTPException(400, "Invalid page mapping")
            conn.execute("UPDATE batches SET page_map = ? WHERE id = ?", (json.dumps(body.page_map), batch_id))
        if body.checked is not None:
            conn.execute("UPDATE batches SET checked = ? WHERE id = ?", (int(body.checked), batch_id))
        return assignment_detail(conn, b["assignment_id"])


@app.delete("/api/batches/{batch_id}")
def delete_batch(batch_id: int):
    with db.session() as conn:
        b = fetch(conn, "SELECT id, key, assignment_id FROM batches WHERE id = ?", (batch_id,))
        conn.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
        detail = assignment_detail(conn, b["assignment_id"])
    remove_files(b["key"])
    return detail


# ---------------------------------------------------------------- page order


def slot_labels(conn: sqlite3.Connection, a: sqlite3.Row, b: sqlite3.Row) -> list[str]:
    """What each page slot of a test is expected to hold, from the blank page mapping."""
    labels: list[list[str]] = [[] for _ in range(b["pages_per_test"])]
    by_page = {p["page"]: p["label"] for p in db.problems(conn, a["id"])}
    for blank_page, offset in enumerate(json.loads(b["page_map"])):
        if not 0 <= offset < b["pages_per_test"]:
            continue
        if blank_page == a["cover_page"]:
            labels[offset].append("Cover")
        elif blank_page in by_page:
            labels[offset].append(f"Problem {by_page[blank_page]}")
    return [", ".join(names) for names in labels]


def batch_state(conn: sqlite3.Connection, batch_id: int) -> dict:
    """Everything the organize screen needs: the tests of one batch and their pages."""
    b = fetch(conn, "SELECT * FROM batches WHERE id = ?", (batch_id,))
    a = assignment_row(conn, b["assignment_id"])
    subs = [s for s in db.submissions(conn, b["assignment_id"]) if s["batch_id"] == batch_id]
    return assignment_meta(conn, a) | {
        "batch": batch_info(conn, b, a["blank_pages"]),
        "slots": slot_labels(conn, a, b),
        "tests": [
            {
                "id": s["id"],
                "first_name": s["first_name"],
                "last_name": s["last_name"],
                "pages": s["pages"],
            }
            for s in subs
        ],
    }


@app.get("/api/batches/{batch_id}/pages")
def get_batch_pages(batch_id: int):
    with db.session() as conn:
        return batch_state(conn, batch_id)


@app.put("/api/batches/{batch_id}/pages")
def put_batch_pages(batch_id: int, body: BatchPagesIn):
    """Reorder and flip the pages of a batch. The pages themselves must stay the same ones."""
    with db.session() as conn:
        fetch(conn, "SELECT id FROM batches WHERE id = ?", (batch_id,))
        current = db.batch_pages(conn, batch_id)
        if len(body.pages) != len(current):
            raise HTTPException(400, f"Expected {len(current)} pages, got {len(body.pages)}")
        if sorted(p.scan_page for p in body.pages) != sorted(p["scan_page"] for p in current):
            raise HTTPException(400, "The same pages must be used exactly once each")
        db.set_batch_pages(
            conn, batch_id, [(p.scan_page, int(p.upside_down), int(p.mirrored)) for p in body.pages]
        )
        return batch_state(conn, batch_id)


@app.get("/api/pages/{key}/{index}.png")
def page_image(key: str, index: int, thumb: bool = False):
    if not re.fullmatch(r"[ab]\d+_[0-9a-f]+", key):
        raise HTTPException(404, "Not found")
    path = pdf.page_png(pages_dir(key), index, thumb)
    if not path.is_file():
        raise HTTPException(404, "Not found")
    # Keys are unique per upload, so a page image never changes.
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "max-age=31536000, immutable"})


# ---------------------------------------------------------------- matching


@app.get("/api/assignments/{assignment_id}/submissions")
def list_submissions(assignment_id: int):
    with db.session() as conn:
        a = assignment_row(conn, assignment_id)
        return assignment_meta(conn, a) | {
            "cover_page": a["cover_page"],
            "submissions": db.submissions(conn, assignment_id),
            "students": db.students(conn, a["course_id"]),
        }


@app.put("/api/submissions/{submission_id}/student")
def match_student(submission_id: int, body: MatchIn):
    with db.session() as conn:
        s = submission_assignment(conn, submission_id)
        if body.student_id is not None:
            fetch(
                conn,
                "SELECT id FROM students WHERE id = ? AND course_id = ?",
                (body.student_id, s["course_id"]),
            )
        db.assign_student(conn, submission_id, body.student_id)
        return {"submissions": db.submissions(conn, s["assignment_id"])}


# ---------------------------------------------------------------- grading


def comments_for(conn: sqlite3.Connection, problem_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT c.id, c.problem_id, c.text, c.deduction,
                  (SELECT COUNT(DISTINCT submission_id) FROM annotations a WHERE a.comment_id = c.id) AS uses
           FROM comments c WHERE c.problem_id = ? ORDER BY c.position, c.id""",
        (problem_id,),
    )
    return [dict(r) for r in rows]


@app.get("/api/assignments/{assignment_id}/grading")
def grading_state(assignment_id: int):
    """Everything the grading screen needs in one request."""
    with db.session() as conn:
        a = assignment_row(conn, assignment_id)
        probs = db.problems(conn, assignment_id)
        for p in probs:
            p["comments"] = comments_for(conn, p["id"])
        annotations = conn.execute(
            """SELECT an.id, an.submission_id, an.comment_id, an.page, an.x, an.y FROM annotations an
               JOIN submissions s ON s.id = an.submission_id JOIN batches b ON b.id = s.batch_id
               WHERE b.assignment_id = ? ORDER BY an.id""",
            (assignment_id,),
        )
        return assignment_meta(conn, a) | {
            "problems": probs,
            "submissions": db.submissions(conn, assignment_id, roster_order=True),
            "annotations": [dict(r) for r in annotations],
            "graded": sorted(db.graded_pairs(conn, assignment_id)),
        }


@app.post("/api/problems/{problem_id}/comments")
def create_comment(problem_id: int, body: CommentIn):
    with db.session() as conn:
        fetch(conn, "SELECT id FROM problems WHERE id = ?", (problem_id,))
        (position,) = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM comments WHERE problem_id = ?", (problem_id,)
        ).fetchone()
        cur = conn.execute(
            "INSERT INTO comments (problem_id, text, deduction, position) VALUES (?, ?, ?, ?)",
            (problem_id, body.text, body.deduction, position),
        )
        return {"id": cur.lastrowid, "problem_id": problem_id, "text": body.text, "deduction": body.deduction, "uses": 0}


@app.patch("/api/comments/{comment_id}")
def update_comment(comment_id: int, body: CommentPatch):
    with db.session() as conn:
        c = fetch(conn, "SELECT * FROM comments WHERE id = ?", (comment_id,))
        text = body.text if body.text is not None else c["text"]
        deduction = body.deduction if body.deduction is not None else c["deduction"]
        conn.execute("UPDATE comments SET text = ?, deduction = ? WHERE id = ?", (text, deduction, comment_id))
        return {"ok": True}


@app.delete("/api/comments/{comment_id}")
def delete_comment(comment_id: int):
    with db.session() as conn:
        fetch(conn, "SELECT id FROM comments WHERE id = ?", (comment_id,))
        conn.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
        return {"ok": True}


@app.post("/api/submissions/{submission_id}/annotations")
def create_annotation(submission_id: int, body: AnnotationIn):
    with db.session() as conn:
        s = submission_assignment(conn, submission_id)
        fetch(
            conn,
            "SELECT c.id FROM comments c JOIN problems p ON p.id = c.problem_id WHERE c.id = ? AND p.assignment_id = ?",
            (body.comment_id, s["assignment_id"]),
        )
        if body.page >= s["page_count"]:
            raise HTTPException(400, "No such page")
        cur = conn.execute(
            "INSERT INTO annotations (submission_id, comment_id, page, x, y) VALUES (?, ?, ?, ?, ?)",
            (submission_id, body.comment_id, body.page, body.x, body.y),
        )
        return {"id": cur.lastrowid, "submission_id": submission_id, **body.model_dump()}


@app.patch("/api/annotations/{annotation_id}")
def move_annotation(annotation_id: int, body: AnnotationPatch):
    with db.session() as conn:
        an = fetch(conn, "SELECT * FROM annotations WHERE id = ?", (annotation_id,))
        s = submission_assignment(conn, an["submission_id"])
        page = body.page if body.page is not None else an["page"]
        if page >= s["page_count"]:
            raise HTTPException(400, "No such page")
        conn.execute(
            "UPDATE annotations SET page = ?, x = ?, y = ? WHERE id = ?",
            (page, body.x if body.x is not None else an["x"], body.y if body.y is not None else an["y"], annotation_id),
        )
        return {"ok": True}


@app.delete("/api/annotations/{annotation_id}")
def delete_annotation(annotation_id: int):
    with db.session() as conn:
        conn.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))
        return {"ok": True}


@app.put("/api/submissions/{submission_id}/graded/{problem_id}")
def set_graded(submission_id: int, problem_id: int, body: GradedIn):
    with db.session() as conn:
        s = submission_assignment(conn, submission_id)
        fetch(
            conn, "SELECT id FROM problems WHERE id = ? AND assignment_id = ?", (problem_id, s["assignment_id"])
        )
        if body.graded:
            conn.execute("INSERT OR IGNORE INTO graded VALUES (?, ?)", (submission_id, problem_id))
        else:
            conn.execute("DELETE FROM graded WHERE submission_id = ? AND problem_id = ?", (submission_id, problem_id))
        return {"ok": True}


# ---------------------------------------------------------------- results


@app.get("/api/assignments/{assignment_id}/results")
def get_results(assignment_id: int):
    with db.session() as conn:
        a = assignment_row(conn, assignment_id)
        return assignment_meta(conn, a) | export.results(conn, assignment_id)


@app.get("/api/assignments/{assignment_id}/results.csv")
def get_results_csv(assignment_id: int):
    with db.session() as conn:
        a = assignment_row(conn, assignment_id)
        body = export.results_csv(export.results(conn, assignment_id))
    filename = (export.safe_filename(a["name"]) or "results") + ".csv"
    return Response(
        body, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.post("/api/assignments/{assignment_id}/export")
def export_pdfs(assignment_id: int):
    with db.session() as conn:
        assignment_row(conn, assignment_id)
        files, _ = export.export_pdfs(conn, assignment_id)
    return {"count": len(files), "files": [f.name for f in files], "zip": f"/api/assignments/{assignment_id}/export.zip"}


@app.get("/api/assignments/{assignment_id}/export.zip")
def download_export(assignment_id: int):
    with db.session() as conn:
        a = assignment_row(conn, assignment_id)
    folder = export.safe_filename(a["name"]) or f"assignment_{assignment_id}"
    path = db.data_dir() / "exports" / f"{folder}.zip"
    if not path.is_file():
        raise HTTPException(404, "Export first")
    return FileResponse(path, media_type="application/zip", filename=path.name)


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")


# ---------------------------------------------------------------- entry point


def run() -> None:
    parser = argparse.ArgumentParser(prog="grader")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port)
