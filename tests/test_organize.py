import pymupdf

from app import db
from conftest import add_comment, place, upload


def setup(client, fixtures, scan="scans", **form) -> tuple[int, dict]:
    """A course with a roster and an assignment with the blank test and one scan file."""
    cid = client.post("/api/courses", json={"name": "C"}).json()["id"]
    client.put(f"/api/courses/{cid}/roster", json={"text": "Ann One\nBen Two\nCy Three"})
    aid = client.post(f"/api/courses/{cid}/assignments", json={"name": "Quiz"}).json()["id"]
    upload(client, f"/api/assignments/{aid}/blank", fixtures["blank"])
    detail = upload(client, f"/api/assignments/{aid}/batches", fixtures[scan], **form)
    return aid, detail["batches"][0]


def order(client, aid: int) -> list[list[int]]:
    """The scan page behind every page slot of every test."""
    subs = client.get(f"/api/assignments/{aid}/submissions").json()["submissions"]
    return [[p["scan_page"] for p in s["pages"]] for s in subs]


def put_pages(client, batch_id: int, pages: list[dict]):
    return client.put(f"/api/batches/{batch_id}/pages", json={"pages": pages})


def flat(client, batch_id: int) -> list[dict]:
    state = client.get(f"/api/batches/{batch_id}/pages").json()
    return [dict(p) for t in state["tests"] for p in t["pages"]]


def ink_centroid(page: pymupdf.Page) -> tuple[float, float]:
    """Where the dark pixels sit on a page, as fractions of its width and height."""
    pix = page.get_pixmap(dpi=18, alpha=False)
    xs = ys = n = 0
    for y in range(pix.height):
        for x in range(pix.width):
            if pix.pixel(x, y)[0] < 128:
                xs, ys, n = xs + x, ys + y, n + 1
    assert n, "page is blank"
    return xs / n / pix.width, ys / n / pix.height


def same_spot(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) < 0.02 and abs(a[1] - b[1]) < 0.02


def test_new_scan_starts_in_scan_order_and_unchecked(client, fixtures):
    aid, batch = setup(client, fixtures)
    assert batch["checked"] is False
    assert order(client, aid) == [[0, 1, 2, 3, 4], [5, 6, 7, 8, 9], [10, 11, 12, 13, 14]]
    state = client.get(f"/api/batches/{batch['id']}/pages").json()
    assert state["slots"] == ["Cover", "Problem 1", "Problem 2", "Problem 3", "Problem 4"]
    assert [len(t["pages"]) for t in state["tests"]] == [5, 5, 5]


def test_moving_a_page_shifts_the_rest_along(client, fixtures):
    aid, batch = setup(client, fixtures)
    pages = flat(client, batch["id"])
    # The last page of the last test was fed in first: put it back at the front.
    pages.insert(0, pages.pop(14))
    assert put_pages(client, batch["id"], pages).status_code == 200
    assert order(client, aid) == [[14, 0, 1, 2, 3], [4, 5, 6, 7, 8], [9, 10, 11, 12, 13]]
    # The order survives a round trip and the tests keep their own page counts.
    assert [p["scan_page"] for p in flat(client, batch["id"])] == [14, *range(14)]


def test_the_same_pages_must_be_kept(client, fixtures):
    _, batch = setup(client, fixtures)
    pages = flat(client, batch["id"])
    assert put_pages(client, batch["id"], pages[:-1]).status_code == 400
    duplicated = [*pages[:-1], dict(pages[0])]
    assert put_pages(client, batch["id"], duplicated).status_code == 400
    outside = [*pages[:-1], {"scan_page": 99}]
    assert put_pages(client, batch["id"], outside).status_code == 400
    assert [p["scan_page"] for p in flat(client, batch["id"])] == list(range(15))


