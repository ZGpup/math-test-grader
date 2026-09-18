"""The packaged app's launcher: importing a previous install, and running only once."""

import os
import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from app import db, desktop


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An empty home directory, so the search for a previous install finds only what a test puts
    there and never a real checkout on the machine running the tests."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("GRADER_IMPORT_FROM", raising=False)
    return tmp_path


def make_install(path: Path, course: str = "Algebra II") -> Path:
    """A `data/` folder like a source checkout's, with one course in it."""
    path.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path / "grader.db")
    try:
        conn.executescript(db.SCHEMA)
        conn.execute("INSERT INTO courses (name, roster_text) VALUES (?, '')", (course,))
        conn.commit()
    finally:
        conn.close()
    return path


def courses(path: Path) -> list[str]:
    conn = sqlite3.connect(f"file:{path / 'grader.db'}?mode=ro", uri=True)
    try:
        return [r[0] for r in conn.execute("SELECT name FROM courses")]
    finally:
        conn.close()


def support_in(home: Path) -> Path:
    path = home / "Library" / "Application Support" / "MathTestGrader"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------- finding a previous install


def test_finds_a_checkout_under_home(home):
    data = make_install(home / "Projects" / "math-test-grader" / "data")
    assert desktop.find_previous_data() == data


def test_finds_a_nested_checkout(home):
    data = make_install(home / "Projects" / "Personal" / "math-test-grader" / "data")
    assert desktop.find_previous_data() == data


def test_finds_nothing_when_there_is_nothing(home):
    assert desktop.find_previous_data() is None


def test_ignores_a_checkout_with_no_database(home):
    (home / "Projects" / "math-test-grader" / "data").mkdir(parents=True)
    assert desktop.find_previous_data() is None


def test_explicit_source_wins(home, tmp_path, monkeypatch):
    make_install(home / "Projects" / "math-test-grader" / "data")
    named = make_install(tmp_path / "elsewhere")
    monkeypatch.setenv("GRADER_IMPORT_FROM", str(named))
    assert desktop.find_previous_data() == named


def test_explicit_source_without_a_database_is_ignored(home, tmp_path, monkeypatch):
    monkeypatch.setenv("GRADER_IMPORT_FROM", str(tmp_path / "nothing here"))
    assert desktop.find_previous_data() is None


def test_picks_the_most_recently_used_checkout(home):
    old = make_install(home / "archive" / "math-test-grader" / "data", course="Old")
    new = make_install(home / "math-test-grader" / "data", course="New")
    stale = time.time() - 10_000
    os.utime(old / "grader.db", (stale, stale))
    assert desktop.find_previous_data() == new


# ---------------------------------------------------------------- importing


def test_imports_database_and_files(home, tmp_path):
    source = make_install(tmp_path / "data")
    (source / "uploads").mkdir()
    (source / "uploads" / "a1_abc.pdf").write_bytes(b"%PDF-1.4 fake")
    (source / "pages" / "a1_abc").mkdir(parents=True)
    (source / "pages" / "a1_abc" / "0000.png").write_bytes(b"png")

    support = support_in(home)
    desktop.import_previous_data(source, support)

    assert courses(support) == ["Algebra II"]
    assert (support / "uploads" / "a1_abc.pdf").read_bytes() == b"%PDF-1.4 fake"
    assert (support / "pages" / "a1_abc" / "0000.png").read_bytes() == b"png"


def test_exports_and_fixtures_are_left_behind(home, tmp_path):
    source = make_install(tmp_path / "data")
    (source / "exports").mkdir()
    (source / "exports" / "old.zip").write_bytes(b"zip")
    (source / "fixtures").mkdir()
    (source / "fixtures" / "blank.pdf").write_bytes(b"pdf")

    support = support_in(home)
    desktop.import_previous_data(source, support)

    assert not (support / "exports").exists()
    assert not (support / "fixtures").exists()


def test_import_does_not_modify_the_source(home, tmp_path):
    source = make_install(tmp_path / "data")
    before = (source / "grader.db").read_bytes()
    desktop.import_previous_data(source, support_in(home))
    assert (source / "grader.db").read_bytes() == before


