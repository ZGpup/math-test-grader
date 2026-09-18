# Shipping math-test-grader as a Mac app

What it would take to turn `uv run grader` into something a math teacher downloads and
double-clicks. Everything with a number next to it was measured on this machine
(macOS 26.5.1, arm64, Python 3.13.1) by actually building and running the bundle, not estimated.

## The short version

It works, and it is less work than it looks. I built a real `.app` from the current code with
PyInstaller, launched it from Finder, and drove the whole workflow through it — roster, blank test,
scan upload, splitting, matching, rubric comments with LaTeX math, CSV, annotated PDF export. All of
it passed, unmodified. The app is **117 MB** on disk, **62 MB** as a compressed `.dmg`, and is
serving **0.76 s** after a double-click.

The code changes were small and genuinely shallow, and **steps 2 and 3 below are now done**:
`app/desktop.py` and `Grader.spec` are committed, and an existing `./data/` folder is imported on
first launch. Nothing about the architecture fought this.

The real cost is not code. It is **$99/year for an Apple Developer account**, without which every
teacher you send this to hits a scary Gatekeeper wall on first launch. That is the decision to make;
the engineering is the easy part.

## What I verified

I built `Grader.app` from the current `anonymous-grading` branch and ran the fixture workflow against
the frozen bundle over its own HTTP API:

```
[ 1] course created                        [ 9] matched 3 tests to students
[ 2] roster parsed: 3 students             [10] comments created (math + bonus)
[ 3] assignment created                    [11] placed 6 annotations
[ 4] blank test uploaded, 5 pages          [12] results computed: first total=39.5 (98.8%)
[ 5] cover set, 4 problems defined         [13] CSV generated (202 bytes)
[ 6] scan uploaded -> batch 1              [14] EXPORTED 3 annotated PDFs
[ 7] page order confirmed                  [15] export zip downloaded (414714 bytes)
[ 8] 3 tests split out of the scan
```

I rendered an exported PDF back to an image to confirm the export is really correct inside the
bundle, not merely exit-code-zero: `Sign error: should be $-\frac{3}{4}x^2$` rendered as proper
math with the `−2pts` superscript, and the bonus comment rendered `+1.5pts` in accent blue. The
matplotlib mathtext path and the vendored KaTeX font both survive freezing.

I also launched it through LaunchServices (`open -a Grader.app`, the Finder path, where the working
directory and environment differ from a shell) and confirmed `/`, `/api/courses` and `/grade.html`
all return 200.

### Measurements

| | |
|---|---|
| `.app` bundle | 117 MB |
| `.dmg` (UDZO compressed) | 62 MB |
| Startup, first ever launch | 12.6 s (builds the font cache once) |
| Double-click to serving, after that | **0.76 s** |
| Architecture | arm64 only (see below) |
| Gatekeeper verdict, ad-hoc signed | **rejected** |

Where the 117 MB goes:

| Component | Size | Note |
|---|---|---|
| PyMuPDF | 47 MB | `libmupdf.dylib` alone is 32 MB |
| matplotlib + numpy + Pillow + friends | ~25 MB | pulled in *only* by `render_label` in `app/export.py` |
| CPython + framework | ~12 MB | |
| pydantic-core, sqlite, openssl, misc | ~15 MB | |
| pywebview + pyobjc | ~3 MB | the window; did not change the `.dmg` size |
| `static/` incl. vendored KaTeX | 1.6 MB | |

## Four things I found by building it

These are the non-obvious ones. Each cost me a build cycle to find, and each would have cost you one.

**1. matplotlib rebuilt its font cache on every single launch — an 11-second startup, every time.**

PyInstaller's matplotlib runtime hook points `MPLCONFIGDIR` at a temp directory that is destroyed
when the process exits, so the cache is never reused. Phase timings from the instrumented build:

```
[  0.00s] interpreter up
[  0.06s] pymupdf imported          <- PyMuPDF is not the problem
          Matplotlib is building the font cache; this may take a moment.
[ 10.83s] matplotlib imported       <- it is
[ 11.03s] app.main imported
```

The fix is three lines in the launcher, setting `MPLCONFIGDIR` to a persistent directory *before*
matplotlib is imported. After the fix: 12.6 s once, then 0.65 s forever. This single change is the
difference between an app that feels broken and one that feels instant.

