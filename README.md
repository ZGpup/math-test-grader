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

## Mac app

`Grader.app` opens the same screens in a window of its own rather than a browser tab. It stores data
in `~/Library/Application Support/MathTestGrader`, takes a port from the OS, and never starts a
second copy of itself. Closing the window quits. Launches are logged to `grader.log` in the data
folder. See [PACKAGING.md](PACKAGING.md) for what is still needed before sending it to anyone else
(signing and notarization).

```sh
uv run python scripts/make_icon.py                                 # -> Grader.icns (placeholder)
uv run --group build pyinstaller --noconfirm --clean Grader.spec   # -> dist/Grader.app
uv run grader-desktop                                              # the same launcher, unfrozen
```

`--browser` opens the default browser instead of the window, and `--headless` only serves.

On first launch it looks for a checkout's `data/` folder under your home directory and imports it,
so an existing install keeps its courses, scans and grading. `GRADER_IMPORT_FROM=/some/data` names
one instead. The import runs only when there is no database yet, and never writes to the source.

The copies are independent once imported: `uv run grader` keeps using `./data/`. To point the dev
server at the app's data instead:

```sh
GRADER_DATA=~/Library/"Application Support"/MathTestGrader uv run grader
```

## Workflow

1. Home: add a course, then paste the roster (one student per line, `First Last` or `Last, First`).
2. Add an assignment, upload the blank test, and say what each page holds: add a problem to it for
   every problem printed on it, with a label and points, and tick "Cover" on the page the student
   writes their name on. A page takes as many problems as it has on it, the cover page included —
   a quiz with a name at the top and two problems below it is one page that is all three — and a
   test with no cover page at all is fine, its name being read off the first page.
   Optionally upload an answer key: the test with the work filled in. Any page count and any
   layout will do — under its pages, each problem picks the key page its answers start on, which
   is the test's own page unless you say otherwise. So when a worked solution fills a page and
   pushes the next answer onto the one after it, point that problem at the page it really is on.
3. Upload the scan with the pages per test, and check the problem-to-scan-page mapping. If the scanner
   split the job into several PDFs, add them in order: each one carries on where the last stopped.
4. Organize pages: one column per test. Drag a page that was fed in the wrong order to its slot, turn
   the ones that came out upside down or mirrored, then press "Order is correct" to unlock the rest.
5. Match names: click the student for each cover page (the first page, when there is no cover).
6. Grade: pick a problem, place rubric comments on each student's page, press Enter for the next student.
   A comment's "points off" can be negative, which adds points instead, for bonus questions.
   Tick "Anonymous grading" on the assignment to grade without the names: the tests then come up as
   "Test 1", "Test 2"… in scan order. Matching, the results and the exports still use the names.
   "Answer key" opens the key in a column beside the student's work, scrolled to that problem's
   page. The column scrolls on its own, so an answer running onto the next page is read by carrying
   on down, and the student's page never moves.
7. Results: review the table with the average, median, high and low below it, download the CSV,
   export annotated PDFs.

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

