import sqlite3

import pytest

from app import db
from conftest import add_comment, place


def scores(client, aid: int) -> list[list[float]]:
    rows = client.get(f"/api/assignments/{aid}/results").json()["rows"]
    return [[c["points"] for c in row["cells"]] for row in rows]


def test_problem_score_clamps_at_zero():
    assert db.problem_score(10, []) == 10
    assert db.problem_score(10, [2, 0, 1.5]) == 6.5
    assert db.problem_score(10, [6, 7]) == 0


def test_no_comments_is_full_credit(client, assignment):
    assert scores(client, assignment["id"]) == [[10, 10, 10, 10]] * 3


def test_deductions_and_propagation(client, assignment):
    aid = assignment["id"]
    p1, p2 = assignment["problems"][0]["id"], assignment["problems"][1]["id"]
    s1, s2, s3 = [s["id"] for s in assignment["submissions"]]

    sign = add_comment(client, p1, r"Sign error in $2x = 8$", 2)
    note = add_comment(client, p1, "Nice work")  # deduction defaults to 0
    place(client, s1, sign)
    place(client, s1, note)
    place(client, s2, sign, page=1)  # placements on other pages still count
    place(client, s3, note)
    assert scores(client, aid) == [[8, 10, 10, 10], [8, 10, 10, 10], [10, 10, 10, 10]]

    # Editing a rubric comment updates every submission that uses it.
    assert client.patch(f"/api/comments/{sign}", json={"deduction": 3.5}).status_code == 200
    assert scores(client, aid) == [[6.5, 10, 10, 10], [6.5, 10, 10, 10], [10, 10, 10, 10]]

    # Deductions beyond the maximum clamp at 0.
    big = add_comment(client, p1, "Missing", 9)
    place(client, s1, big)
    assert scores(client, aid)[0] == [0, 10, 10, 10]

    # A comment placed twice on one submission deducts twice.
    place(client, s2, sign, x=0.1, y=0.9)
    assert scores(client, aid)[1] == [3, 10, 10, 10]

    # Comments only affect their own problem.
    other = add_comment(client, p2, "Arithmetic", 4)
    place(client, s3, other)
    assert scores(client, aid)[2] == [10, 6, 10, 10]

    # Deleting a comment removes its placements.
    assert client.delete(f"/api/comments/{sign}").status_code == 200
    state = client.get(f"/api/assignments/{aid}/grading").json()
    assert all(a["comment_id"] != sign for a in state["annotations"])
    assert scores(client, aid) == [[1, 10, 10, 10], [10, 10, 10, 10], [10, 6, 10, 10]]


def test_invalid_comments_rejected(client, assignment):
    p1 = assignment["problems"][0]["id"]
    assert client.post(f"/api/problems/{p1}/comments", json={"text": "  "}).status_code == 422
    assert client.post(f"/api/problems/{p1}/comments", json={"text": "x", "deduction": "high"}).status_code == 422


def test_bonus_comments_add_points(client, assignment):
    aid = assignment["id"]
    p1 = assignment["problems"][0]["id"]
    s1 = assignment["submissions"][0]["id"]

    bonus = add_comment(client, p1, "Elegant proof", -2)
    place(client, s1, bonus)
    assert scores(client, aid)[0][0] == 12  # a problem can go above its own points

    place(client, s1, bonus, page=1)  # every placement counts, bonuses too
    assert scores(client, aid)[0][0] == 14

    # Points off and points on cancel out, and the floor is still zero.
    place(client, s1, add_comment(client, p1, "Wrong method", 20))
    assert scores(client, aid)[0][0] == 0

    # A deduction can be turned into a bonus after the fact.
    assert client.patch(f"/api/comments/{bonus}", json={"deduction": 1}).status_code == 200
    assert scores(client, aid)[0][0] == 0


def test_bonus_can_push_a_grade_over_100(client, assignment):
    aid = assignment["id"]
    p1 = assignment["problems"][0]["id"]
    s1 = assignment["submissions"][0]["id"]
    place(client, s1, add_comment(client, p1, "Extra credit", -4))
    row = client.get(f"/api/assignments/{aid}/results").json()["rows"][0]
    assert (row["total"], row["percent"]) == (44, 110.0)


def test_migration_allows_bonuses_without_losing_work(tmp_path):
    """An older database checked that a deduction was never negative. Dropping that check
    rebuilds the comments table, which must not take the placed comments down with it."""
    old_schema = db.SCHEMA.replace(
        "deduction REAL NOT NULL DEFAULT 0,", "deduction REAL NOT NULL DEFAULT 0 CHECK (deduction >= 0),"
    )
    assert "CHECK (deduction >= 0)" in old_schema
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(old_schema)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """INSERT INTO courses (id, name) VALUES (1, 'C');
           INSERT INTO assignments (id, course_id, name, blank_pages) VALUES (1, 1, 'A', 2);
           INSERT INTO problems (id, assignment_id, page, label, max_points) VALUES (1, 1, 1, '1', 10);
           INSERT INTO batches (id, assignment_id, key, filename, page_count, pages_per_test, page_map)
               VALUES (1, 1, 'b1_x', 's.pdf', 2, 2, '[0, 1]');
           INSERT INTO submissions (id, batch_id, first_page, page_count) VALUES (1, 1, 0, 2);
           INSERT INTO comments (id, problem_id, text, deduction, position) VALUES (1, 1, 'Sign error', 2, 0);
           INSERT INTO annotations (submission_id, comment_id, page, x, y) VALUES (1, 1, 0, 0.5, 0.5);"""
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO comments (problem_id, text, deduction, position) VALUES (1, 'Bonus', -2, 1)")

    db.migrate(conn)
    conn.commit()

    conn.execute("INSERT INTO comments (problem_id, text, deduction, position) VALUES (1, 'Bonus', -2, 1)")
    assert conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0] == 1
    assert dict(conn.execute("SELECT text, deduction FROM comments WHERE id = 1").fetchone()) == {
        "text": "Sign error",
        "deduction": 2,
    }
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?", ("idx_comments_problem",)).fetchone()
    assert db.submissions(conn, 1)[0]["pages"] == [
        {"scan_page": 0, "upside_down": 0, "mirrored": 0},
        {"scan_page": 1, "upside_down": 0, "mirrored": 0},
    ]
    conn.close()


def test_graded_status(client, assignment):
    aid = assignment["id"]
    p1 = assignment["problems"][0]["id"]
    s1 = assignment["submissions"][0]["id"]
    assert client.put(f"/api/submissions/{s1}/graded/{p1}", json={"graded": True}).status_code == 200
    state = client.get(f"/api/assignments/{aid}/grading").json()
    assert state["graded"] == [[s1, p1]]
    progress = client.get(f"/api/assignments/{aid}").json()["progress"]
    assert (progress["graded"], progress["gradable"], progress["matched"]) == (1, 12, 3)