**2. `os.environ.setdefault("MPLCONFIGDIR", ...)` silently does nothing.** I wrote the fix above with
`setdefault` the second time round and the 11-second startup came straight back. PyInstaller's
runtime hook has *already* set `MPLCONFIGDIR` by the time the launcher's own code runs, so there is
nothing to default and the temp directory stays. It has to be a plain assignment. This is the kind of
bug that looks like the fix did not work rather than like a mistake, so it is worth knowing about
before you go looking in the wrong place. `test_matplotlib_cache_overrides_pyinstallers_temp_dir`
pins it.

**3. Excluding `unittest` to save space breaks matplotlib.** `pyparsing.testing` imports `unittest`
at module load, so matplotlib fails to import entirely:

```
ModuleNotFoundError: No module named 'unittest'
```

An obvious-looking size optimization that produces an app that will not start.

**4. PyInstaller resolves imports against the spec file's directory.** I had a scratch `timeit.py`
sitting next to the `.spec`, and PyInstaller bundled *it* in place of the stdlib `timeit` that
fontTools imports, producing a baffling crash deep inside matplotlib. Keep the build directory clean,
and never name a helper script after a stdlib module.

## Code changes (done)

Small, and entirely additive — nothing in `app/main.py`, `app/db.py`, `app/pdf.py` or `app/export.py`
changed.

**`app/desktop.py`, new** — the launcher the `.app` runs. `app.main.run()` is untouched and still
serves `uv run grader`. The launcher points `GRADER_DATA` at
`~/Library/Application Support/MathTestGrader` (a frozen app must never write inside its own bundle:
it breaks the code signature and is wiped by the next update), assigns `MPLCONFIGDIR`, takes a port
from the OS, imports a previous install's data on first run, and holds an `flock` so a second
double-click focuses the running instance instead of starting a second server.

**`Grader.spec`, new** — the build, with the `unittest` trap documented in place so nobody
helpfully adds it to `excludes` later.

**`pyproject.toml`** — a `grader-desktop` entry point and a `build` dependency group for PyInstaller.

**`app/main.py:22` — `STATIC` needed nothing.** `db.ROOT / "static"` lands on
`Contents/Frameworks/static`, which is exactly where the spec puts it. Verified: `exists=True`.

**`app/db.py:99` — `data_dir()` needed nothing either**, because the launcher sets `GRADER_DATA`
before anything reads it. Worth knowing that its `ROOT / "data"` default would otherwise resolve
*inside the bundle*; it is only harmless because nothing frozen ever reaches it.

**The frontend needs nothing.** I grepped `static/js` and `static/*.html` for hardcoded hosts and
ports and found none — every request is relative. A random port just works. This is the single
biggest reason the port is cheap.

**Quitting — solved by giving the app a window.** The bundle started as a windowless server, which
needed some handle that was not "find the process". A menu bar item was the first answer and a bad
one (see *The bug that only a real install found*). The app now shows its screens in a window of its
own, and closing that window quits — the ordinary macOS gesture, nothing to explain.

The structural consequence is that uvicorn moves to a background thread, because the main thread has
to hold the window's run loop. uvicorn's signal handlers can only be installed on the main thread,
so they are turned off and stopping is `server.should_exit` instead.

## The bug that only a real install found

Worth recording, because every automated check passed and the app was still broken for the person
using it.

Installed to `/Applications` and double-clicked, it did nothing: no window, no menu bar item,
nothing obvious in Activity Monitor, and macOS refused to let the app be deleted because it was
"open". It was in fact running perfectly and serving on its port the whole time.

Two mistakes compounded:

- **`LSUIElement` made it invisible.** No Dock icon, no app switcher entry. The only handle was the
  menu bar item.
- **The menu bar item was never on screen.** When the menu bar is full, macOS does not refuse to
  create a status item — it parks it off-screen. Probing the live `NSStatusItem` showed
  `isVisible: True`, `title: MG`, and a window frame at **x = -4205**. A hidden status item and one
  that was never created look identical from the outside, which is why this was hard to pin down.

The fix, in the end, was to stop having a windowless app at all: the screens now open in a window of
their own, which cannot be hidden, quits when closed, and needs no explaining. The menu bar item is
gone entirely, and `rumps` with it.

Two things changed for the better as a result:

