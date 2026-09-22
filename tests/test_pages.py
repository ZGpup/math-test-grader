"""Any blank page holds any number of problems, the cover page included, and a test may have
no cover page at all. This is the quiz with a name at the top and two problems below it."""

import pymupdf

from app import db, pdf
from conftest import add_comment, place, upload

# The problems table as it was written when a page could hold exactly one problem.
OLD_PROBLEMS = """
CREATE TABLE old (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    page INTEGER NOT NULL,
    label TEXT NOT NULL,
    max_points REAL NOT NULL,
    UNIQUE (assignment_id, page)
);
INSERT INTO old SELECT id, assignment_id, page, label, max_points FROM problems;
DROP TABLE problems;
ALTER TABLE old RENAME TO problems;
"""


def setup(client, fixtures) -> int:
    """A course with one student and an assignment with the 5-page blank test."""
    cid = client.post("/api/courses", json={"name": "C"}).json()["id"]
    client.put(f"/api/courses/{cid}/roster", json={"text": "Ann One"})
    aid = client.post(f"/api/courses/{cid}/assignments", json={"name": "Quiz"}).json()["id"]
    upload(client, f"/api/assignments/{aid}/blank", fixtures["blank"])
    return aid


def roles(detail: dict) -> list[str]:
    """What each blank page carries, page by page."""
    return [
        ", ".join((["Cover"] if p["cover"] else []) + [pr["label"] for pr in p["problems"]]) or "-"
        for p in detail["pages"]
    ]


def labels(detail: dict) -> list[str]:
    return [p["label"] for p in detail["problems"]]


def clear_problems(client, aid: int) -> dict:
    detail = client.get(f"/api/assignments/{aid}").json()
    for p in detail["problems"]:
        detail = client.delete(f"/api/problems/{p['id']}").json()
    return detail


# ---------------------------------------------------------------- several on a page


def test_cover_page_carries_problems_too(client, fixtures):
    """A quiz with the name on top and two problems under it: one page, and it is the cover."""
    aid = setup(client, fixtures)
    detail = clear_problems(client, aid)
    assert roles(detail) == ["Cover", "-", "-", "-", "-"]
    client.post(f"/api/assignments/{aid}/problems", json={"page": 0, "label": "1", "max_points": 6})
    detail = client.post(f"/api/assignments/{aid}/problems", json={"page": 0, "label": "2", "max_points": 4}).json()
    assert roles(detail) == ["Cover, 1, 2", "-", "-", "-", "-"]
    assert detail["total_points"] == 10
    assert detail["pages"][0]["cover"] is True

    # Both problems are graded on the same page of the scan, and both count in the results.
    upload(client, f"/api/assignments/{aid}/batches", fixtures["scans"])
    subs = client.get(f"/api/assignments/{aid}/submissions").json()
    client.put(f"/api/submissions/{subs['submissions'][0]['id']}/student", json={"student_id": subs["students"][0]["id"]})
    state = client.get(f"/api/assignments/{aid}/grading").json()
    assert [p["page"] for p in state["problems"]] == [0, 0]
    sid = state["submissions"][0]["id"]
    place(client, sid, add_comment(client, state["problems"][1]["id"], "Sign error", 1.5))
    results = client.get(f"/api/assignments/{aid}/results").json()
    assert [p["label"] for p in results["problems"]] == ["1", "2"]
    assert [c["points"] for c in results["rows"][0]["cells"]] == [6, 2.5]


def test_problems_keep_their_order_on_a_page(client, fixtures):
    aid = setup(client, fixtures)
    detail = client.post(f"/api/assignments/{aid}/problems", json={"page": 1, "label": "1b"}).json()
    assert labels(detail) == ["1", "1b", "2", "3", "4"], "page order, then the order on the page"
    p1b = detail["problems"][1]["id"]

    detail = client.patch(f"/api/problems/{p1b}", json={"position": 0}).json()
    assert labels(detail) == ["1b", "1", "2", "3", "4"]
    assert [p["position"] for p in detail["problems"][:2]] == [0, 1]

    # Moved to another page, it joins the end of that one.
    detail = client.patch(f"/api/problems/{p1b}", json={"page": 3}).json()
    assert roles(detail) == ["Cover", "1", "2", "3, 1b", "4"]
    assert labels(detail) == ["1", "2", "3", "1b", "4"]


def test_a_new_problem_is_numbered_from_the_labels_in_use(client, fixtures):
    aid = setup(client, fixtures)
    detail = clear_problems(client, aid)
    for page, label in ((1, "1"), (1, "2a"), (1, "2b")):
        detail = client.post(f"/api/assignments/{aid}/problems", json={"page": page, "label": label}).json()
    detail = client.post(f"/api/assignments/{aid}/problems", json={"page": 2}).json()
    assert labels(detail) == ["1", "2a", "2b", "3"]
    assert detail["total_points"] == 40, "a new problem is worth 10 points until it is changed"


def test_the_organize_screen_lists_every_problem_on_a_slot(client, fixtures):
    aid = setup(client, fixtures)
    client.post(f"/api/assignments/{aid}/problems", json={"page": 0, "label": "1a"})
    detail = upload(client, f"/api/assignments/{aid}/batches", fixtures["scans"])
    state = client.get(f"/api/batches/{detail['batches'][0]['id']}/pages").json()
    assert state["slots"] == ["Cover, Problem 1a", "Problem 1", "Problem 2", "Problem 3", "Problem 4"]


