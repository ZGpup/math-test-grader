# PyInstaller spec for Grader.app.
#
#   uv run --group build pyinstaller --noconfirm --clean Grader.spec
#
# Produces dist/Grader.app (arm64). PyMuPDF and matplotlib ship no universal2 wheels, so a bundle
# is built for the architecture it is built on; an Intel build needs an Intel machine or runner.

from pathlib import Path

PROJECT = Path(SPECPATH)

a = Analysis(
    [str(PROJECT / "app" / "desktop.py")],
    pathex=[str(PROJECT)],
    # app/main.py serves static/ from db.ROOT, which resolves to Contents/Frameworks when frozen.
    datas=[(str(PROJECT / "static"), "static")],
    # pywebview ships its own PyInstaller hook, which pulls in the Cocoa backend.
    hiddenimports=["app", "app.db", "app.pdf", "app.export", "app.main", "webview"],
    excludes=[
        # GUI toolkits, test suites and dev tooling that nothing here imports.
        #
        # Do NOT add "unittest": pyparsing.testing imports it at module load, so excluding it
        # makes matplotlib fail to import and the app will not start at all.
        "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "wx", "gtk",
        "IPython", "pytest", "_pytest", "setuptools", "pip", "pydoc_data",
        "matplotlib.backends.backend_qt5agg", "matplotlib.backends.backend_tkagg",
        "matplotlib.backends._backend_tk", "matplotlib.tests", "numpy.tests",
        "lib2to3", "distutils",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Grader", console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Grader")
app = BUNDLE(
    coll,
    name="Grader.app",
    icon=str(PROJECT / "Grader.icns"),  # placeholder; regenerate with scripts/make_icon.py
    bundle_identifier="dev.zgilmore.mathtestgrader",
    info_plist={
        "CFBundleName": "Math Test Grader",
        "CFBundleDisplayName": "Math Test Grader",
        "CFBundleShortVersionString": "0.1.0",
        # The app has a real window, so it belongs in the Dock like any other app. An earlier
        # menu-bar-only build set LSUIElement and was invisible when the menu bar was full.
        "LSUIElement": False,
        "NSHighResolutionCapable": True,
    },
)
