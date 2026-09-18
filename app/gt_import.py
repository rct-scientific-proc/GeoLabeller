"""Import Ground Truth: another machine's labels, onto this machine's images.

A GT file (Export > Ground Truth, or any project file) records each image
by its absolute path on the machine that wrote it. On this machine the
same GeoTIFFs live somewhere else - another drive, another user profile, a
copied folder - and were loaded with Add Directory rather than from that
project. Importing finds each GT image among the images already in this
project and adds its labels there. Nothing else about the two projects
has to agree: label positions are stored relative to their own image, so
the same file takes them unchanged wherever it sits.

Matching follows Locate Missing Images' rule (relocate.py), for the same
reason - a wrong match would put every label on the wrong image:

- by filename, compared case-insensitively, each path read by the rules
  of the OS that wrote it (a GT from Linux names its files the same here);
- verified: the size and CRS recorded for the GT image must agree with
  this project's image wherever both sides recorded them;
- several verified candidates: the longest shared folder tail wins, and a
  tie matches nothing;
- one image here claimed by two GT images: neither is matched.

Merging adds and never overwrites:

- a label already in this project (same unique_id, wherever it sits) is
  skipped, so importing the same GT twice adds nothing, and an updated GT
  brings only its new labels;
- new labels get fresh ids; their object ids are kept, so links arrive
  intact;
- the classes those labels use are added in the GT's order; description
  and mask-name presets are merged;
- an image's hard-negative flag and location tag are taken only where
  this project's image has none;
- waypoints are not imported - this imports labels;
- labels on GT images that are not loaded here are left out, and listed.

The pure functions live Qt-free at the top so the policy is testable
headlessly; the preview dialog at the bottom is only chrome around them.
"""
import copy
from collections import Counter
from dataclasses import dataclass, field

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout)

from .relocate import _parse, _tail_overlap, verify_candidate

MATCHED = "Found"
NOT_LOADED = "Not loaded here"
MISMATCH = "Different file"      # same name here, but not the same image
AMBIGUOUS = "Ambiguous"


# ---------------------------------------------------------------------------
# Pure matching and merging (no Qt)
# ---------------------------------------------------------------------------

@dataclass
class ImageMatch:
    """Where one GT image's labels will go, if anywhere."""
    gt_key: str                  # its key in the GT's images
    gt_path: str                 # its path as the GT recorded it
    labels: int                  # labels it carries
    status: str                  # MATCHED / NOT_LOADED / MISMATCH /
                                 # AMBIGUOUS
    local_path: "str | None" = None
    note: str = ""
    new_labels: int = 0          # of ``labels``, those not already here

    @property
    def name(self) -> str:
        return _parse(self.gt_path).name


@dataclass
class ImportPlan:
    """What an import would do - worked out before anything changes."""
    gt: object                   # the GT's LabelProject
    matches: list = field(default_factory=list)

    @property
    def matched(self) -> list:
        return [m for m in self.matches if m.status == MATCHED]

    @property
    def new_labels(self) -> int:
        return sum(m.new_labels for m in self.matched)

    @property
    def duplicate_labels(self) -> int:
        return sum(m.labels - m.new_labels for m in self.matched)

    @property
    def left_out_labels(self) -> int:
        return sum(m.labels for m in self.matches if m.status != MATCHED)

    def image_updates(self, current) -> int:
        """Matched images that would gain a flag or a location tag."""
        return sum(1 for m in self.matched
                   if _fills(current.images[m.local_path],
                             self.gt.images[m.gt_key]))


@dataclass
class ImportResult:
    images: int = 0
    labels_added: int = 0
    duplicates: int = 0
    left_out: int = 0
    classes_added: list = field(default_factory=list)


def _name_key(path: str) -> str:
    return _parse(path).name.lower()


def _size(image) -> str:
    return f"{image.original_width}x{image.original_height}"


def _difference(local, gt_image) -> "str | None":
    """Why ``local`` is not the GT's image, or None when it may be.

    Compared on what both projects recorded, without opening anything. A
    side that recorded nothing is not held against the other; an image
    here with no record of its own is checked against its file instead.
    """
    if not (local.original_width or local.original_height
            or local.get_crs() is not None):
        if verify_candidate(local.path, gt_image):
            return None
        return "a file of this name is loaded here, but it does not match"
    if (local.original_width and gt_image.original_width
            and local.original_height and gt_image.original_height
            and (local.original_width, local.original_height)
            != (gt_image.original_width, gt_image.original_height)):
        return (f"a file of this name is loaded here, but its size differs "
                f"({_size(local)} here, {_size(gt_image)} in the GT)")
    here_crs, gt_crs = local.get_crs(), gt_image.get_crs()
    if here_crs is not None and gt_crs is not None and here_crs != gt_crs:
        return ("a file of this name is loaded here, but its coordinate "
                "system differs")
    return None


def _fills(local, gt_image) -> bool:
    return bool((gt_image.hard_negative_source
                 and not local.hard_negative_source)
                or (gt_image.location and not local.location))


def _unique_ids(project) -> set:
    return {label.unique_id for image in project.images.values()
            for label in image.labels}


