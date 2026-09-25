"""Help > About: what this build is made of, ready to paste into a report.

Asked for 2026-09-25: a page listing Python and every package with its
version, so a bug report can say exactly what the user runs instead of
describing it from memory. Beyond the Python packages it names the native
libraries they wrap - Qt, GDAL, PROJ, HDF5 - because those are what differ
between machines when imagery misbehaves (this checkout, for one, carries
two PROJ builds: rasterio's and pyproj's).

The gathering is plain functions so a test can read the same rows the
window shows; every lookup falls back to NOT_AVAILABLE rather than raise,
since the one place this must not fail is a machine where something is
already broken. A frozen build carries the bundled packages' metadata
(cx_Freeze copies their dist-info into library.zip), so the package list
works installed as well as from source.
"""
import importlib
import importlib.metadata
import platform
import sys

from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel,
                             QPushButton, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout)

from .resources import icd_path, install_root
from .settings_scope import settings
from .version import APP_NAME, app_title, app_version

APP_SECTION = "This build"
LIBRARY_SECTION = "Libraries"
PATH_SECTION = "Where things are"
PACKAGE_SECTION = "Bundled packages"
NOT_AVAILABLE = "not available"

DESCRIPTION = ("A geospatial image labeling tool for creating ground truth "
               "datasets.")


def _read(module_name: str, *attributes) -> str:
    """``module.attr.attr`` as a string, or NOT_AVAILABLE for any failure."""
    try:
        value = importlib.import_module(module_name)
        for attribute in attributes:
            value = getattr(value, attribute)
        return str(value)
    except Exception:  # noqa: BLE001 - the report must not fail on a library
        return NOT_AVAILABLE


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_rows() -> list:
    return [
        (APP_NAME, app_version() or NOT_AVAILABLE),
        ("Build", "installed build" if is_frozen() else "source checkout"),
        ("Python", platform.python_version()),
        ("Operating system", platform.platform()),
    ]


def library_rows() -> list:
    """The libraries the application runs on, each with what it wraps."""
    return [
        ("PyQt5", PYQT_VERSION_STR),
        ("Qt", QT_VERSION_STR),
        ("numpy", _read("numpy", "__version__")),
        ("rasterio", _read("rasterio", "__version__")),
        ("GDAL (rasterio)", _read("rasterio", "__gdal_version__")),
        ("PROJ (rasterio)", _read("rasterio", "__proj_version__")),
        ("pyproj", _read("pyproj", "__version__")),
        ("PROJ (pyproj)", _read("pyproj", "proj_version_str")),
        ("h5py", _read("h5py", "__version__")),
        ("HDF5", _read("h5py", "version", "hdf5_version")),
        ("Pillow", _read("PIL", "__version__")),
        ("affine", _read("affine", "__version__")),
    ]


def path_rows() -> list:
    try:
        settings_file = settings().fileName()
    except Exception:  # noqa: BLE001
        settings_file = NOT_AVAILABLE
    icd = icd_path()
    return [
        ("Install folder", str(install_root())),
        ("Settings file", settings_file),
        ("ICD", str(icd) if icd.exists() else f"{icd} (missing)"),
    ]


def package_rows() -> list:
    """Every distribution importlib can see, once each, sorted by name.

    Installed, that is the packages cx_Freeze bundled; from a source
    checkout it is the whole environment.
    """
    found = {}
    try:
        distributions = list(importlib.metadata.distributions())
    except Exception:  # noqa: BLE001
        return []
    for distribution in distributions:
        try:
            name = distribution.metadata["Name"]
            version = distribution.version
        except Exception:  # noqa: BLE001 - a damaged dist-info
            continue
        if name and version and name not in found:
            found[name] = version
    return sorted(found.items(), key=lambda item: item[0].lower())


def report_sections() -> list:
    return [
        (APP_SECTION, app_rows()),
        (LIBRARY_SECTION, library_rows()),
        (PATH_SECTION, path_rows()),
        (PACKAGE_SECTION, package_rows()),
    ]


def report_text(sections=None) -> str:
    """The whole report as plain text: what Copy puts on the clipboard."""
    lines = [app_title(), ""]
    for title, rows in (report_sections() if sections is None else sections):
        lines.append(f"[{title}]")
        lines.extend(f"{name}: {value}" for name, value in rows)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


class AboutDialog(QDialog):
    """The About window: the report as a tree, and a Copy button."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.resize(560, 620)
        self._sections = report_sections()

        heading = QLabel(f"<h2>{app_title()}</h2><p>{DESCRIPTION}</p>")
        heading.setTextFormat(Qt.RichText)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["", "Version"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        bold = QFont()
        bold.setBold(True)
        for title, rows in self._sections:
            section = QTreeWidgetItem([title, ""])
            section.setFont(0, bold)
            section.setFlags(section.flags() & ~Qt.ItemIsSelectable)
            for name, value in rows:
                section.addChild(QTreeWidgetItem([name, value]))
            self.tree.addTopLevelItem(section)
            section.setExpanded(title != PACKAGE_SECTION)
        self.tree.resizeColumnToContents(0)

        self.copy_button = QPushButton("&Copy")
        self.copy_button.setToolTip(
            "Put everything on this page on the clipboard as text, to "
            "paste into a bug report")
        self.copy_button.clicked.connect(self._copy)
        self.copy_note = QLabel("")
        close_button = QPushButton("Close")
        close_button.setDefault(True)
        close_button.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.copy_note, 1)
        buttons.addWidget(close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(self.tree, 1)
        layout.addLayout(buttons)

    def _copy(self):
        QApplication.clipboard().setText(report_text(self._sections))
        self.copy_note.setText("Copied to the clipboard.")
