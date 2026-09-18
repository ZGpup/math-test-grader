from app import db
from conftest import upload


def new_assignment(client, fixtures) -> int:
    """A course and an assignment with the 5-page blank test uploaded."""
    cid = client.post("/api/courses", json={"name": "C"}).json()["id"]
    aid = client.post(f"/api/courses/{cid}/assignments", json={"name": "A"}).json()["id"]
    upload(client, f"/api/assignments/{aid}/blank", fixtures["blank"])
    return aid


def key_files(key: str) -> tuple[bool, bool]:
    return (db.data_dir() / "uploads" / f"{key}.pdf").is_file(), (db.data_dir() / "pages" / key).is_dir()


def test_upload_answer_key(client, fixtures):
    aid = new_assignment(client, fixtures)
    detail = client.get(f"/api/assignments/{aid}").json()
    assert (detail["answer_key"], detail["answer_key_pages"]) == (None, 0)

    detail = upload(client, f"/api/assignments/{aid}/answer_key", fixtures["key"])
    key = detail["answer_key"]
    assert (key is not None, detail["answer_key_pages"]) == (True, 5)
    assert key_files(key) == (True, True)
    for i in range(detail["answer_key_pages"]):
        assert client.get(f"/api/pages/{key}/{i}.png").status_code == 200
    assert client.get(f"/api/pages/{key}/5.png").status_code == 404


def test_any_page_count_is_accepted(client, fixtures):
    aid = new_assignment(client, fixtures)  # a 5-page blank test
    detail = upload(client, f"/api/assignments/{aid}/answer_key", fixtures["scans"])
    assert detail["answer_key_pages"] == 15, "a key of any length is the grader's to page through"
    assert client.get(f"/api/pages/{detail['answer_key']}/14.png").status_code == 200


def test_answer_key_without_a_blank_test(client, fixtures):
    cid = client.post("/api/courses", json={"name": "C"}).json()["id"]
    aid = client.post(f"/api/courses/{cid}/assignments", json={"name": "A"}).json()["id"]
    detail = upload(client, f"/api/assignments/{aid}/answer_key", fixtures["key"])
    assert (detail["answer_key_pages"], detail["blank_pages"]) == (5, 0)


def test_the_answer_key_must_be_a_pdf(client):
    cid = client.post("/api/courses", json={"name": "C"}).json()["id"]
    aid = client.post(f"/api/courses/{cid}/assignments", json={"name": "A"}).json()["id"]
    with (db.ROOT / "pyproject.toml").open("rb") as f:
        r = client.post(f"/api/assignments/{aid}/answer_key", files={"file": ("x.pdf", f, "application/pdf")})
    assert r.status_code == 400
    assert r.json()["detail"] == "Not a PDF"
    assert client.get(f"/api/assignments/{aid}").json()["answer_key"] is None
    assert not (db.data_dir() / "pages").exists() or not any(
        p.name.startswith("k") for p in (db.data_dir() / "pages").iterdir()
    ), "the rejected upload leaves nothing behind"


def test_replacing_the_answer_key_drops_the_old_one(client, fixtures):
    aid = new_assignment(client, fixtures)
    first = upload(client, f"/api/assignments/{aid}/answer_key", fixtures["key"])["answer_key"]
    second = upload(client, f"/api/assignments/{aid}/answer_key", fixtures["blank"])["answer_key"]
    assert second != first
    assert key_files(first) == (False, False)
    assert key_files(second) == (True, True)


def test_a_new_blank_test_clears_the_answer_key(client, fixtures):
    aid = new_assignment(client, fixtures)
    key = upload(client, f"/api/assignments/{aid}/answer_key", fixtures["key"])["answer_key"]
    detail = upload(client, f"/api/assignments/{aid}/blank", fixtures["blank"])
    assert (detail["answer_key"], detail["answer_key_pages"]) == (None, 0), "a new test, so a new key"
    assert key_files(key) == (False, False)


def test_delete_answer_key(client, fixtures):
    aid = new_assignment(client, fixtures)
    key = upload(client, f"/api/assignments/{aid}/answer_key", fixtures["key"])["answer_key"]
    detail = client.delete(f"/api/assignments/{aid}/answer_key").json()
    assert (detail["answer_key"], detail["answer_key_pages"]) == (None, 0)
    assert key_files(key) == (False, False)
    assert client.get(f"/api/pages/{key}/0.png").status_code == 404
    assert client.delete(f"/api/assignments/{aid}/answer_key").status_code == 200, "removing it twice is fine"


def test_grading_state_carries_the_answer_key(client, assignment, fixtures):
    aid = assignment["id"]
    assert client.get(f"/api/assignments/{aid}/grading").json()["answer_key"] is None
    key = upload(client, f"/api/assignments/{aid}/answer_key", fixtures["key"])["answer_key"]
    state = client.get(f"/api/assignments/{aid}/grading").json()
    assert (state["answer_key"], state["answer_key_pages"]) == (key, 5)


def test_deleting_the_course_removes_the_answer_key_files(client, fixtures):
    aid = new_assignment(client, fixtures)
    detail = upload(client, f"/api/assignments/{aid}/answer_key", fixtures["key"])
    client.delete(f"/api/courses/{detail['course']['id']}")
    assert key_files(detail["answer_key"]) == (False, False)


def test_deleting_the_assignment_removes_the_answer_key_files(client, fixtures):
    aid = new_assignment(client, fixtures)
    key = upload(client, f"/api/assignments/{aid}/answer_key", fixtures["key"])["answer_key"]
    client.delete(f"/api/assignments/{aid}")
    assert key_files(key) == (False, False)


# The assignments table as the first version with an answer key wrote it: no page count.
OLD_ASSIGNMENTS = """
CREATE TABLE old (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    blank_key TEXT,
    blank_pages INTEGER NOT NULL DEFAULT 0,
    cover_page INTEGER NOT NULL DEFAULT 0,
    answer_key TEXT
);
INSERT INTO old SELECT id, course_id, name, blank_key, blank_pages, cover_page, answer_key FROM assignments;
DROP TABLE assignments;
ALTER TABLE old RENAME TO assignments;
"""


def test_migrate_counts_the_pages_of_a_key_uploaded_before_the_count_was_stored(client, fixtures):
    aid = new_assignment(client, fixtures)
    detail = upload(client, f"/api/assignments/{aid}/answer_key", fixtures["key"])
    assert detail["answer_key_pages"] == 5

    with db.session() as conn:
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")  # the rebuild must not cascade into problems
        conn.executescript(OLD_ASSIGNMENTS)
    db._initialized.clear()  # so the next connection migrates again
    with db.session() as conn:
        pages = conn.execute("SELECT answer_key_pages FROM assignments WHERE id = ?", (aid,)).fetchone()[0]
    assert pages == 5, "the count comes back from the pages already rendered, not as 0"
    assert client.get(f"/api/assignments/{aid}").json()["answer_key_pages"] == 5
