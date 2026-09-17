import io
import zipfile

import pymupdf

from app import export
from conftest import add_comment, place, upload


def test_label_rendering_falls_back_to_raw_text():
    good = export.render_label(r"Sign error: $x^2 - \frac{1}{2}$", 2, 10, 200)
    bad = export.render_label(r"Broken $\frac{1}$ and $\notacommand{x}$ math", 0, 10, 200)
    lonely = export.render_label("costs $5", 0, 10, 200)
    for lines in (good, bad, lonely):
        assert lines and all(png.startswith(b"\x89PNG") and w > 0 and h > 0 for png, w, h in lines)
    wrapped = export.render_label("a fairly long comment that must wrap onto several lines", 1, 10, 80)
    assert len(wrapped) > 1
    assert all(w <= 80 for _, w, _ in wrapped)


def test_export_pdfs(client, assignment, fixtures):
    aid = assignment["id"]
    p1, p2 = assignment["problems"][0]["id"], assignment["problems"][1]["id"]
    s1, s2, _ = [s["id"] for s in assignment["submissions"]]
    place(client, s1, add_comment(client, p1, r"Check $2x = 8$", 2), page=1, x=0.6, y=0.1)
    place(client, s1, add_comment(client, p2, r"Bad $\frac{x}$ TeX", 1), page=2, x=0.98, y=0.98)
    place(client, s2, add_comment(client, p2, "Good"), page=4)

    r = client.post(f"/api/assignments/{aid}/export")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 3
    assert body["files"] == ["Example_Alice.pdf", "Sample_Bob.pdf", "Testcase_Carol.pdf"]

    from app import db

    out_dir = db.data_dir() / "exports" / "Quiz_3"
    for name in body["files"]:
        with pymupdf.open(out_dir / name) as doc:
            assert doc.page_count == 5
            cover_text = doc[0].get_text()
            assert "Total" in cover_text
    with pymupdf.open(out_dir / "Example_Alice.pdf") as doc:
        assert "37 / 40" in doc[0].get_text()
        assert len(doc[1].get_images()) > 1  # scan image plus the annotation label
        assert len(doc[2].get_images()) > 1
        assert len(doc[3].get_images()) == 1

    z = client.get(body["zip"])
    assert z.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert sorted(names) == ["Quiz_3/Example_Alice.pdf", "Quiz_3/Sample_Bob.pdf", "Quiz_3/Testcase_Carol.pdf"]


def test_export_duplex_uses_mapped_cover(client, fixtures):
    cid = client.post("/api/courses", json={"name": "C"}).json()["id"]
    client.put(f"/api/courses/{cid}/roster", json={"text": "Ann One\nBen Two\nCy Three"})
    aid = client.post(f"/api/courses/{cid}/assignments", json={"name": "Duplex"}).json()["id"]
    upload(client, f"/api/assignments/{aid}/blank", fixtures["blank"])
    detail = upload(client, f"/api/assignments/{aid}/batches", fixtures["duplex"], pages_per_test="10")
    batch = detail["batches"][0]
    # Pretend the cover is the second scanned page (the back of page 1).
    client.patch(f"/api/batches/{batch['id']}", json={"page_map": [1, 2, 4, 6, 8]})
    subs = client.get(f"/api/assignments/{aid}/submissions").json()
    client.put(f"/api/submissions/{subs['submissions'][0]['id']}/student", json={"student_id": subs["students"][0]["id"]})

    body = client.post(f"/api/assignments/{aid}/export").json()
    assert body["files"] == ["One_Ann.pdf"]
    from app import db

    with pymupdf.open(db.data_dir() / "exports" / "Duplex" / "One_Ann.pdf") as doc:
        assert doc.page_count == 10
        assert "Total" not in doc[0].get_text()
        assert "Total" in doc[1].get_text()
