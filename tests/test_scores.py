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

    # A comment placed twice on one submission deducts once.
    place(client, s2, sign, x=0.1, y=0.9)
    assert scores(client, aid)[1] == [6.5, 10, 10, 10]

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
    assert client.post(f"/api/problems/{p1}/comments", json={"text": "x", "deduction": -1}).status_code == 422
    assert client.post(f"/api/problems/{p1}/comments", json={"text": "  "}).status_code == 422


def test_graded_status(client, assignment):
    aid = assignment["id"]
    p1 = assignment["problems"][0]["id"]
    s1 = assignment["submissions"][0]["id"]
    assert client.put(f"/api/submissions/{s1}/graded/{p1}", json={"graded": True}).status_code == 200
    state = client.get(f"/api/assignments/{aid}/grading").json()
    assert state["graded"] == [[s1, p1]]
    progress = client.get(f"/api/assignments/{aid}").json()["progress"]
    assert (progress["graded"], progress["gradable"], progress["matched"]) == (1, 12, 3)
