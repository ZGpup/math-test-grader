import pymupdf

from app import db, pdf
from conftest import upload


def test_split_even():
    ranges, leftover = pdf.split_ranges(15, 5)
    assert ranges == [(0, 5), (5, 5), (10, 5)]
    assert leftover == 0


def test_split_leftover_keeps_full_chunks():
    ranges, leftover = pdf.split_ranges(17, 5)
    assert ranges == [(0, 5), (5, 5), (10, 5)]
    assert leftover == 2
    assert pdf.split_ranges(3, 5) == ([], 3)


def test_default_map_one_to_one():
    assert pdf.default_page_map(5, 5) == [0, 1, 2, 3, 4]
    assert pdf.mapping_mode(5, 5, [0, 1, 2, 3, 4]) == "identity"


def test_default_map_duplex_uses_odd_pages():
    # 0-based offsets 0, 2, 4, ... are scan pages 1, 3, 5, ...
    assert pdf.default_page_map(5, 10) == [0, 2, 4, 6, 8]
    assert pdf.mapping_mode(5, 10, [0, 2, 4, 6, 8]) == "odd"


def test_default_map_other_sizes_needs_table():
    assert pdf.default_page_map(5, 7) == [0, 1, 2, 3, 4]
    assert pdf.mapping_mode(5, 7, [0, 1, 2, 3, 4]) == "custom"
    assert pdf.default_page_map(5, 3) == [0, 1, 2, 2, 2]
    assert pdf.mapping_mode(5, 5, [0, 1, 2, 4, 3]) == "custom"


def test_roster_parsing():
    text = "Alice Example\n\n  Sample,  Bob \nMary Ann Smith\nde la Cruz, Maria\nPrince\n"
    assert db.parse_roster(text) == [
        ("Alice", "Example"),
        ("Bob", "Sample"),
        ("Mary Ann", "Smith"),
        ("Maria", "de la Cruz"),
        ("", "Prince"),
    ]


def test_roster_edit_keeps_student_ids(client):
    cid = client.post("/api/courses", json={"name": "C"}).json()["id"]
    before = client.put(f"/api/courses/{cid}/roster", json={"text": "Alice Example\nBob Sample"}).json()["students"]
    client.put(f"/api/courses/{cid}/roster", json={"text": "New Person\nAlice Example\nBob Sample"})
    after = client.put(
        f"/api/courses/{cid}/roster", json={"text": "New Person\nAlice Example\nBob Sampel"}
    ).json()["students"]
    ids = {(s["first_name"], s["last_name"]): s["id"] for s in after}
    assert ids[("Alice", "Example")] == before[0]["id"]
    assert ids[("Bob", "Sampel")] == before[1]["id"]  # typo fix on the same line keeps the student
    assert [s["last_name"] for s in after] == ["Person", "Example", "Sampel"]
    after = client.put(f"/api/courses/{cid}/roster", json={"text": "Alice Example"}).json()["students"]
    assert [s["id"] for s in after] == [before[0]["id"]]


def new_assignment(client, fixtures) -> int:
    """A course and an assignment with the 5-page blank test uploaded."""
    cid = client.post("/api/courses", json={"name": "C"}).json()["id"]
    aid = client.post(f"/api/courses/{cid}/assignments", json={"name": "A"}).json()["id"]
    detail = upload(client, f"/api/assignments/{aid}/blank", fixtures["blank"])
    assert detail["blank_pages"] == 5
    assert [p["cover"] for p in detail["pages"]] == [True, False, False, False, False]
    assert [len(p["problems"]) for p in detail["pages"]] == [0, 1, 1, 1, 1]
    assert [p["label"] for p in detail["problems"]] == ["1", "2", "3", "4"]
    return aid


def test_upload_splits_a_scan_into_tests(client, fixtures):
    aid = new_assignment(client, fixtures)
    detail = upload(client, f"/api/assignments/{aid}/batches", fixtures["scans"])
    b = detail["batches"][0]
    assert (b["tests"], b["pages_per_test"], b["leftover"], b["mode"]) == (3, 5, 0, "identity")
    assert detail["progress"]["submissions"] == 3


def test_duplex_scan_maps_to_odd_pages(client, fixtures):
    aid = new_assignment(client, fixtures)
    detail = upload(client, f"/api/assignments/{aid}/batches", fixtures["duplex"], pages_per_test="10")
    b = detail["batches"][0]
    assert (b["tests"], b["leftover"], b["mode"]) == (3, 0, "odd")
    assert b["page_map"] == [0, 2, 4, 6, 8]
    subs = client.get(f"/api/assignments/{aid}/submissions").json()["submissions"]
    assert [(s["first_page"], s["page_count"]) for s in subs] == [(0, 10), (10, 10), (20, 10)]


def test_mapping_and_resplit(client, fixtures):
    aid = new_assignment(client, fixtures)
    detail = upload(client, f"/api/assignments/{aid}/batches", fixtures["scans"], pages_per_test="4")
    b = detail["batches"][0]
    assert (b["tests"], b["leftover"], b["mode"]) == (3, 3, "custom")

    r = client.patch(f"/api/batches/{b['id']}", json={"page_map": [0, 1, 1, 2, 3]})
    assert r.json()["batches"][0]["page_map"] == [0, 1, 1, 2, 3]
    assert client.patch(f"/api/batches/{b['id']}", json={"page_map": [0, 1, 2, 3, 4]}).status_code == 400

    resplit = client.patch(f"/api/batches/{b['id']}", json={"pages_per_test": 5}).json()["batches"][0]
    assert (resplit["tests"], resplit["leftover"], resplit["mode"]) == (3, 0, "identity")

    detail = client.delete(f"/api/batches/{b['id']}").json()
    assert detail["batches"] == []
    assert detail["progress"]["submissions"] == 0


