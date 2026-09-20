"""What the file dialogs and the File menu remember between sessions.

Two things labellers asked for, and one place to keep both.

A dialog that opens wherever Qt feels like costs the user the walk down
their imagery tree every time: adding the second directory of a survey
meant finding it again from scratch. Each KIND of dialog remembers its
own folder instead - imagery, project files and exports are rarely near
each other, so one shared memory would send every dialog to the wrong
place two times in three.

And projects opened or saved are kept as a list, newest first, for File >
Open Recent.

Both live in the user's settings, not in any project: they are how this
person works, not what the work is. Paths are compared the way the
platform reads them, so one file is one entry however it was spelled
(C:/work/a.geolabel and C:\\work\\a.geolabel are the same file, and on
Windows so is C:/WORK/A.GEOLABEL).
"""
import os

from PyQt5.QtCore import QSettings

from .labels import canonical_path

SETTINGS = ("GeoLabeller", "GeoLabeller")

# The kinds of dialog, each with a folder of its own.
IMAGERY = "imagery"      # Add Directory, Add GeoTIFF, hunting for images
PROJECT = "project"      # open, save as, combine, import ground truth
EXPORT = "export"        # everything written for somebody else

# How many projects File > Open Recent keeps. Ten fills a menu without
# needing a scrollbar, and nobody reaches for the eleventh.
LIMIT = 10

_DIR_KEY = "recent/dir/{}"
_PROJECTS_KEY = "recent/projects"


def _same(a: str, b: str) -> bool:
    """One file, however its path was spelled."""
    return (os.path.normcase(canonical_path(a))
            == os.path.normcase(canonical_path(b)))


def promoted(paths: list, path: str, limit: int = LIMIT) -> list:
    """``path`` first, the rest in order, no repeats, oldest dropped."""
    kept = [known for known in paths if not _same(known, path)]
    # The spelling already listed wins, so an entry does not change shape
    # under the user because they typed it differently this time.
    first = next((known for known in paths if _same(known, path)), path)
    return [first] + kept[:limit - 1]


def last_dir(kind: str) -> str:
    """Where this kind of dialog was last used, or "" the first time."""
    value = QSettings(*SETTINGS).value(_DIR_KEY.format(kind), "")
    folder = str(value) if value else ""
    return folder if folder and os.path.isdir(folder) else ""


def remember_dir(kind: str, path: str) -> None:
    """Remember where ``path`` is - a file's folder, or the folder itself."""
    if not path:
        return
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    if folder:
        QSettings(*SETTINGS).setValue(_DIR_KEY.format(kind),
                                      canonical_path(folder))


def projects() -> list:
    """The recently opened projects, newest first."""
    value = QSettings(*SETTINGS).value(_PROJECTS_KEY, [])
    if isinstance(value, str):          # one entry comes back bare
        value = [value] if value else []
    return [str(path) for path in (value or [])]


def _store(paths: list) -> list:
    QSettings(*SETTINGS).setValue(_PROJECTS_KEY, list(paths))
    return list(paths)


def remember_project(path) -> list:
    """Put ``path`` at the top of the list, and return the new list."""
    return _store(promoted(projects(), str(path)))


def forget_project(path) -> list:
    """Drop ``path`` - it has been moved, renamed or deleted."""
    return _store([known for known in projects()
                   if not _same(known, str(path))])


def clear_projects() -> None:
    _store([])
