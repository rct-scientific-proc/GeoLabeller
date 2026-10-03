"""Where this build keeps the files that ship alongside the application.

A frozen build has no repository around it. cx_Freeze copies data files into
the install directory next to the executable, so anything opened at runtime is
found relative to sys.executable there and relative to the source tree here.
Both answers live in this one place, because the last time two parts of the
project each decided how to serialise themselves they drifted apart and a
crash started losing waypoints.
"""
import sys
from pathlib import Path

ICD_NAME = "GeoLabeller-ICD.pdf"
HOWTO_NAME = "GeoLabeller-HowTo.pdf"


def install_root() -> Path:
    """The directory the application's own files sit in."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _document_path(name: str) -> Path:
    """Where a document that ships with the application is, if it did.

    A source checkout keeps it in docs/; cx_Freeze flattens include_files into
    the install directory, so a frozen build has it beside the executable. The
    path is returned whether or not the file is there - the caller can say
    where it looked, which is more use than a None.
    """
    if getattr(sys, "frozen", False):
        return install_root() / name
    return install_root() / "docs" / name


def icd_path() -> Path:
    """Where the Interface Control Document is, if it shipped."""
    return _document_path(ICD_NAME)


def howto_path() -> Path:
    """Where the How-To guide is, if it shipped."""
    return _document_path(HOWTO_NAME)