- **The app now logs.** A double-clicked app has nowhere to print, so its stdout goes nowhere and
  any failure reads as "nothing happened". Every launch now appends to `grader.log` beside the data,
  including where the status item landed.
- **Failures are loud.** `run_menu_bar` catches every exception rather than only `ImportError` —
  the original code would silently fall through to a headless server with no way to quit it — and if
  the menu bar cannot start, the app says so in a dialog instead of vanishing.

The general lesson: a GUI app that cannot tell you what it did is untestable by anyone but its
author, and "it passed on my machine" meant very little here. The first thing to build for a
double-clicked app is its log.

## The window

The app is not a browser tab. The screens are drawn by WebKit in a plain window (pywebview,
1280×860, resizable), served over the same local HTTP as always. The UI needed **no changes at
all** — every request in `static/` was already relative, so nothing in it knows or cares that it is
not in a browser.

What this costs and what it takes care of:

| | |
|---|---|
| Added to the bundle | ~3 MB (pyobjc was already there) |
| UI changes required | none |
| Uploads (`<input type="file">`, incl. `multiple`) | native open panel, via pywebview's Cocoa backend |
| CSV and zip downloads | save panel — needs `ALLOW_DOWNLOADS`, **off by default** |
| Pasting the roster (⌘V) | works: pywebview builds an Edit menu and handles the keys itself |
| Quitting | close the window |

Verified by reading the DOM back out of the running window, on the real data:

```
assignment: file inputs (uploads)      3      (one of them multiple)
organize:   draggable page cells       290
organize:   scan images loaded         290
grade:      rubric comments listed     14     (all draggable)
grade:      KaTeX rendered in rubric   18
grade:      annotation boxes on page   2
grade:      score box                  9 / 12
JS errors                              none
```

The two settings worth remembering are that **downloads are off by default** in pywebview, and that
`run_window` must fall back rather than raise: a server left running behind a window that never
opened is unreachable, which is the same failure as the menu bar one in a different costume.

A native rewrite — AppKit or SwiftUI controls instead of WebKit — is a different project entirely:
seven screens, the drag-and-drop organizer, annotation placement and KaTeX, and no browser to fall
back on. Weeks, not a day, and nothing here rules it out later.

## Packaging tool choice

**PyInstaller is the right answer here, and it is what I used.** It handled PyMuPDF's three
`.dylib`s and matplotlib's data files with no hand-written hooks, ad-hoc signed the bundle
automatically, and produced a working `.app` on the first build. The only friction was the three
issues above, all of which are now solved.

The alternatives, honestly assessed:

- **py2app** — macOS-only, works, but a smaller community and fussier with binary wheels. No
  advantage over PyInstaller unless you want the Mac App Store, which you do not (sandboxing
  would fight the "read PDFs from anywhere, write exports anywhere" workflow).
- **Briefcase (BeeWare)** — the most *official* path and the only documented Mac App Store route.
  Nicer project scaffolding and cross-platform story. Worth a look only if Windows becomes a
  requirement; it is more ceremony for a single-platform app.
- **Nuitka** — compiles to C. Smaller and faster in principle, much slower builds and more ways for a
  binary-heavy dependency like PyMuPDF to go wrong. Not worth it; your startup problem was the font
  cache, not the interpreter.
- **Tauri / Electron with a Python sidecar** — also gives a real window, at the cost of a second
  toolchain (Rust or Node) and a bigger bundle. pywebview does the same job here for ~3 MB and no
  second language, which is why the window is built on it.

## Getting under 117 MB

You probably do not need to. 62 MB is an unremarkable download in 2026 and teachers download bigger
things routinely. But if you want it smaller:

- **Drop matplotlib (~25 MB, the biggest realistic win).** It is used in exactly one place —
  `render_label` in `app/export.py` — for `$...$` math in rubric comments, and it drags in numpy,
  Pillow, fontTools, contourpy, kiwisolver and pyparsing behind it. It is also what makes your first
  launch slow. Replacing it means rendering math another way, and there is no drop-in: PyMuPDF can
  set text but not typeset LaTeX. Only worth it if you decide math in comments is a nice-to-have.
  I would keep it.
- **Prune matplotlib's `mpl-data` fonts and sample data.** A few MB, safe, ~an hour. Mind finding
  #2 above.
- **PyMuPDF (47 MB) is not reducible.** It is MuPDF itself and it is load-bearing for every screen.

## Distribution, which is where the actual difficulty is

