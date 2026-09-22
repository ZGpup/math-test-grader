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


# ---------------------------------------------------------------- which key page a problem opens at


def key_pages(detail: dict) -> list[tuple[str, int, int | None]]:
    return [(p["label"], p["key_page"], p["key_at"]) for p in detail["problems"]]


def test_a_key_page_is_chosen_per_problem(client, fixtures):
    """The key that needed a second page for the second problem: p. 1 and p. 2 of the test are
    one page, but their answers are on key pages 1 and 2."""
    aid = new_assignment(client, fixtures)
    detail = client.get(f"/api/assignments/{aid}").json()
    for p in detail["problems"][2:]:  # keep problems 1 and 2
        detail = client.delete(f"/api/problems/{p['id']}").json()
    one, two = detail["problems"]
    detail = client.patch(f"/api/problems/{two['id']}", json={"page": one["page"]}).json()
    assert [p["page"] for p in detail["problems"]] == [1, 1], "both printed on one page of the test"

    detail = upload(client, f"/api/assignments/{aid}/answer_key", fixtures["key"])
    assert key_pages(detail) == [("1", -1, 1), ("2", -1, 1)], "following the test, both open at p. 2"

    detail = client.patch(f"/api/problems/{two['id']}", json={"key_page": 2}).json()
    assert key_pages(detail) == [("1", -1, 1), ("2", 2, 2)], "the second answer is a page further on"
    assert key_pages(client.get(f"/api/assignments/{aid}/grading").json()) == [("1", -1, 1), ("2", 2, 2)]

    # Back to following the test.
    detail = client.patch(f"/api/problems/{two['id']}", json={"key_page": -1}).json()
    assert key_pages(detail) == [("1", -1, 1), ("2", -1, 1)]


def test_a_chosen_key_page_survives_everything_else_about_the_problem(client, fixtures):
    aid = new_assignment(client, fixtures)
    upload(client, f"/api/assignments/{aid}/answer_key", fixtures["key"])
    pid = client.get(f"/api/assignments/{aid}").json()["problems"][0]["id"]
    client.patch(f"/api/problems/{pid}", json={"key_page": 4})
    detail = client.patch(f"/api/problems/{pid}", json={"label": "1a", "max_points": 3, "page": 2}).json()
    moved = next(p for p in detail["problems"] if p["id"] == pid)
    assert (moved["label"], moved["page"], moved["key_page"], moved["key_at"]) == ("1a", 2, 4, 4), (
        "renaming and moving it leaves its key page alone"
    )


def test_without_a_key_a_problem_opens_at_nothing(client, fixtures):
    aid = new_assignment(client, fixtures)
    assert key_pages(client.get(f"/api/assignments/{aid}").json())[0] == ("1", -1, None)
    pid = client.get(f"/api/assignments/{aid}").json()["problems"][0]["id"]
    assert client.patch(f"/api/problems/{pid}", json={"key_page": 0}).status_code == 404


def test_a_key_page_must_be_one_the_key_has(client, fixtures):
    aid = new_assignment(client, fixtures)
    upload(client, f"/api/assignments/{aid}/answer_key", fixtures["key"])  # 5 pages
    pid = client.get(f"/api/assignments/{aid}").json()["problems"][0]["id"]
    assert client.patch(f"/api/problems/{pid}", json={"key_page": 5}).status_code == 404
    assert client.patch(f"/api/problems/{pid}", json={"key_page": -2}).status_code == 422
    assert client.patch(f"/api/problems/{pid}", json={"key_page": 4}).status_code == 200
    assert client.post(f"/api/assignments/{aid}/problems", json={"page": 0, "key_page": 9}).status_code == 404


def test_a_short_key_clamps_to_its_last_page(client, fixtures, tmp_path):
    """A one-page key for a five-page test: every problem opens at the page there is."""
    import pymupdf

    aid = new_assignment(client, fixtures)
    one_page = tmp_path / "one_page_key.pdf"
    with pymupdf.open(fixtures["key"]) as src, pymupdf.open() as out:
        out.insert_pdf(src, from_page=0, to_page=0)
        out.save(one_page)
    detail = upload(client, f"/api/assignments/{aid}/answer_key", one_page)
    assert detail["answer_key_pages"] == 1
    assert [p["key_at"] for p in detail["problems"]] == [0, 0, 0, 0]


def test_a_new_key_puts_the_pages_back_to_following_the_test(client, fixtures):
    """The pages were picked out of the key being replaced, which is a different document."""
    aid = new_assignment(client, fixtures)
    upload(client, f"/api/assignments/{aid}/answer_key", fixtures["key"])
    pid = client.get(f"/api/assignments/{aid}").json()["problems"][0]["id"]
    assert client.patch(f"/api/problems/{pid}", json={"key_page": 3}).json()["problems"][0]["key_page"] == 3
    detail = upload(client, f"/api/assignments/{aid}/answer_key", fixtures["key"])
    assert [p["key_page"] for p in detail["problems"]] == [-1, -1, -1, -1]

    client.patch(f"/api/problems/{pid}", json={"key_page": 3})
    detail = client.delete(f"/api/assignments/{aid}/answer_key").json()
    assert [p["key_page"] for p in detail["problems"]] == [-1, -1, -1, -1]


def test_resolving_the_key_page():
    from app import pdf

    assert pdf.answer_key_page(-1, 2, 5) == 2, "no page chosen: the one sitting where the problem is"
    assert pdf.answer_key_page(4, 2, 5) == 4, "the page chosen for it"
    assert pdf.answer_key_page(-1, 9, 5) == 4, "clamped to the key's last page"
    assert pdf.answer_key_page(-1, 0, 0) is None, "no key, nothing to open"
    assert pdf.answer_key_page(3, 0, 0) is None


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
