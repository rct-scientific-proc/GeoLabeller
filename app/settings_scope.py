"""Whose settings a run of this code reads and writes.

The app remembers things per user in QSettings - the registry, on
Windows: the projects opened lately, the last folder of each dialog, pane
sizes, which Snippet Editor sections are on. That is right for a person
using the app, and wrong for a test suite driving the same windows: the
"user" whose habits get recorded is then whoever ran the tests. Found
when File > Open Recent listed ten projects nobody had opened, all of
them temporary files from the test suite (2026-09-21).

So a test run is sent to a throwaway folder instead. "A test run" is
`unittest` being loaded: a normal start of the app never loads it, and the
installed build does not contain it at all (build/setup.py excludes it,
and build/check_frozen_imports.py keeps the app from importing it), so
this cannot fire for a user. A script that wants the same - a benchmark, a
preview - sets GEOLABELLER_SETTINGS_DIR to a folder of its choosing.

Called as the app package is imported, before any window exists.
"""
import os
import sys
import tempfile

ENV = "GEOLABELLER_SETTINGS_DIR"
ORGANISATION = APPLICATION = "GeoLabeller"

# Where this run's settings live when they are NOT the user's own; None
# for the platform's native store. Set once, by apply().
_folder: "str | None" = None


def folder_for(environ, modules) -> "str | None":
    """The folder this run's settings belong in ("" for a fresh temporary
    one), or None for the user's own, in the platform's native store."""
    chosen = environ.get(ENV)
    if chosen:
        return chosen
    if "unittest" in modules:
        return ""
    return None


def apply(environ=None, modules=None) -> "str | None":
    """Decide where this run's settings live. Imports nothing heavy: the
    app package calls this first, and for a real user it must stay free."""
    global _folder
    folder = folder_for(os.environ if environ is None else environ,
                        sys.modules if modules is None else modules)
    if folder == "":
        folder = tempfile.mkdtemp(prefix="geolabeller_settings_")
    _folder = folder
    return folder


def settings():
    """The app's QSettings - every part of the app gets it here.

    It has to be a factory rather than a Qt-wide switch: QSettings(org,
    app) is hard-wired to the native store (setDefaultFormat only reaches
    the no-argument constructor), and on Windows that store is the
    registry, which cannot be pointed anywhere else.
    """
    from PyQt5.QtCore import QSettings
    if _folder is None:
        return QSettings(ORGANISATION, APPLICATION)
    return QSettings(os.path.join(_folder, "GeoLabeller.ini"),
                     QSettings.IniFormat)
