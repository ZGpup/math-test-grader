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
4. Organize pages: one column per test. Drag a misfed page to its slot, turn the ones that came out
   upside down or mirrored, then press "Order is correct".
5. Match names: click the student for each cover page.
6. Grade: pick a problem, place rubric comments on each student's page, press Enter for the next student.
7. Results: review the table, download the CSV, export annotated PDFs.

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
- An assignment has one scan, however many PDFs it took. A scanner that cuts a job in half produces files that carry on from each other, so a later PDF is appended to the first one in upload order: its pages are added to `uploads/<key>.pdf`, rendered from the old page count up (so cached images stay good), and the stream is re-cut into tests. A test can therefore span two files. Pages per test is asked once, for the first PDF.
- Pages past the last full test are not lost, they wait to be the start of the next test when the next PDF arrives. Appending never touches tests that already exist, and it clears `checked` only when it makes new ones.
- Changing pages per test re-splits the whole scan. That discards the name matches and placed comments, so it asks first.

Page order
- Which scan page sits in a test's page slot is stored per slot (`submission_pages`), so a misfed or upside-down page is fixed once and every screen follows. `submissions.first_page` only marks where a test starts in scan order and how many pages it has.
- The organize screen shows the batch as one flat page list cut into tests. Dropping a page pulls it out and inserts it at that slot, shifting everything in between, so test boundaries and page counts never change and a page fed in the wrong order is fixed with one drag. The row buttons turn a whole row, which is the duplex case where every back side is upside down.
- Flips are two independent flags, `upside_down` and `mirrored`. On screen they are a CSS transform on the image (`.flip`); in the export the same turn is baked into the PDF page, 180° via the page rotation and the mirror via a matrix prepended to the content stream. Annotations are drawn afterwards, so they read the right way up and land where they were placed.
- Each scan carries a `checked` flag. Match names and Grade stay locked until every scan's order is confirmed, because both hang off page slots. Re-splitting clears it; fixing the order later does not.
- Annotations belong to a page slot, not to a scan page. Moving pages after grading leaves them where they were, and the organize screen warns when comments are already placed.

Roster and matching
- The roster textarea is saved as typed. Student ids survive edits: an unchanged name keeps its id, and an edited line reuses the student on that line. A typo fix therefore keeps the match, and removing a line unmatches that student's test.
- "Roster order" is the textarea order. Grading goes through tests in that order, with unmatched tests last.
- A student can be matched to at most one test per assignment. Clicking an already-matched student moves them to the current test.

Grading
- Every placement of a comment deducts, so placing "Incorrect (−1)" on three T/F answers takes off 3 points.
- "Points off" may be negative, which gives points instead: that is how bonus questions work, usually on a problem worth 0. A problem's score can then pass its own points and a grade can pass 100%, but a score never drops below 0. The comment shows `+2` in accent instead of `−2`, on screen and in the exported PDF. Older databases checked that a deduction was never negative; `db.migrate` rebuilds the comments table to drop that check, with foreign keys off so placed comments survive.
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
- Deleting an assignment takes two steps: the normal confirmation, then a warning listing what will be lost, where you must type `yes`.
- The server binds to 127.0.0.1 only and has no authentication.
