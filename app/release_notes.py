"""Help > Release Notes: what changed in each release, newest first.

Asked for 2026-11-20. The notes are docs/RELEASE_NOTES.md, written at each
release from the release tags (build/make_release_notes.py) and shipped
beside the executable, so the window works installed and offline.

The file is parsed here rather than handed to Qt's Markdown reader: the
notes are prose and "*" / "-" lists with wrapped lines, and they name code
(_seat, label_x, <path>) that a Markdown reader would take for emphasis or
markup. Each paragraph and list item is escaped and laid out as it is.
"""
import html
import re
from dataclasses import dataclass, field

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QPushButton, QSplitter,
                             QTextBrowser, QVBoxLayout, QWidget)

from .resources import release_notes_path
from .version import app_version

HEADING = re.compile(r"^## (?P<version>\d+\.\d+\.\d+) "
                     r"\((?P<date>\d{4}-\d{2}-\d{2})\)(?: - (?P<title>.+))?$")
BULLET = re.compile(r"^(?:[*-]|\d+\.)\s+(?P<text>.*)$")


@dataclass
class Release:
    version: str
    date: str
    title: str = ""
    # ("p", text) or ("ul", [item text, ...]), in order.
    blocks: list = field(default_factory=list)


def parse(text: str) -> list:
    """The releases in the file, in the file's order (newest first)."""
    releases, current, lines = [], None, []

    def close():
        if current is not None:
            current.blocks = _blocks(lines)

    for line in text.splitlines():
        match = HEADING.match(line.rstrip())
        if match:
            close()
            current = Release(match["version"], match["date"],
                              (match["title"] or "").strip())
            releases.append(current)
            lines = []
        elif current is not None:
            lines.append(line.rstrip())
    close()
    return releases


def _blocks(lines: list) -> list:
    """Paragraphs and lists. A blank line ends a paragraph; a line that
    starts with "* ", "- " or "1. " starts a list item, and the indented
    lines after it continue it, across blank lines between items."""
    blocks, paragraph, items = [], [], None

    def end_paragraph():
        if paragraph:
            blocks.append(("p", " ".join(paragraph)))
            paragraph.clear()

    def end_list():
        nonlocal items
        if items:
            blocks.append(("ul", [" ".join(item) for item in items]))
        items = None

    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            end_paragraph()
            continue
        bullet = BULLET.match(stripped)
        if bullet:
            end_paragraph()
            if items is None:
                items = []
            items.append([bullet["text"]])
        elif items is not None and raw.startswith((" ", "\t")):
            items[-1].append(stripped)          # an item's wrapped line
        else:
            end_list()
            paragraph.append(stripped)
    end_paragraph()
    end_list()
    return blocks


def anchor(version: str) -> str:
    return "v" + version.replace(".", "-")


def to_html(releases: list, installed: str = "") -> str:
    """The whole page: a heading per release, its notes under it."""
    parts = []
    for release in releases:
        mark = (" <span style='color: palette(mid);'>(this version)</span>"
                if release.version == installed else "")
        title = f" &ndash; {html.escape(release.title)}" if release.title \
            else ""
        parts.append(
            f"<h2><a name='{anchor(release.version)}'></a>"
            f"{html.escape(release.version)}{title}{mark}</h2>"
            f"<p style='color: palette(mid);'>{html.escape(release.date)}"
            "</p>")
        for kind, content in release.blocks:
            if kind == "p":
                parts.append(f"<p>{html.escape(content)}</p>")
            else:
                parts.append("<ul>" + "".join(
                    f"<li>{html.escape(item)}</li>" for item in content)
                    + "</ul>")
    return "\n".join(parts)


class ReleaseNotesDialog(QDialog):
    """Every release's notes, with a list of versions to jump to."""

    def __init__(self, parent=None, path=None):
        super().__init__(parent)
        self.setWindowTitle("Release Notes")
        self.resize(940, 680)
        self.path = path or release_notes_path()
        self.installed = app_version() or ""
        try:
            self.releases = parse(self.path.read_text(encoding="utf-8"))
        except OSError:
            self.releases = []

        self.versions = QListWidget()
        self.versions.setMinimumWidth(150)
        self.text = QTextBrowser()
        self.text.setOpenLinks(False)
        if self.releases:
            for release in self.releases:
                label = release.version + (
                    "  (this version)" if release.version == self.installed
                    else "")
                item = QListWidgetItem(f"{label}\n{release.date}")
                item.setData(Qt.UserRole, release.version)
                self.versions.addItem(item)
            self.text.setHtml(to_html(self.releases, self.installed))
        else:
            self.text.setPlainText(
                "The release notes were not found at:\n\n"
                f"{self.path}\n\n"
                "They ship with the installed application; a copy is also "
                "in the project's docs folder.")
        self.versions.currentItemChanged.connect(self._show_version)

        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 0, 0)
        left_box.addWidget(QLabel("Versions"))
        left_box.addWidget(self.versions)
        splitter.addWidget(left)
        splitter.addWidget(self.text)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([180, 760])

        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(splitter, 1)
        layout.addLayout(buttons)

        # Open on the version running, else the newest.
        start = next((row for row, release in enumerate(self.releases)
                      if release.version == self.installed), 0)
        if self.releases:
            self.versions.setCurrentRow(start)

    def _show_version(self, item, _previous=None):
        if item is not None:
            self.text.scrollToAnchor(anchor(item.data(Qt.UserRole)))