This is the part worth your attention.

**Ad-hoc signing is not enough.** I ran Gatekeeper against the bundle PyInstaller produced:

```
$ spctl -a -vvv Grader.app
Grader.app: rejected
```

And, as of macOS Sequoia, Apple **removed the old right-click → Open shortcut** that let people
past this. A teacher who downloads an unsigned build now has to open System Settings → Privacy &
Security, scroll to the bottom, and click "Open Anyway" — after first being told the app cannot be
opened. For a non-technical audience that is not meaningfully better than the terminal you are
trying to escape. It is arguably worse, because it looks like a virus warning.

**The fix is $99/year.** An Apple Developer Program membership includes the Developer ID certificate;
you sign with it and run the bundle through `xcrun notarytool`, then `xcrun stapler staple` the
ticket onto the `.dmg`. After that it opens with a normal double-click and no warnings. Notarization
is automated and takes a few minutes per build. This machine currently has **no signing identities**
(`security find-identity` returns "0 valid identities found"), so this is step one whenever you
decide to do it.

There is no free path that produces a clean first-run experience. Homebrew casks still require the
terminal. Distributing a `.zip` does not change Gatekeeper's mind.

**Architecture.** PyMuPDF and matplotlib ship separate `x86_64` and `arm64` macOS wheels and no
`universal2` wheel, so there is no clean way to build one bundle for both — you would be
`lipo`-merging MuPDF's dylibs by hand, which is not worth it. Ship **arm64 only**. Any Mac sold since
late 2020 is Apple Silicon, and Intel is on its way out industry-wide: GitHub's Intel macOS runners
are scheduled to disappear in August 2027. If a colleague turns up with a 2019 MacBook, build them a
second `.dmg` from an Intel runner; do not make it the default.

**Build automation.** A GitHub Actions workflow on `macos-15` (arm64) that runs PyInstaller, signs,
notarizes and uploads the `.dmg` to a Release is maybe 60 lines, and means a new version is a
`git tag` away. Certificate and notarization credentials go in repository secrets. Do this once you
have the developer account, not before.

**Updates.** Teachers will not re-download manually. Cheapest credible option is a version check
against the GitHub Releases API on startup with a "new version available" link. Full auto-update
(Sparkle) is real work and not worth it at this scale.

## Does existing `./data/` carry over?

Yes, completely. Verified by pointing the packaged app at a copy of the real `./data/` on this
machine (2 courses, 47 students, 45 submissions, 82 annotations) and exercising it.

**Nothing in the database is tied to a location.** I scanned every `TEXT` column in every table for
absolute paths and found none. The only path-shaped values are LaTeX fractions in comment text
(`$\pi/2$`, `$\sqrt{3}/2$`) and `batches.filename`, which keeps the scanner's original filename for
display only. Files are addressed by the random key (`a3_54207c1f`, `b4_faebdeeb0120`) and resolved
against `data_dir()` at runtime, so moving the folder moves everything with it.

Against the copied data, the packaged app served the real courses, computed the real scores
(assignment 4: 8 problems, 90 points, 29 graded tests), served scanned page images, and exported all
29 annotated PDFs. The cover page of `Burke_Owen.pdf` came out with its score box intact — per
problem down the side, `Total 87 / 90`, matching the API's 96.7%.

**This is now automatic** — see `import_if_first_run` in `app/desktop.py`. On first launch, when
`~/Library/Application Support/MathTestGrader` has no database yet, the app looks for a checkout's
`data/grader.db` under the home directory (most recently used one wins, `GRADER_IMPORT_FROM`
overrides) and imports it. It runs once: after that the support folder has a database and the check
short-circuits, so work done since is never overwritten.

Two details that made the implementation non-obvious:

- **The database is copied with sqlite's backup API, not as a file.** `db.connect()` sets
  `journal_mode = WAL`, so recent writes can still be sitting in `grader.db-wal`. Copying
  `grader.db` alone would silently drop the most recent grading — and because the WAL is 0 bytes
  when idle, it would appear to work almost every time, which is what makes it dangerous.
  `Connection.backup()` takes a consistent snapshot including the log, and does it **without**
  writing to the source: no checkpoint, source opened read-only, original untouched. There is a test
  for exactly this (`test_import_takes_writes_still_in_the_write_ahead_log`) that holds a connection
  open so the write is stranded in the WAL, then asserts the import still sees it.