def test_flips_and_the_checked_flag(client, fixtures):
    aid, batch = setup(client, fixtures)
    pages = flat(client, batch["id"])
    pages[1]["upside_down"] = True
    pages[2]["mirrored"] = True
    pages[3].update(upside_down=True, mirrored=True)
    state = put_pages(client, batch["id"], pages).json()
    saved = [p for t in state["tests"] for p in t["pages"]]
    assert [(p["upside_down"], p["mirrored"]) for p in saved[:4]] == [(0, 0), (1, 0), (0, 1), (1, 1)]

    subs = client.get(f"/api/assignments/{aid}/submissions").json()["submissions"]
    assert [(p["upside_down"], p["mirrored"]) for p in subs[0]["pages"]][:4] == [(0, 0), (1, 0), (0, 1), (1, 1)]

    detail = client.patch(f"/api/batches/{batch['id']}", json={"checked": True}).json()
    assert detail["batches"][0]["checked"] is True
    # Fixing the order afterwards leaves it checked.
    put_pages(client, batch["id"], pages)
    assert client.get(f"/api/assignments/{aid}").json()["batches"][0]["checked"] is True


def test_resplitting_restores_scan_order_and_asks_again(client, fixtures):
    aid, batch = setup(client, fixtures)
    pages = flat(client, batch["id"])
    pages.insert(0, pages.pop(14))
    pages[0]["upside_down"] = True
    put_pages(client, batch["id"], pages)
    client.patch(f"/api/batches/{batch['id']}", json={"checked": True})

    detail = client.patch(f"/api/batches/{batch['id']}", json={"pages_per_test": 3}).json()
    assert detail["batches"][0]["checked"] is False
    assert order(client, aid)[:2] == [[0, 1, 2], [3, 4, 5]]


def test_export_follows_the_fixed_order_and_flips(client, fixtures):
    # The blank test doubles as a scan here, so the exported pages can be told apart by their text.
    cid = client.post("/api/courses", json={"name": "C"}).json()["id"]
    client.put(f"/api/courses/{cid}/roster", json={"text": "Ann One"})
    aid = client.post(f"/api/courses/{cid}/assignments", json={"name": "Flip"}).json()["id"]
    upload(client, f"/api/assignments/{aid}/blank", fixtures["blank"])
    batch = upload(client, f"/api/assignments/{aid}/batches", fixtures["blank"])["batches"][0]

    src = pymupdf.open(fixtures["blank"])
    upright = [ink_centroid(page) for page in src]

    pages = flat(client, batch["id"])
    pages.insert(0, pages.pop(4))  # the last page was fed in first: [4, 0, 1, 2, 3]
    pages[1]["upside_down"] = True  # scan page 0 came out of the feeder upside down
    pages[2]["mirrored"] = True  # scan page 1 came out mirrored
    put_pages(client, batch["id"], pages)

    subs = client.get(f"/api/assignments/{aid}/submissions").json()
    sid = subs["submissions"][0]["id"]
    client.put(f"/api/submissions/{sid}/student", json={"student_id": subs["students"][0]["id"]})
    problems = client.get(f"/api/assignments/{aid}").json()["problems"]
    place(client, sid, add_comment(client, problems[0]["id"], "Look here", 2), page=3, x=0.1, y=0.1)

    body = client.post(f"/api/assignments/{aid}/export").json()
    assert body["files"] == ["One_Ann.pdf"]
    with pymupdf.open(db.data_dir() / "exports" / "Flip" / "One_Ann.pdf") as doc:
        assert doc.page_count == 5
        assert "Problem 4" in doc[0].get_text()  # the moved page leads
        assert "Algebra Quiz 3" in doc[1].get_text()  # everything else shifted along
        x, y = upright[0]
        assert same_spot(ink_centroid(doc[1]), (1 - x, 1 - y))  # upside down
        x, y = upright[1]
        assert same_spot(ink_centroid(doc[2]), (1 - x, y))  # mirrored
        assert same_spot(ink_centroid(doc[4]), upright[3])  # left alone
        assert doc[3].get_images(), "the comment label is drawn on the page it was placed on"
    src.close()
