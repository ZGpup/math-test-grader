import csv
import io

from app import export
from conftest import add_comment, place


def read_csv(client, aid: int) -> list[list[str]]:
    r = client.get(f"/api/assignments/{aid}/results.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    return list(csv.reader(io.StringIO(r.text)))


def test_number_formatting():
    assert export.fmt_num(7.0) == "7"
    assert export.fmt_num(7.5) == "7.5"
    assert export.fmt_num(0) == "0"
    assert export.grade_percent(37, 40) == 92.5
    assert export.grade_percent(1, 3) == 33.3
    assert export.grade_percent(2, 3) == 66.7
    assert export.grade_percent(0.9125, 1) == 91.3  # half up, not banker's rounding
    assert export.grade_percent(0, 0) == 0.0


def test_csv_format(client, assignment):
    aid = assignment["id"]
    probs = assignment["problems"]
    s1, s2, _ = [s["id"] for s in assignment["submissions"]]

    # Relabel problem 4 so the header uses labels, and give it 5 points.
    client.put(f"/api/assignments/{aid}/pages/{probs[3]['page']}", json={"kind": "problem", "label": "4a", "max_points": 5})
    place(client, s1, add_comment(client, probs[0]["id"], "a", 2.5))
    place(client, s2, add_comment(client, probs[1]["id"], "b", 12))
    place(client, s2, add_comment(client, probs[3]["id"], "c", 1))

    # An absent student added to the end of the roster.
    roster = client.get(f"/api/courses/{assignment['course_id']}").json()["roster_text"]
    client.put(f"/api/courses/{assignment['course_id']}/roster", json={"text": roster + "Dan Absent\n"})

    rows = read_csv(client, aid)
    assert rows[0] == ["First name", "Last name", "Grade %", "1", "2", "3", "4a", "Total points earned"]
    assert rows[1] == ["Alice", "Example", "92.9", "7.5", "10", "10", "5", "32.5"]
    assert rows[2] == ["Bob", "Sample", "68.6", "10", "0", "10", "4", "24"]
    assert rows[3] == ["Carol", "Testcase", "100.0", "10", "10", "10", "5", "35"]
    assert rows[4] == ["Dan", "Absent", "", "", "", "", "", ""]
    assert len(rows) == 5


def test_csv_omits_unmatched(client, assignment):
    aid = assignment["id"]
    s3 = assignment["submissions"][2]["id"]
    client.put(f"/api/submissions/{s3}/student", json={"student_id": None})
    rows = read_csv(client, aid)
    assert [r[:2] for r in rows[1:]] == [["Alice", "Example"], ["Bob", "Sample"], ["Carol", "Testcase"]]
    assert rows[3][2:] == ["", "", "", "", "", ""]
    results = client.get(f"/api/assignments/{aid}/results").json()
    assert [r["status"] for r in results["rows"]] == ["matched", "matched", "absent", "unmatched"]


def test_summary_statistics(client, assignment):
    aid = assignment["id"]
    probs = assignment["problems"]
    s1, s2, s3 = [s["id"] for s in assignment["submissions"]]

    # The scores of test_csv_format: 32.5, 24 and 35 out of 35.
    client.put(f"/api/assignments/{aid}/pages/{probs[3]['page']}", json={"kind": "problem", "label": "4a", "max_points": 5})
    place(client, s1, add_comment(client, probs[0]["id"], "a", 2.5))
    place(client, s2, add_comment(client, probs[1]["id"], "b", 12))
    place(client, s2, add_comment(client, probs[3]["id"], "c", 1))

    # An absent student is left out; an unmatched test still counts.
    roster = client.get(f"/api/courses/{assignment['course_id']}").json()["roster_text"]
    client.put(f"/api/courses/{assignment['course_id']}/roster", json={"text": roster + "Dan Absent\n"})
    client.put(f"/api/submissions/{s3}/student", json={"student_id": None})

    stats = client.get(f"/api/assignments/{aid}/results").json()["stats"]
    assert [s["label"] for s in stats] == ["Average", "Median", "High", "Low"]
    assert [s["total"] for s in stats] == [30.5, 32.5, 35, 24]
    assert [s["percent"] for s in stats] == [87.1, 92.9, 100.0, 68.6]
    assert stats[0]["cells"] == [9.166667, 6.666667, 10, 4.666667]
    assert stats[1]["cells"] == [10, 10, 10, 5]
    assert stats[2]["cells"] == [10, 10, 10, 5]
    assert stats[3]["cells"] == [7.5, 0, 10, 4]


def test_summary_statistics_edge_cases():
    def test(total: float) -> dict:
        return {"status": "matched", "cells": [{"points": total}], "total": total}

    absent = {"status": "absent", "cells": [], "total": None}
    median = export.stats([test(20), test(25), absent], 1, 30)[1]
    assert median == {"label": "Median", "cells": [22.5], "total": 22.5, "percent": 75.0}
    assert export.stats([absent], 1, 30) == []
    assert export.stats([], 1, 30) == []


def test_reassigning_student_moves_match(client, assignment):
    aid = assignment["id"]
    s1, s2, _ = [s["id"] for s in assignment["submissions"]]
    alice = assignment["submissions"][0]["student_id"]
    subs = client.put(f"/api/submissions/{s2}/student", json={"student_id": alice}).json()["submissions"]
    by_id = {s["id"]: s["student_id"] for s in subs}
    assert by_id[s1] is None and by_id[s2] == alice
