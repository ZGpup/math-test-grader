"""Anonymous grading: the grading screen gets no names, and the tests come in scan order."""

from app import db
from conftest import add_comment, place

# The assignments table as it was written before there was a choice about names.
OLD_ASSIGNMENTS = """
CREATE TABLE old (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    blank_key TEXT,
    blank_pages INTEGER NOT NULL DEFAULT 0,
    cover_page INTEGER NOT NULL DEFAULT 0,
    answer_key TEXT,
    answer_key_pages INTEGER NOT NULL DEFAULT 0
);
INSERT INTO old SELECT id, course_id, name, blank_key, blank_pages, cover_page, answer_key, answer_key_pages
    FROM assignments;
DROP TABLE assignments;
ALTER TABLE old RENAME TO assignments;
"""


def grading(client, aid: int) -> dict:
    r = client.get(f"/api/assignments/{aid}/grading")
    assert r.status_code == 200, r.text
    return r.json()


def set_anonymous(client, aid: int, value: bool) -> dict:
    r = client.patch(f"/api/assignments/{aid}", json={"anonymous": value})
    assert r.status_code == 200, r.text
    return client.get(f"/api/assignments/{aid}").json()


def names(state: dict) -> list[tuple]:
    return [(s["first_name"], s["last_name"]) for s in state["submissions"]]


def test_off_by_default(client, assignment):
    aid = assignment["id"]
    assert client.get(f"/api/assignments/{aid}").json()["anonymous"] is False
    state = grading(client, aid)
    assert state["anonymous"] is False
    assert all(first and last for first, last in names(state))


def test_hides_the_names(client, assignment):
    aid = assignment["id"]
    assert set_anonymous(client, aid, True)["anonymous"] is True
    state = grading(client, aid)
    assert state["anonymous"] is True
    assert names(state) == [(None, None)] * 3, "no name reaches the grading screen"
    assert [s["id"] for s in state["submissions"]] == [s["id"] for s in assignment["submissions"]]


def test_the_setting_is_kept(client, assignment):
    aid = assignment["id"]
    set_anonymous(client, aid, True)
    # Renaming the assignment leaves it alone, and it survives being turned back off.
    assert client.patch(f"/api/assignments/{aid}", json={"name": "Quiz 4"}).status_code == 200
    detail = client.get(f"/api/assignments/{aid}").json()
    assert (detail["name"], detail["anonymous"]) == ("Quiz 4", True)
    assert set_anonymous(client, aid, False)["anonymous"] is False
    assert grading(client, aid)["anonymous"] is False


def test_scan_order_instead_of_roster_order(client, assignment):
    aid = assignment["id"]
    subs = client.get(f"/api/assignments/{aid}/submissions").json()
    scan_order = [s["id"] for s in subs["submissions"]]
    # Match the tests to the roster backwards, so the two orders differ.
    for sub, student in zip(subs["submissions"], reversed(subs["students"])):
        assert client.put(f"/api/submissions/{sub['id']}/student", json={"student_id": student["id"]}).status_code == 200

    assert [s["id"] for s in grading(client, aid)["submissions"]] == list(reversed(scan_order))
    set_anonymous(client, aid, True)
    assert [s["id"] for s in grading(client, aid)["submissions"]] == scan_order, "roster order would name them"


def test_unmatched_tests_are_still_marked(client, assignment):
    aid = assignment["id"]
    first = assignment["submissions"][0]["id"]
    assert client.put(f"/api/submissions/{first}/student", json={"student_id": None}).status_code == 200
    set_anonymous(client, aid, True)
    state = grading(client, aid)
    matched = [s["student_id"] is not None for s in state["submissions"]]
    assert matched.count(False) == 1, "a test with no name still says so, its grade would go nowhere"


def test_an_older_database_grades_with_names(client, assignment):
    aid = assignment["id"]
    with db.session() as conn:
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")  # the rebuild must not cascade into problems
        conn.executescript(OLD_ASSIGNMENTS)
    db._initialized.clear()  # so the next connection migrates again
    assert client.get(f"/api/assignments/{aid}").json()["anonymous"] is False
    assert all(first and last for first, last in names(grading(client, aid)))


def test_results_and_exports_keep_the_names(client, assignment):
    aid = assignment["id"]
    p1 = assignment["problems"][0]["id"]
    place(client, assignment["submissions"][0]["id"], add_comment(client, p1, "Sign error", 2))
    set_anonymous(client, aid, True)

    rows = client.get(f"/api/assignments/{aid}/results").json()["rows"]
    assert all(r["last_name"] for r in rows)
    csv = client.get(f"/api/assignments/{aid}/results.csv").text
    assert rows[0]["last_name"] in csv
