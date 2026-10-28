"""Duplicate labels on import (2.8.11).

A label brought in - by Import Ground Truth, Combine Projects or a Model
Review import - may land on an object the project already has a label
for: the same image, within a few pixels. What becomes of the pair is a
setting (File > Import Options...): keep the labels already here and
drop the new ones, which is the default; keep the new and drop the old;
keep both; or ask, a window listing each pair for a decision by hand.
The neighbourhood that counts as the same object, in pixels of the
image, is a setting too.

The import adds its labels first; then every new label is paired with
the nearest label already there on its image within the distance, and
the decision removes one of the pair or neither - inside the import's
undo step, so the whole of it is one thing to take back.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import PurePosixPath

from PyQt5.QtWidgets import (QApplication, QComboBox, QDialog,
                             QDialogButtonBox, QDoubleSpinBox, QHBoxLayout,
                             QLabel, QPushButton, QRadioButton, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout)

from .settings_scope import settings as app_settings

KEEP_OLD = "keep_old"
KEEP_NEW = "keep_new"
KEEP_BOTH = "keep_both"
ASK = "ask"
POLICIES = (
    (KEEP_OLD, "Keep the labels already here; drop the new ones"),
    (KEEP_NEW, "Keep the new labels; drop the ones already here"),
    (KEEP_BOTH, "Keep both"),
    (ASK, "Ask: a window to decide each pair by hand"),
)
DEFAULT_POLICY = KEEP_OLD
DEFAULT_DISTANCE_PX = 10.0
POLICY_KEY = "import/duplicate_policy"
DISTANCE_KEY = "import/duplicate_px"


# -- the settings ---------------------------------------------------------------

def policy() -> str:
    value = str(app_settings().value(POLICY_KEY, DEFAULT_POLICY))
    return value if value in {name for name, _text in POLICIES} \
        else DEFAULT_POLICY


def set_policy(value: str):
    if value not in {name for name, _text in POLICIES}:
        raise ValueError(value)
    app_settings().setValue(POLICY_KEY, value)


def distance_px() -> float:
    try:
        value = float(app_settings().value(DISTANCE_KEY, DEFAULT_DISTANCE_PX))
    except (TypeError, ValueError):
        return DEFAULT_DISTANCE_PX
    return min(max(value, 0.0), 10000.0)


def set_distance_px(value: float):
    app_settings().setValue(DISTANCE_KEY, float(value))


def policy_text(name: str) -> str:
    return dict(POLICIES).get(name, name)


# -- finding and deciding ---------------------------------------------------------

@dataclass
class Overlap:
    """A new label on an object already labelled: the nearest label
    already here on the same image within the distance."""
    image_path: str
    old: object                 # the label already here
    new: object                 # the one just imported
    distance: float
    keep: str = KEEP_BOTH       # the decision

    @property
    def image_name(self) -> str:
        return PurePosixPath(str(self.image_path).replace("\\", "/")).name


def _hypot(a, b) -> float:
    return math.hypot(a.pixel_x - b.pixel_x, a.pixel_y - b.pixel_y)


def _grid(labels, cell: float) -> dict:
    """The labels by the ``cell``-sized square each lies in. Everything
    within ``cell`` of a point is in the nine squares around its own,
    so a check costs the labels near the point, not the image's: every
    new label against every label on its image was 1.9 s for 2,000
    (audit 2026-10-28, 2.9.15)."""
    grid: dict = {}
    for label in labels:
        key = (int(label.pixel_x // cell), int(label.pixel_y // cell))
        grid.setdefault(key, []).append(label)
    return grid


def _around(grid: dict, x: float, y: float, cell: float):
    """The grid's labels in the nine squares around (x, y)."""
    column, row = int(x // cell), int(y // cell)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            yield from grid.get((column + dx, row + dy), ())


def nearby_ids(project, places, distance: float) -> set:
    """Ids of the labels within ``distance`` of any ``(image_path, x, y)``
    place: what an import may displace, named to its undo step before
    it runs."""
    wanted: dict = {}
    for path, x, y in places:
        wanted.setdefault(path, []).append((float(x), float(y)))
    cell = max(float(distance), 1e-6)
    ids: set = set()
    for path, points in wanted.items():
        image = project.images.get(path)
        if image is None:
            continue
        grid = _grid(image.labels, cell)
        for x, y in points:
            for label in _around(grid, x, y, cell):
                if math.hypot(label.pixel_x - x, label.pixel_y - y) <= distance:
                    ids.add(label.id)
    return ids


def find_overlaps(project, new_ids, distance: float) -> list:
    """For each label whose id is in ``new_ids``, the nearest label on
    its image that is not new, when within ``distance`` pixels."""
    new_ids = set(new_ids)
    cell = max(float(distance), 1e-6)
    overlaps = []
    for path, image in project.images.items():
        olds = [label for label in image.labels if label.id not in new_ids]
        if not olds:
            continue
        grid = _grid(olds, cell)
        for label in image.labels:
            if label.id not in new_ids:
                continue
            nearest = min(_around(grid, label.pixel_x, label.pixel_y, cell),
                          key=lambda old: _hypot(old, label), default=None)
            if nearest is None:
                continue
            d = _hypot(nearest, label)
            if d <= distance:
                overlaps.append(Overlap(path, nearest, label, d))
    return overlaps


def decide(overlaps: list, keep: str):
    """Every pair decided the same way (a policy other than Ask)."""
    for overlap in overlaps:
        overlap.keep = keep


def resolve(project, overlaps: list) -> tuple:
    """Remove what the decisions say; (ids of old labels removed, ids of
    new labels removed), each once."""
    removed_old: list = []
    removed_new: list = []
    for overlap in overlaps:
        if overlap.keep == KEEP_OLD and overlap.new.id not in removed_new:
            project.remove_label(overlap.new.id)
            removed_new.append(overlap.new.id)
        elif overlap.keep == KEEP_NEW and overlap.old.id not in removed_old:
            project.remove_label(overlap.old.id)
            removed_old.append(overlap.old.id)
    return removed_old, removed_new


def describe(removed_old: list, removed_new: list, pairs: int) -> str:
    """A clause for the import's message; empty when there were no pairs."""
    if not pairs:
        return ""
    parts = []
    if removed_new:
        n = len(removed_new)
        parts.append(f"{n} duplicate{'' if n == 1 else 's'} dropped, keeping "
                     "the labels already here")
    if removed_old:
        n = len(removed_old)
        parts.append(f"{n} label{'' if n == 1 else 's'} already here replaced "
                     "by the new")
    kept = pairs - len(removed_new) - len(removed_old)
    if kept > 0:
        parts.append(f"{kept} possible duplicate{'' if kept == 1 else 's'} "
                     "kept")
    return "; " + "; ".join(parts)


def settle(project, new_ids, ask=None, old_name: str = "Already here",
           new_name: str = "New", keep: "str | None" = None,
           distance: "float | None" = None) -> tuple:
    """Find the pairs and apply the setting - or ``ask(overlaps)``, which
    returns True when the user decided them in a window - and remove
    accordingly; (removed old ids, removed new ids, the clause)."""
    keep = policy() if keep is None else keep
    distance = distance_px() if distance is None else distance
    overlaps = find_overlaps(project, new_ids, distance)
    if not overlaps:
        return [], [], ""
    if keep == ASK:
        if ask is None or not ask(overlaps, old_name, new_name):
            decide(overlaps, KEEP_BOTH)
    else:
        decide(overlaps, keep)
    removed_old, removed_new = resolve(project, overlaps)
    return removed_old, removed_new, describe(removed_old, removed_new,
                                              len(overlaps))


# -- the windows ------------------------------------------------------------------

def _fit(dialog, width: int, height: int):
    screen = QApplication.primaryScreen()
    if screen is not None:
        available = screen.availableGeometry()
        width = min(width, max(320, available.width() - 80))
        height = min(height, max(240, available.height() - 120))
    dialog.resize(width, height)


class DuplicateReviewDialog(QDialog):
    """Each pair on a row, with what to keep; OK applies, Cancel keeps
    both of every pair."""

    def __init__(self, overlaps: list, parent=None,
                 old_name: str = "Already here", new_name: str = "New"):
        super().__init__(parent)
        self.setWindowTitle("Possible duplicate labels")
        self.overlaps = list(overlaps)
        self._choices = (("Both", KEEP_BOTH), (old_name, KEEP_OLD),
                         (new_name, KEEP_NEW))
        layout = QVBoxLayout(self)
        n = len(self.overlaps)
        note = QLabel(
            f"{n} new label{'' if n == 1 else 's'} landed within "
            f"{distance_px():g} px of a label already here on the same "
            "image. Choose what to keep for each; File > Import Options... "
            "sets the distance and a standing answer.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["Image", old_name, new_name,
                                   "Distance (px)", "Keep"])
        self.tree.setRootIsDecorated(False)
        self._combos: list = []
        for overlap in self.overlaps:
            item = QTreeWidgetItem([
                overlap.image_name,
                f"{overlap.old.class_name} at ({overlap.old.pixel_x:.0f}, "
                f"{overlap.old.pixel_y:.0f})",
                f"{overlap.new.class_name} at ({overlap.new.pixel_x:.0f}, "
                f"{overlap.new.pixel_y:.0f})",
                f"{overlap.distance:.1f}", ""])
            self.tree.addTopLevelItem(item)
            combo = QComboBox()
            for text, keep in self._choices:
                combo.addItem(text, keep)
            combo.setCurrentIndex(
                [k for _t, k in self._choices].index(overlap.keep))
            self.tree.setItemWidget(item, 4, combo)
            self._combos.append(combo)
        for column in range(4):
            self.tree.resizeColumnToContents(column)
        layout.addWidget(self.tree, 1)
        row = QHBoxLayout()
        for text, keep in ((f"All: {old_name.lower()}", KEEP_OLD),
                           (f"All: {new_name.lower()}", KEEP_NEW),
                           ("All: both", KEEP_BOTH)):
            button = QPushButton(text)
            button.clicked.connect(lambda _c, k=keep: self.set_all(k))
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                        | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        _fit(self, 820, 480)

    def set_all(self, keep: str):
        index = [k for _t, k in self._choices].index(keep)
        for combo in self._combos:
            combo.setCurrentIndex(index)

    def decisions(self) -> list:
        """The overlaps, each with the keep its row says."""
        for overlap, combo in zip(self.overlaps, self._combos):
            overlap.keep = combo.currentData()
        return self.overlaps

    def accept(self):
        self.decisions()
        super().accept()


