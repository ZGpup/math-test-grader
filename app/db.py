"""SQLite storage, roster parsing, and score computation."""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    roster_text TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    position INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    blank_key TEXT,                        -- uploads/<key>.pdf, pages/<key>/
    blank_pages INTEGER NOT NULL DEFAULT 0,
    cover_page INTEGER NOT NULL DEFAULT 0, -- 0-based blank page index
    answer_key TEXT,                       -- worked solutions, shown beside a student's work
    answer_key_pages INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS problems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    page INTEGER NOT NULL,                 -- 0-based blank page index
    label TEXT NOT NULL,
    max_points REAL NOT NULL,
    UNIQUE (assignment_id, page)
);
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    filename TEXT NOT NULL,
    page_count INTEGER NOT NULL,
    pages_per_test INTEGER NOT NULL,
    page_map TEXT NOT NULL,                -- JSON: blank page index -> scan page offset in a test
    checked INTEGER NOT NULL DEFAULT 0     -- the page order has been confirmed on the organize screen
);
CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    first_page INTEGER NOT NULL,           -- where this test starts in scan order (sort key and size)
    page_count INTEGER NOT NULL,
    student_id INTEGER REFERENCES students(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS submission_pages (
    submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,             -- page offset within the test
    scan_page INTEGER NOT NULL,            -- 0-based page index in the scan PDF
    upside_down INTEGER NOT NULL DEFAULT 0,
    mirrored INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (submission_id, position)
);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_id INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    deduction REAL NOT NULL DEFAULT 0,     -- points off; negative adds points (bonus)
    position INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    comment_id INTEGER NOT NULL REFERENCES comments(id) ON DELETE CASCADE,
    page INTEGER NOT NULL,                 -- page offset within the submission
    x REAL NOT NULL,                       -- top-left corner, 0-1 page coordinates
    y REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS graded (
    submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    problem_id INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    PRIMARY KEY (submission_id, problem_id)
);
CREATE INDEX IF NOT EXISTS idx_students_course ON students(course_id);
CREATE INDEX IF NOT EXISTS idx_submission_pages ON submission_pages(submission_id);
CREATE INDEX IF NOT EXISTS idx_submissions_batch ON submissions(batch_id);
CREATE INDEX IF NOT EXISTS idx_comments_problem ON comments(problem_id);
CREATE INDEX IF NOT EXISTS idx_annotations_submission ON annotations(submission_id);
CREATE INDEX IF NOT EXISTS idx_annotations_comment ON annotations(comment_id);
"""

_initialized: set[Path] = set()


def data_dir() -> Path:
    path = Path(os.environ.get("GRADER_DATA", ROOT / "data")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def rendered_pages(key: str) -> int:
    """How many pages an upload has, counted from the page images rendered for it."""
    return sum(1 for p in (data_dir() / "pages" / key).glob("*.png") if p.stem.isdigit())


def connect() -> sqlite3.Connection:
    path = data_dir() / "grader.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path not in _initialized:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        migrate(conn)
        conn.commit()
        _initialized.add(path)
    return conn


COMMENTS_REBUILD = """
CREATE TABLE comments_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_id INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    deduction REAL NOT NULL DEFAULT 0,
    position INTEGER NOT NULL
);
INSERT INTO comments_new (id, problem_id, text, deduction, position)
    SELECT id, problem_id, text, deduction, position FROM comments;
DROP TABLE comments;
ALTER TABLE comments_new RENAME TO comments;
CREATE INDEX IF NOT EXISTS idx_comments_problem ON comments(problem_id);
"""


def migrate(conn: sqlite3.Connection) -> None:
    """Bring a database written by an older version up to date. A new one needs nothing."""
    comments_sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'comments'").fetchone()
    if comments_sql and "CHECK" in comments_sql[0]:
        # Drop the "deduction >= 0" check so a comment can add points. SQLite only lets you
        # do that by rebuilding the table, and the rebuild must not cascade into annotations.
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.executescript(COMMENTS_REBUILD)
        conn.execute("PRAGMA foreign_keys = ON")
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(assignments)")}
    if "answer_key" not in columns:
        conn.execute("ALTER TABLE assignments ADD COLUMN answer_key TEXT")
    if "answer_key_pages" not in columns:
        conn.execute("ALTER TABLE assignments ADD COLUMN answer_key_pages INTEGER NOT NULL DEFAULT 0")
        # A key uploaded before the count was stored would otherwise read as having no pages.
        for a in conn.execute("SELECT id, answer_key FROM assignments WHERE answer_key IS NOT NULL").fetchall():
            conn.execute(
                "UPDATE assignments SET answer_key_pages = ? WHERE id = ?", (rendered_pages(a["answer_key"]), a["id"])
            )
    if "checked" not in {r["name"] for r in conn.execute("PRAGMA table_info(batches)")}:
        conn.execute("ALTER TABLE batches ADD COLUMN checked INTEGER NOT NULL DEFAULT 0")
        # Scans uploaded before there was a page order screen count as already checked.
        conn.execute("UPDATE batches SET checked = 1")
    missing = conn.execute(
        """SELECT id, first_page, page_count FROM submissions s
           WHERE NOT EXISTS (SELECT 1 FROM submission_pages p WHERE p.submission_id = s.id)"""
    ).fetchall()
    for s in missing:
        set_pages(conn, s["id"], [(s["first_page"] + i, 0, 0) for i in range(s["page_count"])])


@contextmanager
def session():
    """A connection that commits on success, rolls back on error, and always closes."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------- roster


