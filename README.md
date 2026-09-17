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

Fake data to try it out, written to `data/fixtures/` (blank test, 15-page scan, 30-page duplex scan, roster):

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
3. Upload scan PDFs with the pages per test, and check the problem-to-scan-page mapping.
4. Match names: click the student for each cover page.
5. Grade: pick a problem, place rubric comments on each student's page, press Enter for the next student.
6. Results: review the table, download the CSV, export annotated PDFs.

Keyboard: grading uses `←`/`→` to change students, `1`–`9` to apply a comment, and `Enter` for Next. Matching uses typing to filter the roster, `Enter` to assign the top match, and `↑`/`↓` to change tests.

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

## Design decisions

Storage
- Plain `sqlite3`, no ORM. Files: `data/grader.db`, `data/uploads/<key>.pdf`, `data/pages/<key>/NNNN.png` (150 dpi) plus `NNNN_t.png` thumbnails at 1/4 size. Each upload gets a random key, so cached page images never go stale.
- Scores are never stored. They are computed from placed comments when read, so editing a comment's text or deduction updates every submission right away.

Blank test
- New problems default to 10 points and number themselves in page order. Changing the cover turns the old cover page into "none". To make the cover a problem or "none", pick another cover first.
- You can't replace the blank test while scans exist (delete the scans first), because the page mappings depend on its page count.

Scans
- The problem-to-scan-page mapping is stored per blank page (cover included), so changing problem labels or points never loses it. When pages per test is neither equal to nor double the blank page count, the mapping table opens with a one-to-one default (clamped to the last scan page).
- Leftover pages that don't fill a whole test are ignored, and a warning shows how many.
- Changing pages per test on an uploaded file re-splits it. That discards the file's name matches and placed comments, so it asks first.

Roster and matching
- The roster textarea is saved as typed. Student ids survive edits: an unchanged name keeps its id, and an edited line reuses the student on that line. A typo fix therefore keeps the match, and removing a line unmatches that student's test.
- "Roster order" is the textarea order. Grading goes through tests in that order, with unmatched tests last.
- A student can be matched to at most one test per assignment. Clicking an already-matched student moves them to the current test.

Grading
- A comment counts once per submission, even when placed more than once or on several pages. Placing it on a page's back doesn't double-deduct.
- "Graded" is only a status flag, set by Next (click it in the sidebar to toggle). Results and the CSV always use the current computed scores; ungraded cells are striped in the results table.
- An annotation's (x, y) is the top-left corner of its box. Box font size is 1.6% of page width and max width is 35%, both on screen (CSS container units) and in the export, so exported PDFs match the screen.
- Annotations from other problems on the same page are shown faded and can't be edited.
- A click places a comment at the top right, below any boxes already on the right half of the page.

Results and export
- CSV rows are every roster student in roster order. Absent students get empty cells, and unmatched tests are left out (they still appear in the results table). Problem column headers are the problem labels. Grade % rounds half up to 1 decimal, and point values drop trailing zeros (`7`, `7.5`).
- PDFs are named `<Last>_<First>.pdf` (unsafe characters become `_`, and duplicates get `_2`). Each export replaces the previous one. The score box goes in the top-right corner of the mapped cover page.
- Comment text is drawn with the vendored KaTeX_Main font, and math uses matplotlib mathtext with Computer Modern. Each `$...$` that mathtext can't parse is printed as raw text. Rotated scan pages are normalized before drawing.

UI
- One HTML page per screen, no framework. Confirmations use in-page square modals, not browser dialogs, to keep the no-rounded-corners rule.
- The server binds to 127.0.0.1 only and has no authentication.
