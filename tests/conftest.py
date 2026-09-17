import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import make_fixture  # noqa: E402


@pytest.fixture(scope="session")
def fixtures(tmp_path_factory) -> dict[str, Path]:
    return make_fixture.make_all(tmp_path_factory.mktemp("fixtures"))


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("GRADER_DATA", str(tmp_path / "data"))
    from app.main import app

    return TestClient(app)


def upload(client: TestClient, url: str, path: Path, **form) -> dict:
    with path.open("rb") as f:
        r = client.post(url, files={"file": (path.name, f, "application/pdf")}, data=form)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def assignment(client, fixtures) -> dict:
    """A course with the fixture roster and an assignment with the blank test and the 15-page scan,
    every submission matched to its student in order. Returns the grading state plus ids."""
    course = client.post("/api/courses", json={"name": "Algebra"}).json()
    client.put(f"/api/courses/{course['id']}/roster", json={"text": fixtures["roster"].read_text()})
    aid = client.post(f"/api/courses/{course['id']}/assignments", json={"name": "Quiz 3"}).json()["id"]
    upload(client, f"/api/assignments/{aid}/blank", fixtures["blank"])
    upload(client, f"/api/assignments/{aid}/batches", fixtures["scans"])
    subs = client.get(f"/api/assignments/{aid}/submissions").json()
    for sub, student in zip(subs["submissions"], subs["students"]):
        r = client.put(f"/api/submissions/{sub['id']}/student", json={"student_id": student["id"]})
        assert r.status_code == 200
    state = client.get(f"/api/assignments/{aid}/grading").json()
    return state | {"course_id": course["id"]}


def add_comment(client: TestClient, problem_id: int, text: str, deduction: float = 0) -> int:
    r = client.post(f"/api/problems/{problem_id}/comments", json={"text": text, "deduction": deduction})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def place(client: TestClient, submission_id: int, comment_id: int, page: int = 0, x=0.5, y=0.5) -> int:
    r = client.post(
        f"/api/submissions/{submission_id}/annotations",
        json={"comment_id": comment_id, "page": page, "x": x, "y": y},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]