class ImportOptionsDialog(QDialog):
    """File > Import Options...: the standing answer and the distance."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Options")
        layout = QVBoxLayout(self)
        note = QLabel(
            "A label brought in - by Import Ground Truth, Combine Projects "
            "or a Model Review import - may land on an object already "
            "labelled here. When it does, within the distance below on the "
            "same image:")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.radios: dict = {}
        current = policy()
        for name, text in POLICIES:
            radio = QRadioButton(text)
            radio.setChecked(name == current)
            self.radios[name] = radio
            layout.addWidget(radio)
        row = QHBoxLayout()
        row.addWidget(QLabel("Same object within"))
        self.distance_spin = QDoubleSpinBox()
        self.distance_spin.setRange(0.0, 10000.0)
        self.distance_spin.setDecimals(1)
        self.distance_spin.setSingleStep(1.0)
        self.distance_spin.setSuffix(" px")
        self.distance_spin.setValue(distance_px())
        self.distance_spin.setToolTip(
            "Pixels of the image. Labels closer than this on the same "
            "image are taken for the same object.")
        row.addWidget(self.distance_spin)
        row.addStretch(1)
        layout.addLayout(row)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                        | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def chosen(self) -> str:
        return next((name for name, radio in self.radios.items()
                     if radio.isChecked()), DEFAULT_POLICY)

    def accept(self):
        set_policy(self.chosen())
        set_distance_px(self.distance_spin.value())
        super().accept()
