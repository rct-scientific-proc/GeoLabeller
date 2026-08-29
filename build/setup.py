"""
cx_Freeze setup script for GeoLabeller.

Usage:
    python setup.py build       # Build executable
    python setup.py bdist_msi   # Build MSI installer (Windows only)

Configuration via environment variables (set by build_windows.ps1):
    GEOLABELLER_VERSION       Version string "X.Y.Z" (default 1.0.0)
    GEOLABELLER_AUTHOR        Publisher shown in Add/Remove Programs
    GEOLABELLER_URL           About/help URL shown in Add/Remove Programs
    GEOLABELLER_MSI_SHORTCUT  If truthy, also install a Desktop shortcut
"""

import os
import sys
from pathlib import Path

from cx_Freeze import setup, Executable

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# --- Work around nondeterministic Qt bloat in cx_Freeze (8.7.0) -----------
#
# Three installed distributions share the "PyQt5" top-level package: PyQt5,
# PyQt5-Qt5 (the Qt binaries wheel) and PyQt5-sip. cx_Freeze binds the PyQt5
# module to ONE of them - chosen last-write-wins over a set() whose iteration
# order follows the process's randomized string hashing, i.e. a per-build
# coin flip. When PyQt5-Qt5 wins, Module.libs() ships EVERY DLL in that
# wheel's RECORD - all of Qt5/bin, Qt5/plugins and Qt5/qml, ~72MB of unused
# Qt (sql drivers, geoservices, multimedia, the software GL renderer...) on
# top of what the app needs. That is how the 1.7.1 MSI came out 24MB larger
# than 1.7.0 from an identical environment.
#
# The Qt files PyQt5 actually needs are provided by cx_Freeze's Qt hooks and
# the binary dependency walk, never by libs(); in a "slim" (lucky) build
# libs() yields nothing for PyQt5. Pin that behaviour: yield no libs for the
# PyQt5 root module regardless of which distribution won the coin flip.
# build_windows.ps1 asserts the result (no qml dir, no opengl32sw.dll), so
# if a future cx_Freeze changes shape the build fails loudly instead of
# silently shipping the fat installer again.
try:
    import cx_Freeze.module as _cx_module

    _original_libs = _cx_module.Module.libs

    def _libs_without_qt_wheel(self):
        if self.name == "PyQt5":
            return iter(())
        return _original_libs(self)

    _cx_module.Module.libs = _libs_without_qt_wheel
except (ImportError, AttributeError) as exc:  # pragma: no cover
    print(f"WARNING: PyQt5 libs() workaround not applied: {exc}")

# Version can be overridden via GEOLABELLER_VERSION environment variable
VERSION = os.environ.get("GEOLABELLER_VERSION", "1.0.0")
# Publisher / author shown in Add/Remove Programs (overridable).
AUTHOR = os.environ.get("GEOLABELLER_AUTHOR", "Ryan Todd")
# Optional "about"/help URL surfaced in Add/Remove Programs.
ABOUT_URL = os.environ.get("GEOLABELLER_URL", "").strip()
# A Desktop shortcut is opt-in; the Start Menu shortcut is always installed.
ENABLE_MSI_SHORTCUT = os.environ.get("GEOLABELLER_MSI_SHORTCUT", "").lower() in {"1", "true", "yes", "on"}

ICON_FILE = "geolabel_icon.ico"
# Windows executable name (cx_Freeze appends .exe on Windows).
TARGET_EXE = "GeoLabeller.exe"

# Locate rasterio's bundled PROJ + GDAL data directories. rasterio ships a
# newer PROJ than pyproj, so we bundle its copy; main.py points PROJ_DATA /
# GDAL_DATA at these folders when frozen.
import rasterio
_rasterio_dir = Path(rasterio.__file__).parent
proj_data_dir = _rasterio_dir / "proj_data"
gdal_data_dir = _rasterio_dir / "gdal_data"

# Data folders to bundle next to the executable (only if present).
include_files = []
if proj_data_dir.exists():
    include_files.append((str(proj_data_dir), "proj_data"))
if gdal_data_dir.exists():
    include_files.append((str(gdal_data_dir), "gdal_data"))

# app/version.py reads this to put the version in the window title, and a
# frozen build has no repository to read it from. Without it the application
# still runs, but calls itself plain "GeoLabeller".
_repo_root = Path(__file__).resolve().parent.parent
_version_file = _repo_root / "VERSION"
if _version_file.exists():
    include_files.append((str(_version_file), "VERSION"))

# Ships the Interface Control Document so Help > ICD has something to open.
# app/resources.py looks for it beside the executable, which is where
# include_files puts it - the docs/ directory itself is not copied.
_icd_file = _repo_root / "docs" / "GeoLabeller-ICD.pdf"
if _icd_file.exists():
    include_files.append((str(_icd_file), "GeoLabeller-ICD.pdf"))

