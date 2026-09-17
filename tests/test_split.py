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


def test_upload_batches_split_and_map(client, fixtures):
    cid = client.post("/api/courses", json={"name": "C"}).json()["id"]
    aid = client.post(f"/api/courses/{cid}/assignments", json={"name": "A"}).json()["id"]
    detail = upload(client, f"/api/assignments/{aid}/blank", fixtures["blank"])
    assert detail["blank_pages"] == 5
    assert [p["kind"] for p in detail["pages"]] == ["cover", "problem", "problem", "problem", "problem"]
    assert [p["label"] for p in detail["problems"]] == ["1", "2", "3", "4"]

    detail = upload(client, f"/api/assignments/{aid}/batches", fixtures["scans"])
    simplex = detail["batches"][0]
    assert (simplex["tests"], simplex["pages_per_test"], simplex["leftover"], simplex["mode"]) == (3, 5, 0, "identity")

    detail = upload(client, f"/api/assignments/{aid}/batches", fixtures["duplex"], pages_per_test="10")
    duplex = detail["batches"][1]
    assert (duplex["tests"], duplex["leftover"], duplex["mode"]) == (3, 0, "odd")
    assert duplex["page_map"] == [0, 2, 4, 6, 8]

    detail = upload(client, f"/api/assignments/{aid}/batches", fixtures["scans"], pages_per_test="4")
    odd = detail["batches"][2]
    assert (odd["tests"], odd["leftover"], odd["mode"]) == (3, 3, "custom")
    assert detail["progress"]["submissions"] == 9

    subs = client.get(f"/api/assignments/{aid}/submissions").json()["submissions"]
    assert [(s["first_page"], s["page_count"]) for s in subs if s["batch_id"] == duplex["id"]] == [
        (0, 10),
        (10, 10),
        (20, 10),
    ]

    r = client.patch(f"/api/batches/{odd['id']}", json={"page_map": [0, 1, 1, 2, 3]})
    assert r.json()["batches"][2]["page_map"] == [0, 1, 1, 2, 3]
    assert client.patch(f"/api/batches/{odd['id']}", json={"page_map": [0, 1, 2, 3, 4]}).status_code == 400

    r = client.patch(f"/api/batches/{odd['id']}", json={"pages_per_test": 5})
    resplit = r.json()["batches"][2]
    assert (resplit["tests"], resplit["leftover"], resplit["mode"]) == (3, 0, "identity")

    detail = client.delete(f"/api/batches/{duplex['id']}").json()
    assert len(detail["batches"]) == 2
    assert detail["progress"]["submissions"] == 6


def test_page_kinds(client, fixtures):
    cid = client.post("/api/courses", json={"name": "C"}).json()["id"]
    aid = client.post(f"/api/courses/{cid}/assignments", json={"name": "A"}).json()["id"]
    upload(client, f"/api/assignments/{aid}/blank", fixtures["blank"])
    detail = client.put(f"/api/assignments/{aid}/pages/4", json={"kind": "none"}).json()
    assert detail["total_points"] == 30
    detail = client.put(f"/api/assignments/{aid}/pages/1", json={"kind": "cover"}).json()
    assert [p["kind"] for p in detail["pages"]] == ["none", "cover", "problem", "problem", "none"]
    detail = client.put(
        f"/api/assignments/{aid}/pages/3", json={"kind": "problem", "label": "2b", "max_points": 7.5}
    ).json()
    assert [(p["label"], p["max_points"]) for p in detail["problems"]] == [("2", 10), ("2b", 7.5)]
    assert client.put(f"/api/assignments/{aid}/pages/1", json={"kind": "none"}).status_code == 400