- **`exports/` and `fixtures/` are deliberately skipped.** Exports regenerate from the Results
  screen and fixtures are throwaway. Skipping them takes the import from 127 MB to 108 MB.

**The two copies then drift.** This is the one thing to keep in mind: after importing, `uv run
grader` still uses `./data/` and the app uses Application Support. They are independent from that
moment on, and grading done in one will not appear in the other. Pick one and stay there. If you
want the dev server to keep working against the same data as the app:

```sh
GRADER_DATA=~/Library/"Application Support"/MathTestGrader uv run grader
```

## Suggested order of work

1. **Confirm the $99 is acceptable.** Everything else is contingent. If it is not, stop here — the
   packaging is achievable but the distribution experience will be poor enough that a well-written
   README may genuinely serve teachers better.
2. ~~**`app/desktop.py` plus `Grader.spec`**~~ — **done.** Free port, `GRADER_DATA` and
   `MPLCONFIGDIR` into `~/Library/Application Support/MathTestGrader`, single-instance guard.
3. ~~**First-run import of an existing `./data/` folder**~~ — **done**, via sqlite's backup API
   rather than a checkpoint, so the source is never written to. 20 tests cover both.
4. ~~**A way to quit, and somewhere to see that it is running.**~~ — **done**, by giving the app its
   own window: closing it quits. A menu bar item was tried first and removed.
5. ~~**Icon**~~ — **done**, as a placeholder: `scripts/make_icon.py` draws an accent square with
   "MG" and builds `Grader.icns`. A real icon and a DMG background are still worth half a day.
6. **Sign, notarize, staple; hand the `.dmg` to one teacher and watch them install it.** A day,
   most of it spent on Apple's tooling the first time.
7. **GitHub Actions release workflow.** Half a day, once the manual path works.

Call it **three to four days** of focused work to a `.dmg` you would be comfortable emailing to a
colleague, plus the developer account. Steps 2 through 5 are done, so what is left is Apple's
tooling and, if you want it, a real icon — **about a day**, gated on the developer account.

## One caveat worth stating

Everything above was verified on this machine and this branch. Two things I did *not* test, because
they need hardware or an account I do not have: the signed-and-notarized launch on a machine that
has never seen the app (the thing that actually matters to your teachers), and behaviour on an
Intel Mac. Both are expected to work, but "expected" is doing real work in that sentence — test the
notarized build on someone else's Mac before sending it widely.

The menu-bar bug above is the reason to take that seriously. Every automated check passed on a build
that was unusable once installed, and it took someone actually double-clicking it to find out. Watch
the first teacher install it rather than asking them whether it worked.

Separately: the server binds to `127.0.0.1` and has no authentication, which is correct for a local
tool and stays correct when it is packaged. Nothing here changes the app's security posture. Student
grades will now live in `~/Library/Application Support/` instead of a folder the teacher chose, which
is the right default but is worth a line in the README, since that folder is not backed up by
default unless they use Time Machine or iCloud Desktop.

## Appendix: building it

The launcher is `app/desktop.py` and the build is driven by `Grader.spec`, both committed.

```sh
uv run --group build pyinstaller --noconfirm --clean Grader.spec   # -> dist/Grader.app

mkdir -p build/dmgroot && cp -R dist/Grader.app build/dmgroot/
ln -s /Applications build/dmgroot/Applications
hdiutil create -volname "Math Test Grader" -srcfolder build/dmgroot -ov -format UDZO Grader.dmg
```

`scripts/make_icon.py` regenerates `Grader.icns`, and `uv run grader-desktop --no-menu-bar` runs the
same launcher unfrozen, which is the quick way to check the import and single-instance behaviour
without waiting for a build.

Still to do before this goes to anyone else: signing and notarization, and a real icon in place of
the "MG" placeholder.

---

*Sources consulted for the distribution section: [Apple — Notarizing macOS software before
distribution](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution),
[Apple Developer Program membership details](https://developer.apple.com/programs/whats-included/),
[AppleInsider — Sequoia removes the Control-click Gatekeeper
override](https://appleinsider.com/articles/24/08/06/apple-removes-control-click-option-for-skipping-gatekeeper-in-macos-sequoia),
[GitHub — macOS runner image
deprecation](https://github.com/actions/runner-images/issues/13027).*