def parse_roster(text: str) -> list[tuple[str, str]]:
    """Parse one student per line as "First Last" (split on the last space) or "Last, First"."""
    names = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        if "," in line:
            last, _, first = line.partition(",")
            names.append((first.strip(), last.strip()))
        elif " " in line:
            first, _, last = line.rpartition(" ")
            names.append((first, last))
        else:
            names.append(("", line))
    return names


def set_roster(conn: sqlite3.Connection, course_id: int, text: str) -> None:
    """Replace a course roster while keeping student ids (and their matches) stable.

    Lines whose name is unchanged keep their student. A line whose name changed
    reuses the student previously on that line (a typo fix). Students no longer
    listed are deleted, which unmatches their submissions.
    """
    names = parse_roster(text)
    existing = conn.execute(
        "SELECT id, first_name, last_name, position FROM students WHERE course_id = ? ORDER BY position, id",
        (course_id,),
    ).fetchall()
    unused = {r["id"]: r for r in existing}
    assigned: list[int | None] = [None] * len(names)
    for i, (first, last) in enumerate(names):
        for sid, r in unused.items():
            if (r["first_name"], r["last_name"]) == (first, last):
                assigned[i] = sid
                del unused[sid]
                break
    by_position = {r["position"]: sid for sid, r in unused.items()}
    for i in range(len(names)):
        sid = by_position.get(i)
        if assigned[i] is None and sid in unused:
            assigned[i] = sid
            del unused[sid]
    for sid in unused:
        conn.execute("DELETE FROM students WHERE id = ?", (sid,))
    for i, ((first, last), sid) in enumerate(zip(names, assigned)):
        if sid is None:
            conn.execute(
                "INSERT INTO students (course_id, first_name, last_name, position) VALUES (?, ?, ?, ?)",
                (course_id, first, last, i),
            )
        else:
            conn.execute(
                "UPDATE students SET first_name = ?, last_name = ?, position = ? WHERE id = ?",
                (first, last, i, sid),
            )
    conn.execute("UPDATE courses SET roster_text = ? WHERE id = ?", (text, course_id))