def test_another_pdf_carries_on_from_the_last_page(client, fixtures):
    aid = new_assignment(client, fixtures)
    detail = upload(client, f"/api/assignments/{aid}/batches", fixtures["scans"], pages_per_test="8")
    b = detail["batches"][0]
    assert (b["page_count"], b["tests"], b["leftover"]) == (15, 1, 7)
    client.patch(f"/api/batches/{b['id']}", json={"checked": True})

    detail = upload(client, f"/api/assignments/{aid}/batches", fixtures["scans"])
    assert len(detail["batches"]) == 1, "a second PDF joins the scan instead of starting its own"
    b = detail["batches"][0]
    assert (b["page_count"], b["tests"], b["leftover"]) == (30, 3, 6)
    assert b["filename"] == "scans.pdf, scans.pdf"
    assert b["checked"] is False, "the new tests have not been looked at yet"

    subs = client.get(f"/api/assignments/{aid}/submissions").json()["submissions"]
    assert [(s["first_page"], s["page_count"]) for s in subs] == [(0, 8), (8, 8), (16, 8)]
    assert [p["scan_page"] for p in subs[0]["pages"]] == list(range(8)), "the first test is untouched"
    # The test straddling the cut takes the tail of the first file and the head of the second.
    assert [p["scan_page"] for p in subs[1]["pages"]] == [8, 9, 10, 11, 12, 13, 14, 15]
    assert client.get(f"/api/pages/{b['key']}/29.png").status_code == 200
    assert client.get(f"/api/pages/{b['key']}/30.png").status_code == 404


def test_pages_per_test_is_kept_across_files(client, fixtures):
    aid = new_assignment(client, fixtures)
    upload(client, f"/api/assignments/{aid}/batches", fixtures["scans"], pages_per_test="4")
    # A later file cannot change how the scan is cut up, whatever it sends.
    detail = upload(client, f"/api/assignments/{aid}/batches", fixtures["scans"], pages_per_test="10")
    b = detail["batches"][0]
    assert (b["pages_per_test"], b["page_count"], b["tests"]) == (4, 30, 7)


def test_a_test_split_across_two_pdfs_exports_in_one_piece(client, fixtures):
    # The blank test doubles as a scan here, so the exported pages can be told apart by their text.
    cid = client.post("/api/courses", json={"name": "C"}).json()["id"]
    client.put(f"/api/courses/{cid}/roster", json={"text": "Ann One"})
    aid = client.post(f"/api/courses/{cid}/assignments", json={"name": "Split"}).json()["id"]
    upload(client, f"/api/assignments/{aid}/blank", fixtures["blank"])
    upload(client, f"/api/assignments/{aid}/batches", fixtures["blank"], pages_per_test="3")
    detail = upload(client, f"/api/assignments/{aid}/batches", fixtures["blank"])
    assert (detail["batches"][0]["tests"], detail["batches"][0]["leftover"]) == (3, 1)

    subs = client.get(f"/api/assignments/{aid}/submissions").json()
    straddling = subs["submissions"][1]  # scan pages 3, 4 from the first file and 5 from the second
    assert [p["scan_page"] for p in straddling["pages"]] == [3, 4, 5]
    client.put(f"/api/submissions/{straddling['id']}/student", json={"student_id": subs["students"][0]["id"]})

    assert client.post(f"/api/assignments/{aid}/export").json()["files"] == ["One_Ann.pdf"]
    with pymupdf.open(db.data_dir() / "exports" / "Split" / "One_Ann.pdf") as doc:
        assert doc.page_count == 3
        assert "Problem 3" in doc[0].get_text()
        assert "Problem 4" in doc[1].get_text()
        assert "Algebra Quiz 3" in doc[2].get_text(), "the page after the cut comes from the second PDF"


def test_cover_moves_and_problems_are_edited(client, fixtures):
    cid = client.post("/api/courses", json={"name": "C"}).json()["id"]
    aid = client.post(f"/api/courses/{cid}/assignments", json={"name": "A"}).json()["id"]
    detail = upload(client, f"/api/assignments/{aid}/blank", fixtures["blank"])
    last = detail["problems"][-1]
    detail = client.delete(f"/api/problems/{last['id']}").json()
    assert detail["total_points"] == 30
    detail = client.put(f"/api/assignments/{aid}/cover", json={"page": 1}).json()
    assert [p["cover"] for p in detail["pages"]] == [False, True, False, False, False]
    page = detail["pages"][1]
    assert [p["label"] for p in page["problems"]] == ["1"], "the new cover page keeps its problem"
    detail = client.patch(f"/api/problems/{detail['problems'][2]['id']}", json={"label": "2b", "max_points": 7.5}).json()
    assert [(p["label"], p["max_points"]) for p in detail["problems"]] == [("1", 10), ("2", 10), ("2b", 7.5)]