def test_import_takes_writes_still_in_the_write_ahead_log(home, tmp_path):
    """Copying grader.db as a file would lose the most recent grading; the backup API does not."""
    source = tmp_path / "data"
    source.mkdir(parents=True)
    conn = sqlite3.connect(source / "grader.db")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(db.SCHEMA)
        conn.execute("INSERT INTO courses (name, roster_text) VALUES ('In the log', '')")
        conn.commit()
        # The connection stays open, so the write sits in grader.db-wal rather than grader.db --
        # the state a dev server that is still running leaves behind.
        assert (source / "grader.db-wal").stat().st_size > 0, "test needs an unchecked WAL"

        support = support_in(home)
        desktop.import_previous_data(source, support)
        assert courses(support) == ["In the log"]
    finally:
        conn.close()


def test_first_run_imports_once(home):
    make_install(home / "math-test-grader" / "data")
    support = support_in(home)

    assert desktop.import_if_first_run(support) is not None
    assert courses(support) == ["Algebra II"]

    # A second launch must not import over the top of work done since.
    conn = sqlite3.connect(support / "grader.db")
    conn.execute("INSERT INTO courses (name, roster_text) VALUES ('Added later', '')")
    conn.commit()
    conn.close()

    assert desktop.import_if_first_run(support) is None
    assert courses(support) == ["Algebra II", "Added later"]


def test_no_import_when_there_is_nothing_to_import(home):
    support = support_in(home)
    assert desktop.import_if_first_run(support) is None
    assert not (support / "grader.db").exists()


def test_a_support_folder_does_not_import_itself(home, monkeypatch):
    support = support_in(home)
    make_install(support)
    monkeypatch.setenv("GRADER_IMPORT_FROM", str(support))
    assert desktop.import_if_first_run(support) is None


# ---------------------------------------------------------------- one instance at a time


def test_a_second_process_does_not_get_the_lock(home):
    support = support_in(home)
    first = desktop.claim_single_instance(support)
    assert first is not None
    try:
        # flock is held per open file description, so only a separate process is a real test.
        code = textwrap.dedent(f"""
            import sys
            from pathlib import Path
            sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
            from app import desktop
            print(desktop.claim_single_instance(Path({str(support)!r})) is None)
        """)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert out.stdout.strip() == "True", out.stderr
    finally:
        first.close()


def test_lock_is_released_when_the_app_stops(home):
    support = support_in(home)
    handle = desktop.claim_single_instance(support)
    assert handle is not None
    handle.close()
    again = desktop.claim_single_instance(support)
    assert again is not None
    again.close()


def test_running_url_round_trips(home):
    support = support_in(home)
    assert desktop.running_url(support) is None
    (support / "grader.url").write_text("http://127.0.0.1:54321")
    assert desktop.running_url(support) == "http://127.0.0.1:54321"


# ---------------------------------------------------------------- environment


def test_prepare_environment_points_at_writable_storage(home, monkeypatch):
    monkeypatch.delenv("GRADER_DATA", raising=False)
    monkeypatch.delenv("MPLCONFIGDIR", raising=False)
    support = support_in(home)
    desktop.prepare_environment(support)
    assert os.environ["GRADER_DATA"] == str(support)
    assert Path(os.environ["MPLCONFIGDIR"]) == support / "mplcache"
    assert Path(os.environ["MPLCONFIGDIR"]).is_dir()


def test_matplotlib_cache_overrides_pyinstallers_temp_dir(home, monkeypatch, tmp_path):
    """PyInstaller's runtime hook sets MPLCONFIGDIR to a temp dir that is discarded on exit, so
    leaving it alone costs an ~11s font cache rebuild on every single launch."""
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "_MEIsomething" / "matplotlib"))
    support = support_in(home)
    desktop.prepare_environment(support)
    assert Path(os.environ["MPLCONFIGDIR"]) == support / "mplcache"


def test_prepare_environment_respects_an_explicit_data_dir(home, monkeypatch, tmp_path):
    monkeypatch.setenv("GRADER_DATA", str(tmp_path / "chosen"))
    desktop.prepare_environment(support_in(home))
    assert os.environ["GRADER_DATA"] == str(tmp_path / "chosen")


# ---------------------------------------------------------------- serving and quitting


class FakeServer:
    """uvicorn.Server as far as the launcher is concerned."""

    def __init__(self):
        self.should_exit = False


def test_serve_until_interrupted_returns_when_the_server_stops():
    server = FakeServer()
    threading.Timer(0.1, lambda: setattr(server, "should_exit", True)).start()
    desktop.serve_until_interrupted(server)  # returns rather than hanging
    assert server.should_exit


