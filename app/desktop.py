"""Entry point for the packaged Mac app.

`app.main.run()` still serves `uv run grader`. This module is what `Grader.app` launches, and it
differs in four ways, each forced by being a double-clicked app rather than a terminal command:

- User data goes to `~/Library/Application Support/MathTestGrader`. A frozen app must never write
  inside its own bundle: it breaks the code signature and is wiped by the next update.
- The port is chosen by the OS. Port 8000 may be taken, and nothing in `static/` assumes a port.
- Matplotlib gets a persistent cache directory. PyInstaller's hook points `MPLCONFIGDIR` at a temp
  directory that is discarded on exit, so the font cache is rebuilt on every launch (about 11
  seconds). Pointing it somewhere that survives makes that a one-time cost.
- A `data/` folder from a source checkout is imported on first run, so an existing install keeps
  its courses, scans and grading.

The screens are shown in the app's own window, not a browser tab: the same pages, served over
127.0.0.1 as always, drawn by WebKit in a plain window with no address bar. The window is the app's
handle -- closing it quits -- which is why there is no menu bar item and no `LSUIElement`.

The server therefore runs on a background thread, because the main thread has to hold the window's
run loop. `--browser` falls back to the default browser if the window ever misbehaves, and
`--headless` just serves, which is how it runs under test.

Double-clicking while it is already running focuses the existing instance instead of starting a
second server on a second port.
"""

import fcntl
import os
import socket
import sqlite3
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

APP_NAME = "MathTestGrader"
WINDOW_TITLE = "Math Test Grader"

# Wide enough for the grading screen, which puts the page, the rubric and an optional answer key
# column side by side. Capped to the screen at run time: a 13" laptop is 1440x900, and asking for
# more than fits leaves macOS to clamp it to something arbitrary.
WINDOW_SIZE = (1280, 860)
WINDOW_MIN_SIZE = (900, 600)
SCREEN_FRACTION = (0.94, 0.88)  # leave room for the menu bar and the Dock

# Copied from a previous install; `exports/` regenerates from the Results screen and `fixtures/`
# is throwaway test data, so neither is worth moving.
IMPORTED = ("uploads", "pages")

# Where a source checkout is likely to be. Searched only when there is nothing to open yet.
SEARCH_ROOTS = ("", "Projects", "Developer", "Documents", "Desktop", "src", "code", "repos")
SEARCH_PATTERNS = (
    "math-test-grader/data/grader.db",
    "*/math-test-grader/data/grader.db",
    "*/*/math-test-grader/data/grader.db",
)


def support_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / APP_NAME


def log(message: str) -> None:
    """Append to grader.log beside the data.

    A double-clicked app has nowhere to print: stdout goes to the void, so a failure looks like
    "nothing happened". Every launch leaves a trace here instead.
    """
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}\n"
    try:
        with (support_dir() / "grader.log").open("a") as f:
            f.write(line)
    except OSError:
        pass
    print(line.rstrip(), file=sys.stderr)


# ---------------------------------------------------------------- environment


def prepare_environment(support: Path) -> None:
    """Point the app at writable storage. Must run before `app.main` (and so matplotlib) is
    imported, and before anything calls `db.data_dir()`."""
    support.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("GRADER_DATA", str(support))
    cache = support / "mplcache"
    cache.mkdir(parents=True, exist_ok=True)
    # Assigned, not defaulted: PyInstaller's runtime hook has already pointed MPLCONFIGDIR at a
    # temp directory by the time this runs, so setdefault would leave that in place and matplotlib
    # would rebuild its font cache on every launch.
    os.environ["MPLCONFIGDIR"] = str(cache)


# ---------------------------------------------------------------- importing a previous install


def find_previous_data() -> Path | None:
    """The `data/` folder of a source checkout, or None. `GRADER_IMPORT_FROM` names one directly."""
    named = os.environ.get("GRADER_IMPORT_FROM")
    if named:
        path = Path(named).expanduser()
        return path if (path / "grader.db").is_file() else None

    home = Path.home()
    found: list[Path] = []
    for root in (home / r if r else home for r in SEARCH_ROOTS):
        if not root.is_dir():
            continue
        for pattern in SEARCH_PATTERNS:
            try:
                found += [db for db in root.glob(pattern) if db.is_file()]
            except OSError:  # unreadable directory somewhere along the way
                continue
    if not found:
        return None
    # The most recently used checkout is the one they have been grading in.
    return max(found, key=lambda db: db.stat().st_mtime).parent


def import_previous_data(source: Path, support: Path) -> None:
    """Copy a previous install's data in. The source is opened read-only and never modified.

    The database is copied with sqlite's backup API rather than as a file, because `db.connect()`
    sets `journal_mode = WAL`: recent writes can still be sitting in `grader.db-wal`, and copying
    `grader.db` alone would silently drop the most recent grading. The backup takes a consistent
    snapshot that includes the log, without checkpointing (and so without writing to) the original.
    """
    src = sqlite3.connect(f"file:{source / 'grader.db'}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(support / "grader.db")
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    for name in IMPORTED:
        folder = source / name
        if not folder.is_dir():
            continue
        for item in folder.rglob("*"):
            if not item.is_file():
                continue
            target = support / name / item.relative_to(folder)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item.read_bytes())