def students(conn: sqlite3.Connection, course_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, first_name, last_name FROM students WHERE course_id = ? ORDER BY position, id",
        (course_id,),
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- submissions


def create_submissions(conn: sqlite3.Connection, batch_id: int, ranges: list[tuple[int, int]]) -> None:
    """Create the tests of a batch, each holding its scan pages in scan order."""
    for first, count in ranges:
        cur = conn.execute(
            "INSERT INTO submissions (batch_id, first_page, page_count) VALUES (?, ?, ?)", (batch_id, first, count)
        )
        set_pages(conn, cur.lastrowid, [(first + i, 0, 0) for i in range(count)])


def set_pages(conn: sqlite3.Connection, submission_id: int, pages: list[tuple[int, int, int]]) -> None:
    """Replace a test's page list with [(scan_page, upside_down, mirrored), ...]."""
    conn.execute("DELETE FROM submission_pages WHERE submission_id = ?", (submission_id,))
    conn.executemany(
        """INSERT INTO submission_pages (submission_id, position, scan_page, upside_down, mirrored)
           VALUES (?, ?, ?, ?, ?)""",
        [(submission_id, i, page, upside_down, mirrored) for i, (page, upside_down, mirrored) in enumerate(pages)],
    )


def page_lists(conn: sqlite3.Connection, assignment_id: int) -> dict[int, list[dict]]:
    """submission id -> its pages in order."""
    rows = conn.execute(
        """SELECT p.submission_id, p.scan_page, p.upside_down, p.mirrored
           FROM submission_pages p
           JOIN submissions s ON s.id = p.submission_id
           JOIN batches b ON b.id = s.batch_id
           WHERE b.assignment_id = ?
           ORDER BY p.submission_id, p.position""",
        (assignment_id,),
    )
    pages: dict[int, list[dict]] = {}
    for r in rows:
        pages.setdefault(r["submission_id"], []).append(
            {"scan_page": r["scan_page"], "upside_down": r["upside_down"], "mirrored": r["mirrored"]}
        )
    return pages


def batch_pages(conn: sqlite3.Connection, batch_id: int) -> list[dict]:
    """Every page of a batch as one flat list, test by test, in the order they will be graded."""
    rows = conn.execute(
        """SELECT p.scan_page, p.upside_down, p.mirrored FROM submission_pages p
           JOIN submissions s ON s.id = p.submission_id
           WHERE s.batch_id = ? ORDER BY s.first_page, s.id, p.position""",
        (batch_id,),
    )
    return [dict(r) for r in rows]


def set_batch_pages(conn: sqlite3.Connection, batch_id: int, flat: list[tuple[int, int, int]]) -> None:
    """Deal a flat page list back out to the tests of a batch, in order."""
    subs = conn.execute(
        "SELECT id, page_count FROM submissions WHERE batch_id = ? ORDER BY first_page, id", (batch_id,)
    ).fetchall()
    at = 0
    for s in subs:
        set_pages(conn, s["id"], flat[at : at + s["page_count"]])
        at += s["page_count"]


def submissions(conn: sqlite3.Connection, assignment_id: int, roster_order: bool = False) -> list[dict]:
    """All submissions of an assignment, in scan order or roster order (unmatched last)."""
    order = "st.id IS NULL, st.position, b.id, s.first_page" if roster_order else "b.id, s.first_page"
    rows = conn.execute(
        f"""SELECT s.id, s.batch_id, s.first_page, s.page_count, s.student_id,
                   st.first_name, st.last_name, b.key, b.page_map
            FROM submissions s
            JOIN batches b ON b.id = s.batch_id
            LEFT JOIN students st ON st.id = s.student_id
            WHERE b.assignment_id = ?
            ORDER BY {order}""",
        (assignment_id,),
    )
    pages = page_lists(conn, assignment_id)
    result = []
    for r in rows:
        d = dict(r)
        d["page_map"] = json.loads(d["page_map"])
        d["pages"] = pages.get(d["id"], [])
        result.append(d)
    return result


def assign_student(conn: sqlite3.Connection, submission_id: int, student_id: int | None) -> None:
    """Link a submission to a student; a student belongs to at most one submission per assignment."""
    if student_id is not None:
        conn.execute(
            """UPDATE submissions SET student_id = NULL
               WHERE student_id = ? AND batch_id IN (
                   SELECT id FROM batches WHERE assignment_id = (
                       SELECT b.assignment_id FROM submissions s JOIN batches b ON b.id = s.batch_id
                       WHERE s.id = ?))""",
            (student_id, submission_id),
        )
    conn.execute("UPDATE submissions SET student_id = ? WHERE id = ?", (student_id, submission_id))


# ---------------------------------------------------------------- scoring


def problems(conn: sqlite3.Connection, assignment_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, page, label, max_points FROM problems WHERE assignment_id = ? ORDER BY page",
        (assignment_id,),
    )
    return [dict(r) for r in rows]


def problem_score(max_points: float, deductions: list[float]) -> float:
    """A negative deduction is a bonus, so a score can pass max_points. It never goes below 0."""
    return round(max(0.0, max_points - sum(deductions)), 6)


def score_table(conn: sqlite3.Connection, assignment_id: int) -> dict[tuple[int, int], float]:
    """Score for every (submission_id, problem_id) of an assignment.

    Each placement of a comment deducts, so a comment placed three times deducts three times.
    """
    applied: dict[tuple[int, int], list[float]] = {}
    rows = conn.execute(
        """SELECT a.submission_id, c.problem_id, c.deduction
           FROM annotations a
           JOIN comments c ON c.id = a.comment_id
           JOIN problems p ON p.id = c.problem_id
           WHERE p.assignment_id = ?""",
        (assignment_id,),
    )
    for r in rows:
        applied.setdefault((r["submission_id"], r["problem_id"]), []).append(r["deduction"])
    probs = problems(conn, assignment_id)
    return {
        (s["id"], p["id"]): problem_score(p["max_points"], applied.get((s["id"], p["id"]), []))
        for s in submissions(conn, assignment_id)
        for p in probs
    }


def graded_pairs(conn: sqlite3.Connection, assignment_id: int) -> set[tuple[int, int]]:
    rows = conn.execute(
        """SELECT g.submission_id, g.problem_id FROM graded g
           JOIN problems p ON p.id = g.problem_id WHERE p.assignment_id = ?""",
        (assignment_id,),
    )
    return {(r[0], r[1]) for r in rows}


def progress(conn: sqlite3.Connection, assignment_id: int) -> dict:
    (course_id,) = conn.execute("SELECT course_id FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
    subs = submissions(conn, assignment_id)
    n_problems = len(problems(conn, assignment_id))
    return {
        "submissions": len(subs),
        "matched": sum(1 for s in subs if s["student_id"] is not None),
        "students": conn.execute("SELECT COUNT(*) FROM students WHERE course_id = ?", (course_id,)).fetchone()[0],
        "graded": len(graded_pairs(conn, assignment_id)),
        "gradable": len(subs) * n_problems,
    }
