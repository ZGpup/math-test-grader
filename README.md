# math-test-grader

Lightweight Gradescope-like grader for scanned paper math tests. Runs locally and fully offline.

## Run

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv run grader                 # serves http://127.0.0.1:8000 and opens the browser
uv run grader --port 8001 --no-browser
```

All user data (database, uploaded PDFs, rendered pages, exports) lives in `./data/`, which is gitignored.
Set `GRADER_DATA=/some/dir` to use another location.

Fake data to try it out, written to `data/fixtures/` (blank test, answer key, 15-page scan, 30-page
duplex scan, roster):

```sh
uv run python scripts/make_fixture.py
```

Tests:

```sh
uv run pytest
```

## Workflow

1. Home: add a course, then paste the roster (one student per line, `First Last` or `Last, First`).
2. Add an assignment, upload the blank test, and set the cover page plus each problem's label and points.
   Optionally upload an answer key: the test with the work filled in. Any page count will do.
3. Upload the scan with the pages per test, and check the problem-to-scan-page mapping. If the scanner
   split the job into several PDFs, add them in order: each one carries on where the last stopped.
4. Organize pages: one column per test. Drag a page that was fed in the wrong order to its slot, turn
   the ones that came out upside down or mirrored, then press "Order is correct" to unlock the rest.
5. Match names: click the student for each cover page.
6. Grade: pick a problem, place rubric comments on each student's page, press Enter for the next student.
   A comment's "points off" can be negative, which adds points instead, for bonus questions.
   "Answer key" puts the key beside the student's work, on the page for that problem; "Prev"/"Next"
   move through the key if its pages don't line up with the test.
7. Results: review the table, download the CSV, export annotated PDFs.

Keyboard: grading uses `←`/`→` to change students, `1`–`9` to apply a comment, `a` to show the answer key beside the work, and `Enter` for Next. Matching uses typing to filter the roster, `Enter` to assign the top match, and `↑`/`↓` to change tests.

## Layout

```
app/main.py      FastAPI routes (REST API under /api, static files at /) and the `grader` entry point
app/db.py        SQLite schema, roster parsing, score computation
app/pdf.py       PyMuPDF rendering, splitting scans into tests, default page mappings
app/export.py    results table, CSV, annotated PDF export
static/          one HTML page per screen, style.css, vanilla JS, vendored KaTeX
scripts/         make_fixture.py
tests/           pytest
```