def plan_import(current, gt) -> ImportPlan:
    """Match every GT image to an image in ``current``. Changes nothing."""
    by_name: dict = {}
    for key, image in current.images.items():
        by_name.setdefault(_name_key(image.path or key), []).append(
            (key, image))
    here = _unique_ids(current)

    plan = ImportPlan(gt=gt)
    for gt_key, gt_image in gt.images.items():
        gt_path = gt_image.path or gt_key
        match = ImageMatch(gt_key=gt_key, gt_path=gt_path,
                           labels=len(gt_image.labels), status=NOT_LOADED)
        plan.matches.append(match)
        candidates = by_name.get(_name_key(gt_path), [])
        if not candidates:
            match.note = "no image of this name is loaded here"
            continue
        verified, reasons = [], []
        for key, image in candidates:
            why = _difference(image, gt_image)
            if why is None:
                verified.append(key)
            else:
                reasons.append(why)
        if not verified:
            match.status, match.note = MISMATCH, reasons[0]
            continue
        if len(verified) > 1:
            scores = {key: _tail_overlap(key, gt_path) for key in verified}
            best = max(scores.values())
            verified = [key for key in verified if scores[key] == best]
        if len(verified) > 1:
            match.status = AMBIGUOUS
            match.note = (f"{len(verified)} images loaded here fit equally "
                          "well")
            continue
        match.status, match.local_path = MATCHED, verified[0]

    # One image here, two GT images claiming it: whichever is right, the
    # other's labels would land on the wrong image.
    claims = Counter(m.local_path for m in plan.matched)
    for match in plan.matched:
        if claims[match.local_path] > 1:
            match.status, match.local_path = AMBIGUOUS, None
            match.note = ("another image in the GT fits the same file here")

    for match in plan.matched:
        match.new_labels = sum(
            1 for label in gt.images[match.gt_key].labels
            if label.unique_id not in here)
    return plan


def apply_import(current, plan: ImportPlan) -> ImportResult:
    """Add the plan's labels to ``current``. The GT is left as it was."""
    gt = plan.gt
    result = ImportResult(left_out=plan.left_out_labels)
    here = _unique_ids(current)
    top = max((label.id for image in current.images.values()
               for label in image.labels), default=0)
    next_id = max(current._next_id, top + 1)

    used: dict = {}
    for match in plan.matched:
        result.images += 1
        gt_image = gt.images[match.gt_key]
        local = current.images[match.local_path]
        for label in gt_image.labels:
            if label.unique_id in here:
                result.duplicates += 1
                continue
            placed = copy.deepcopy(label)
            placed.id = next_id
            next_id += 1
            local.labels.append(placed)
            current._index_label(placed, match.local_path)
            here.add(placed.unique_id)
            used.setdefault(placed.class_name, None)
            result.labels_added += 1
        if gt_image.hard_negative_source:
            local.hard_negative_source = True
        if gt_image.location and not local.location:
            local.location = gt_image.location
    current._next_id = next_id

    # The GT's own class order, then any class a label used that its list
    # did not name.
    ordered = [c for c in gt.classes if c in used] + \
        [c for c in used if c not in gt.classes]
    for class_name in ordered:
        if class_name not in current.classes:
            current.classes.append(class_name)
            result.classes_added.append(class_name)
    current.descriptions = list(dict.fromkeys(
        list(current.descriptions) + list(gt.descriptions)))
    current.mask_names = list(dict.fromkeys(
        list(current.mask_names) + list(gt.mask_names)))
    return result


# ---------------------------------------------------------------------------
# The preview
# ---------------------------------------------------------------------------

def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class ImportGroundTruthDialog(QDialog):
    """What an import will bring in, per image, before anything changes."""

    def __init__(self, plan: ImportPlan, source_name: str, current=None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Ground Truth")
        self.setMinimumSize(720, 420)
        layout = QVBoxLayout(self)

        total = len(plan.matches)
        found = len(plan.matched)
        lines = [f"From {source_name}: {found} of {total} images found "
                 f"here - {_plural(plan.new_labels, 'new label')} to add."]
        if plan.duplicate_labels:
            lines.append(f"{_plural(plan.duplicate_labels, 'label')} "
                         "already in this project will be skipped.")
        if plan.left_out_labels:
            lines.append(
                f"{_plural(plan.left_out_labels, 'label')} on "
                f"{_plural(total - found, 'image')} that could not be "
                "matched will be left out (see below).")
        if any(m.status == NOT_LOADED for m in plan.matches):
            lines.append(
                "For images not loaded here: load them, then import this "
                "GT again - labels already imported are never added twice.")
        lines.append("Images are matched by filename and checked against "
                     "the size and coordinate system the GT recorded.")
        self.summary = QLabel("\n".join(lines))
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Image", "Result", "Labels",
                                   "Here / why not"])
        self.tree.setRootIsDecorated(False)
        for match in plan.matches:
            if match.status == MATCHED:
                skipped = match.labels - match.new_labels
                count = f"{match.new_labels} new"
                if skipped:
                    count += f", {skipped} already here"
                where = match.local_path
            else:
                count = str(match.labels)
                where = match.note
            item = QTreeWidgetItem([match.name, match.status, count, where])
            item.setToolTip(0, f"In the GT: {match.gt_path}")
            item.setToolTip(3, where)
            item.setData(0, Qt.UserRole, match.gt_key)
            self.tree.addTopLevelItem(item)
        for column in range(3):
            self.tree.resizeColumnToContents(column)
        layout.addWidget(self.tree, 1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok = self.buttons.button(QDialogButtonBox.Ok)
        ok.setText("Import")
        updates = plan.image_updates(current) if current is not None else 0
        ok.setEnabled(bool(plan.new_labels or updates))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)


def confirm_import(plan: ImportPlan, source_name: str, current,
                   parent=None) -> bool:
    """Show the preview; True when the user chose Import."""
    dialog = ImportGroundTruthDialog(plan, source_name, current, parent)
    return dialog.exec_() == QDialog.Accepted
