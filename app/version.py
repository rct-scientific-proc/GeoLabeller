"""What this build calls itself, for the window title.

The VERSION file at the repository root is already the single source of truth:
the release workflow refuses to build a tag that disagrees with it, and the ICD
stamps it on its cover. Reading the same file here means the title cannot drift
from either, and there is no second copy to remember to bump.

A frozen build has no repository around it, so cx_Freeze copies VERSION next to
the executable (see include_files in build/setup.py) and this looks beside
sys.executable instead.
"""
import sys
from functools import lru_cache
from pathlib import Path

APP_NAME = "GeoLabeller"


def _version_file() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "VERSION"
    return Path(__file__).resolve().parent.parent / "VERSION"


@lru_cache(maxsize=1)
def app_version() -> str:
    """The version string, or "" when it cannot be read.

    Never raises: a missing or unreadable VERSION file costs the version in the
    title bar, which is not worth failing to start over.
    """
    try:
        version = _version_file().read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return version.lstrip("vV")


def app_title() -> str:
    """The window title with no project open: "GeoLabeller v1.3.2"."""
    version = app_version()
    return f"{APP_NAME} v{version}" if version else APP_NAME