def test_ctrl_c_stops_the_server(monkeypatch):
    server = FakeServer()
    monkeypatch.setattr(desktop.time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt))
    desktop.serve_until_interrupted(server)
    assert server.should_exit


def fake_webview(monkeypatch, on_start=None):
    """Stand in for pywebview, recording how the window was asked for."""
    created = {}

    def create_window(title, url, **kwargs):
        created.update({"title": title, "url": url}, **kwargs)
        return object()

    fake = type(sys)("webview")
    fake.settings = {}
    fake.create_window = create_window
    fake.start = on_start or (lambda: None)
    monkeypatch.setitem(sys.modules, "webview", fake)
    return fake, created


def test_window_falls_back_when_pywebview_is_missing(monkeypatch):
    """Without pywebview the launcher must fall back to the browser, not crash."""
    monkeypatch.setitem(sys.modules, "webview", None)  # import webview -> ImportError
    assert desktop.run_window(FakeServer(), "http://127.0.0.1:1") is False


def test_window_falls_back_when_it_cannot_open(monkeypatch):
    """A failure here must not leave a server running behind a window that never appeared."""

    def explode():
        raise RuntimeError("no display")

    fake_webview(monkeypatch, on_start=explode)
    assert desktop.run_window(FakeServer(), "http://127.0.0.1:1") is False


def test_closing_the_window_stops_the_server(monkeypatch):
    """The window is the app: when it closes, uvicorn has to stop too."""
    server = FakeServer()
    fake_webview(monkeypatch)  # start() returns at once, as it does when the last window closes
    assert desktop.run_window(server, "http://127.0.0.1:1") is True
    assert server.should_exit, "closing the window left the server running"


def test_window_enables_downloads(monkeypatch):
    """The Results screen's CSV and zip are Content-Disposition links; pywebview blocks those
    unless downloads are turned on."""
    fake, _ = fake_webview(monkeypatch)
    desktop.run_window(FakeServer(), "http://127.0.0.1:1")
    assert fake.settings.get("ALLOW_DOWNLOADS") is True


def test_window_opens_the_served_url(monkeypatch):
    fake, created = fake_webview(monkeypatch)
    fake.screens = [type("S", (), {"width": 3840, "height": 2160})()]
    desktop.run_window(FakeServer(), "http://127.0.0.1:7777")
    assert created["url"] == "http://127.0.0.1:7777"
    assert created["title"] == desktop.WINDOW_TITLE
    # The grading screen puts the page, the rubric and the answer key side by side.
    assert created["width"] == desktop.WINDOW_SIZE[0]
    assert created["min_size"] == desktop.WINDOW_MIN_SIZE


def test_window_shrinks_to_fit_a_small_screen(monkeypatch):
    """A 13" laptop is 1440x900; asking for more leaves macOS to clamp it to something arbitrary."""
    fake, created = fake_webview(monkeypatch)
    fake.screens = [type("S", (), {"width": 1440, "height": 900})()]
    desktop.run_window(FakeServer(), "http://127.0.0.1:1")
    assert created["width"] <= 1440 and created["height"] < 900
    assert created["height"] <= int(900 * desktop.SCREEN_FRACTION[1])


def test_window_never_shrinks_below_the_minimum(monkeypatch):
    fake, created = fake_webview(monkeypatch)
    fake.screens = [type("S", (), {"width": 800, "height": 500})()]
    desktop.run_window(FakeServer(), "http://127.0.0.1:1")
    assert (created["width"], created["height"]) == desktop.WINDOW_MIN_SIZE


def test_window_size_falls_back_when_the_screen_is_unreadable(monkeypatch):
    """Never let a screen query stop the window from opening."""

    class NoScreens:
        @property
        def screens(self):
            raise RuntimeError("no display")

    assert desktop.window_size(NoScreens()) == desktop.WINDOW_SIZE


def test_wait_until_serving_gives_up_rather_than_hanging():
    assert desktop.wait_until_serving(desktop.free_port(), timeout=0.3) is False


def test_wait_until_serving_sees_a_live_port():
    import socket as s

    sock = s.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        assert desktop.wait_until_serving(sock.getsockname()[1], timeout=5) is True
    finally:
        sock.close()