def import_if_first_run(support: Path) -> Path | None:
    """Import a previous install's data, but only into an empty one. Returns what was imported."""
    if (support / "grader.db").is_file():
        return None
    source = find_previous_data()
    if source is None or source.resolve() == support.resolve():
        return None
    import_previous_data(source, support)
    return source


# ---------------------------------------------------------------- one instance at a time


def claim_single_instance(support: Path):
    """Hold an exclusive lock for this process's lifetime, or return None if another holds it.

    The returned file must stay referenced: closing it drops the lock.
    """
    handle = open(support / "grader.lock", "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def running_url(support: Path) -> str | None:
    try:
        url = (support / "grader.url").read_text().strip()
    except OSError:
        return None
    return url or None


# ---------------------------------------------------------------- serving


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_until_serving(port: int, timeout: float = 30) -> bool:
    """True once the server answers. The window must not load before there is anything to load."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            s.settimeout(0.2)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.05)
    return False


def start_server(api_app, port: int):
    """Serve on a background thread, leaving the main thread free to hold the window's run loop.

    uvicorn installs signal handlers, which only the main thread may do, so that is turned off;
    stopping is `should_exit` instead.
    """
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(api_app, host="127.0.0.1", port=port, log_level="warning"))
    server.install_signal_handlers = lambda: None
    threading.Thread(target=server.run, daemon=True).start()
    return server


def serve_until_interrupted(server) -> None:
    """Block with no window: the terminal case, where Ctrl-C is how it ends."""
    try:
        while not server.should_exit:
            time.sleep(0.2)
    except KeyboardInterrupt:
        server.should_exit = True


def window_size(webview) -> tuple[int, int]:
    """The preferred size, shrunk to fit the screen but never below the minimum."""
    try:
        screen = webview.screens[0]
        fitted = (
            min(WINDOW_SIZE[0], int(screen.width * SCREEN_FRACTION[0])),
            min(WINDOW_SIZE[1], int(screen.height * SCREEN_FRACTION[1])),
        )
    except Exception:
        log("could not read the screen size:\n" + traceback.format_exc())
        return WINDOW_SIZE
    return max(fitted[0], WINDOW_MIN_SIZE[0]), max(fitted[1], WINDOW_MIN_SIZE[1])


def run_window(server, url: str) -> bool:
    """Show the screens in the app's own window and hold the main thread until it closes.

    False if the window could not be opened at all, so the caller can fall back to a browser rather
    than leave a server running behind nothing.

    Downloads are off by default in pywebview, and the Results screen needs them for the CSV and the
    exported zip: both are plain links whose response says `Content-Disposition: attachment`.
    """
    try:
        import webview
    except ImportError:
        log("pywebview is not installed; falling back to the browser")
        return False

    try:
        webview.settings["ALLOW_DOWNLOADS"] = True
        width, height = window_size(webview)
        webview.create_window(
            WINDOW_TITLE,
            url,
            width=width,
            height=height,
            min_size=WINDOW_MIN_SIZE,
            text_select=True,
        )
        log(f"window opening at {width}x{height}")
        webview.start()  # returns once the last window is closed
        log("window closed")
        server.should_exit = True
        return True
    except Exception:
        log("window failed:\n" + traceback.format_exc())
        return False


def main() -> None:
    support = support_dir()
    support.mkdir(parents=True, exist_ok=True)
    browser = "--browser" in sys.argv  # escape hatch if the window ever misbehaves
    headless = "--headless" in sys.argv  # serve only: tests and automation

    lock = claim_single_instance(support)
    if lock is None:
        # Already running. A second double-click from Finder or the Dock activates that window by
        # itself; this is the command line case, and the browser is the only way to surface it.
        existing = running_url(support)
        if existing and browser:
            webbrowser.open(existing)
        print(f"Already running at {existing or 'another window'}.", file=sys.stderr)
        return

    prepare_environment(support)
    log(f"starting (frozen={getattr(sys, 'frozen', False)})")
    imported = import_if_first_run(support)
    if imported:
        log(f"imported existing data from {imported}")

    from app import main as api

    port = free_port()
    url = f"http://127.0.0.1:{port}"
    (support / "grader.url").write_text(url)
    log(f"serving {url} from {os.environ['GRADER_DATA']}")

    server = start_server(api.app, port)
    try:
        if not wait_until_serving(port):
            log("the server never came up")
            return
        if headless:
            serve_until_interrupted(server)
        elif browser:
            webbrowser.open(url)
            serve_until_interrupted(server)
        elif not run_window(server, url):
            # The window is the whole app; without it, fall back rather than leave a server
            # running behind nothing the user can see.
            webbrowser.open(url)
            serve_until_interrupted(server)
    finally:
        server.should_exit = True
        (support / "grader.url").unlink(missing_ok=True)
        log("stopped")


if __name__ == "__main__":
    main()