def test_deleting_a_problem_takes_its_comments(client, assignment):
    aid = assignment["id"]
    p1, p2 = assignment["problems"][0]["id"], assignment["problems"][1]["id"]
    s1 = assignment["submissions"][0]["id"]
    place(client, s1, add_comment(client, p1, "Sign error", 2))
    kept = add_comment(client, p2, "Arithmetic", 1)
    place(client, s1, kept)

    detail = client.delete(f"/api/problems/{p1}").json()
    assert labels(detail) == ["2", "3", "4"]
    state = client.get(f"/api/assignments/{aid}/grading").json()
    assert [a["comment_id"] for a in state["annotations"]] == [kept]
    assert [c["points"] for c in client.get(f"/api/assignments/{aid}/results").json()["rows"][0]["cells"]] == [9, 10, 10]


# ---------------------------------------------------------------- no cover page


def test_cover_offset_falls_back_to_the_first_page():
    assert pdf.cover_offset([0, 1, 2], 1, 3) == 1
    assert pdf.cover_offset([0, 1, 2], -1, 3) == 0, "no cover page: the first page of the test"
    assert pdf.cover_offset([0, 1, 9], 2, 3) == 2, "clamped to the last page of the test"
    assert pdf.cover_offset([], 0, 3) == 0


def test_a_test_can_have_no_cover_page(client, fixtures):
    """The blank test doubles as a scan, so the page the score box lands on can be told apart."""
    aid = setup(client, fixtures)
    batch = upload(client, f"/api/assignments/{aid}/batches", fixtures["blank"])["batches"][0]
    # The name is on the second page of this test, wherever the score box goes.
    client.patch(f"/api/batches/{batch['id']}", json={"page_map": [1, 1, 2, 3, 4]})
    subs = client.get(f"/api/assignments/{aid}/submissions").json()
    client.put(f"/api/submissions/{subs['submissions'][0]['id']}/student", json={"student_id": subs["students"][0]["id"]})

    client.post(f"/api/assignments/{aid}/export")
    with pymupdf.open(db.data_dir() / "exports" / "Quiz" / "One_Ann.pdf") as doc:
        assert "Total" in doc[1].get_text(), "the mapped cover page"

    detail = client.put(f"/api/assignments/{aid}/cover", json={"page": None}).json()
    assert detail["cover_page"] == -1
    assert not any(p["cover"] for p in detail["pages"])
    assert client.get(f"/api/assignments/{aid}/submissions").json()["cover_page"] == -1

    client.post(f"/api/assignments/{aid}/export")
    with pymupdf.open(db.data_dir() / "exports" / "Quiz" / "One_Ann.pdf") as doc:
        assert "Total" in doc[0].get_text(), "with no cover page it goes on the first page"
        assert "Total" not in doc[1].get_text()


def test_the_cover_can_come_back(client, fixtures):
    aid = setup(client, fixtures)
    client.put(f"/api/assignments/{aid}/cover", json={"page": None})
    detail = client.put(f"/api/assignments/{aid}/cover", json={"page": 2}).json()
    assert (detail["cover_page"], roles(detail)[2]) == (2, "Cover, 2")


def test_pages_outside_the_blank_test_are_refused(client, fixtures):
    aid = setup(client, fixtures)
    assert client.post(f"/api/assignments/{aid}/problems", json={"page": 5}).status_code == 404
    assert client.put(f"/api/assignments/{aid}/cover", json={"page": 5}).status_code == 404
    pid = client.get(f"/api/assignments/{aid}").json()["problems"][0]["id"]
    assert client.patch(f"/api/problems/{pid}", json={"page": 5}).status_code == 404
    assert client.patch("/api/problems/9999", json={"label": "x"}).status_code == 404
    assert client.delete("/api/problems/9999").status_code == 404


# ---------------------------------------------------------------- migration


def test_an_older_database_keeps_its_work_and_gains_the_freedom(client, assignment):
    """Dropping the one-problem-per-page rule rebuilds the problems table, which must not take
    the comments and grades pointing at those problems down with it."""
    aid = assignment["id"]
    p1 = assignment["problems"][0]["id"]
    s1 = assignment["submissions"][0]["id"]
    place(client, s1, add_comment(client, p1, "Sign error", 2))
    client.put(f"/api/submissions/{s1}/graded/{p1}", json={"graded": True})

    with db.session() as conn:
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")  # the rebuild must not cascade into comments
        conn.executescript(OLD_PROBLEMS)
    db._initialized.clear()  # so the next connection migrates again

    detail = client.get(f"/api/assignments/{aid}").json()
    assert labels(detail) == ["1", "2", "3", "4"]
    assert [p["position"] for p in detail["problems"]] == [0, 0, 0, 0]
    assert detail["progress"]["graded"] == 1
    assert client.get(f"/api/assignments/{aid}/results").json()["rows"][0]["cells"][0]["points"] == 8

    detail = client.post(f"/api/assignments/{aid}/problems", json={"page": 1, "label": "1b"}).json()
    assert roles(detail)[1] == "1, 1b"


def test_a_database_from_before_the_key_could_be_mapped(client, assignment):
    """Only the column is missing there, so it is added in place rather than rebuilt."""
    aid = assignment["id"]
    p1 = assignment["problems"][0]["id"]
    s1 = assignment["submissions"][0]["id"]
    place(client, s1, add_comment(client, p1, "Sign error", 2))
    with db.session() as conn:
        conn.execute("ALTER TABLE problems DROP COLUMN key_page")
        assert "key_page" not in {r["name"] for r in conn.execute("PRAGMA table_info(problems)")}
    db._initialized.clear()  # so the next connection migrates again

    detail = client.get(f"/api/assignments/{aid}").json()
    assert labels(detail) == ["1", "2", "3", "4"]
    assert [p["id"] for p in detail["problems"]][0] == p1
    assert [p["key_page"] for p in detail["problems"]] == [-1, -1, -1, -1], "following the test, as before"
    assert client.get(f"/api/assignments/{aid}/results").json()["rows"][0]["cells"][0]["points"] == 8