# Dependencies to include
build_exe_options = {
    # Optimization level: 0=none, 1=basic (-O), 2=full (-OO removes docstrings)
    "optimize": 2,

    # Compress Python modules into a zip file for smaller distribution.
    "zip_include_packages": ["*"],
    # ...but keep packages that resolve data / binaries / plugins via __file__
    # OUTSIDE the zip. Zipping these is a common cause of runtime failures
    # (missing proj.db, numpy/rasterio extension DLLs, Qt plugins).
    "zip_exclude_packages": [
        "rasterio",
        "pyproj",
        "numpy",
        "PIL",
        "PyQt5",
        "h5py",
    ],

    "packages": [
        "PyQt5",
        "PyQt5.QtCore",
        "PyQt5.QtGui",
        "PyQt5.QtWidgets",
        "rasterio",
        "pyproj",
        "numpy",
        "PIL",
        "h5py",
        "affine",
        "json",
        "math",
        "pathlib",
        "traceback",
        "uuid",
        "enum",
        "xml",
    ],
    "excludes": [
        "tkinter",
        "unittest",
        "email",
        # NOT "html": app/shortcuts.py imports html.escape, and main_window
        # imports that at module scope, so excluding it does not trim a rarely
        # used corner - it stops the application starting at all. It shipped
        # that way in 1.2.1 and 1.3.0. build/check_frozen_imports.py now
        # fails the build if anything here is imported by the application.
        "pydoc",
        "doctest",
        "asyncio",
        "concurrent",
        "multiprocessing",
        "test",
        # Exclude Qt modules we don't use (avoid QML path issues)
        "PyQt5.QtQml",
        "PyQt5.QtQuick",
        "PyQt5.QtQuickWidgets",
        "PyQt5.QtNetwork",
        "PyQt5.QtSql",
        "PyQt5.QtMultimedia",
        "PyQt5.QtMultimediaWidgets",
        "PyQt5.QtBluetooth",
        "PyQt5.QtDesigner",
        "PyQt5.QtHelp",
        "PyQt5.QtLocation",
        "PyQt5.QtPositioning",
        "PyQt5.QtSensors",
        "PyQt5.QtSerialPort",
        "PyQt5.QtSvg",
        "PyQt5.QtTest",
        "PyQt5.QtWebChannel",
        "PyQt5.QtWebEngine",
        "PyQt5.QtWebEngineCore",
        "PyQt5.QtWebEngineWidgets",
        "PyQt5.QtWebSockets",
        "PyQt5.QtXml",
        "PyQt5.QtXmlPatterns",
    ],
    "include_files": include_files,
    "include_msvcr": True,  # Include Microsoft Visual C++ runtime (Windows)
}

# --- MSI installer options (Windows) --------------------------------------

# Start Menu shortcut is always installed; Desktop shortcut is opt-in. Rows use
# the MSI Shortcut table schema; Component_ "TARGETDIR" is the cx_Freeze idiom
# for "the install directory component".
# (Shortcut, Directory_, Name, Component_, Target, Arguments, Description,
#  Hotkey, Icon_, IconIndex, ShowCmd, WkDir)
_shortcut_desc = "GeoLabeller - geospatial image labeling"
shortcut_table = [
    (
        "StartMenuShortcut", "ProgramMenuFolder", "GeoLabeller",
        "TARGETDIR", f"[TARGETDIR]{TARGET_EXE}", None, _shortcut_desc,
        None, None, None, None, "TARGETDIR",
    ),
]
if ENABLE_MSI_SHORTCUT:
    shortcut_table.append((
        "DesktopShortcut", "DesktopFolder", "GeoLabeller",
        "TARGETDIR", f"[TARGETDIR]{TARGET_EXE}", None, _shortcut_desc,
        None, None, None, None, "TARGETDIR",
    ))

# Per-user install: cx_Freeze's `all_users = False` (below) sets the MSI
# ALLUSERS property to "" (empty), meaning "install for the current user only",
# which does NOT require administrator elevation. Combined with the LocalAppData
# target, a standard (non-admin) user can install it, and the Start Menu /
# Desktop shortcut folders resolve to that user's own profile.
#
# IMPORTANT: cx_Freeze always writes its own ALLUSERS row into the Property
# table, so do NOT add ALLUSERS / MSIINSTALLPERUSER here - a duplicate key fails
# the build with MSI error 2259. Control the install scope via `all_users`.
msi_properties = []
# Add/Remove Programs "about"/help links, if a URL was provided.
if ABOUT_URL:
    msi_properties += [
        ("ARPURLINFOABOUT", ABOUT_URL),
        ("ARPHELPLINK", ABOUT_URL),
    ]

msi_data = {"Shortcut": shortcut_table}
if msi_properties:
    msi_data["Property"] = msi_properties

bdist_msi_options = {
    # Stable across ALL versions so a new build cleanly upgrades an older one.
    # DO NOT CHANGE this GUID once installers have been distributed.
    "upgrade_code": "{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}",
    "add_to_path": False,
    # Per-user install (ALLUSERS=""): no administrator elevation required.
    "all_users": False,
    # Default location: %LocalAppData%\Programs\GeoLabeller (user-writable).
    "initial_target_dir": r"[LocalAppDataFolder]\Programs\GeoLabeller",
    # Icon shown for the app in Add/Remove Programs.
    "install_icon": ICON_FILE,
    "summary_data": {
        "author": AUTHOR,
        "comments": "GeoLabeller - geospatial image labeling tool",
    },
    "data": msi_data,
}

# Base for GUI application (hides console on Windows)
base = None
if sys.platform == "win32":
    base = "gui"

# Target executable. Shortcuts are defined via the MSI Shortcut table above
# (so we can install both Start Menu and Desktop entries), not here.
target = Executable(
    script="../main.py",
    base=base,
    target_name="GeoLabeller",
    icon=ICON_FILE,
)

setup(
    name="GeoLabeller",
    version=VERSION,
    description="A geospatial image labeling tool for creating ground truth datasets",
    author=AUTHOR,
    license="MIT",
    options={
        "build_exe": build_exe_options,
        "bdist_msi": bdist_msi_options,
    },
    executables=[target],
)
