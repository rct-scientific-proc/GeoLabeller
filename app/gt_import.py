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
  this project's image wherever both sides recorded them - except that an
  image of ANOTHER SIZE is accepted when it is the same picture at another
  resolution (see below);
- several verified candidates: one of the GT's own size is preferred to
  one at another resolution; then the longest shared folder tail wins,
  and a tie matches nothing;
- one image here claimed by two GT images: neither is matched.

The same imagery at another resolution (asked for 2026-10-02): ground
truth made on downsampled copies, to be applied to the full-resolution
originals loaded here under the same filenames. An image of the same
name whose size differs is taken to BE the GT's image at another
resolution. Nothing checks that it covers the same ground (a check that
it did was built and taken out the same day, at the user's instruction):
the imagery is a mirror of the GT's, same names and same extents, and
using the import on the right folder is the user's to get right. The
preview shows both sizes for each such image, which is where a wrong
folder shows. Only a different CRS still makes it a different file, as
it does at the same size. Its labels then go across:

- by GROUND POSITION where both are georeferenced: the GT's pixel through
  the GT image's transform to lon/lat, and back through this image's -
  right even when the two extents differ by a pixel or so; otherwise by
  the same FRACTION of the width and height;
- as given: the result is the label's position, not a guess to be
  confirmed. A label is accurate to about half a pixel of the coarser
  image; correcting one is the user's to do (Labeled Cycle, Move);
- WITHOUT their masks, which are the coarser image's pixels and are left
  out, to be redrawn;
- with everything else - class, description, links, size, rating, review,
  heading. The pixel angle of an orientation is kept, adjusted only when
  the two axes scale differently.

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
import math
from collections import Counter
from dataclasses import dataclass, field

import rasterio
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout)

from .labels import ImageData
from .orientation_math import principal_angle_rad
from .relocate import _parse, _tail_overlap, verify_candidate

MATCHED = "Found"
# What the preview calls a match at another resolution (its status is
# still MATCHED).
RESCALED = "Found, other resolution"
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
    # The image here is the GT's at another resolution: (width, height)
    # of each, or None for the same file. See _fit_label.
    gt_size: "tuple | None" = None
    local_size: "tuple | None" = None
    masks_left_out: int = 0      # on its new labels, when rescaled

    @property
    def rescaled(self) -> bool:
        return self.gt_size is not None

    @property
    def scale(self) -> float:
        """How many of this image's pixels one GT pixel covers, across."""
        if not self.rescaled or not self.gt_size[0]:
            return 1.0
        return self.local_size[0] / self.gt_size[0]

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

    @property
    def rescaled(self) -> list:
        """Matched images that are at another resolution here."""
        return [m for m in self.matched if m.rescaled]

    @property
    def masks_left_out(self) -> int:
        return sum(m.masks_left_out for m in self.rescaled)

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
    rescaled_images: int = 0     # matched at another resolution
    masks_left_out: int = 0      # painted at the GT's resolution


def _name_key(path: str) -> str:
    return _parse(path).name.lower()


def _size(image) -> str:
    return f"{image.original_width}x{image.original_height}"


def _has_record(image) -> bool:
    return bool(image.original_width or image.original_height
                or image.get_crs() is not None)


def _georeferenced(image) -> bool:
    return image.get_affine() is not None and image.get_crs() is not None


def _from_file(image) -> "ImageData | None":
    """What ``image``'s file says of itself, for an entry that recorded
    nothing: a stand-in with its size, transform and CRS. None when the
    file cannot be read."""
    try:
        with rasterio.open(image.path) as src:
            probe = ImageData(path=image.path, name=image.name,
                              group=image.group, original_width=src.width,
                              original_height=src.height)
            if src.crs is not None:
                probe.set_affine(src.transform, src.crs)
            return probe
    except Exception:                         # noqa: BLE001 - unreadable
        return None


def _other_resolution(local, gt_image) -> "str | None":
    """Why ``local``, of another size, is not the GT's image at another
    resolution - or None when it is taken to be.

    The same name at another size is enough (see the module docstring).
    A different CRS is still a different file, as at the same size.
    """
    here_crs, gt_crs = local.get_crs(), gt_image.get_crs()
    if here_crs is not None and gt_crs is not None and here_crs != gt_crs:
        return ("a file of this name is loaded here, but its coordinate "
                "system differs")
    return None


def _difference(local, gt_image) -> tuple:
    """(why ``local`` is not the GT's image - None when it may be -,
    its (width, height) when it is the GT's image at another resolution).

    Compared on what both projects recorded, without opening anything. A
    side that recorded nothing is not held against the other; an image
    here with no record of its own is checked against its file instead.
    """
    if not _has_record(local):
        if verify_candidate(local.path, gt_image):
            return None, None
        probe = _from_file(local)
        if probe is None:
            return ("a file of this name is loaded here, but it does not "
                    "match"), None
        local = probe
    if (local.original_width and gt_image.original_width
            and local.original_height and gt_image.original_height
            and (local.original_width, local.original_height)
            != (gt_image.original_width, gt_image.original_height)):
        why = _other_resolution(local, gt_image)
        if why is not None:
            return why, None
        return None, (local.original_width, local.original_height)
    here_crs, gt_crs = local.get_crs(), gt_image.get_crs()
    if here_crs is not None and gt_crs is not None and here_crs != gt_crs:
        return ("a file of this name is loaded here, but its coordinate "
                "system differs"), None
    return None, None


def _fit_label(label, gt_image, local) -> None:
    """Put a label made on ``gt_image`` where it belongs on ``local``,
    the same image at another resolution. In place.

    The position goes by ground where both images are georeferenced and
    by fraction of the image otherwise; it is kept inside the image, for
    an extent a pixel smaller here (or, the wrong imagery having been
    loaded, for ground this image does not cover at all). Masks are the other image's pixels and
    go. The pixel angle of an orientation changes only when the two axes
    scale differently; the true-north heading and everything measured in
    metres are the object's and stay.
    """
    sx = local.original_width / gt_image.original_width
    sy = local.original_height / gt_image.original_height
    pixel = None
    if _georeferenced(local) and _georeferenced(gt_image):
        ground = gt_image.pixel_to_latlon(label.pixel_x, label.pixel_y)
        if ground is not None:
            pixel = local.latlon_to_pixel(*ground)
    if pixel is None:
        pixel = (label.pixel_x * sx, label.pixel_y * sy)
    # Inside the image: 0 <= x < width is what every reader requires.
    label.pixel_x = min(max(float(pixel[0]), 0.0),
                        local.original_width - 1e-6)
    label.pixel_y = min(max(float(pixel[1]), 0.0),
                        local.original_height - 1e-6)
    ground = local.pixel_to_latlon(label.pixel_x, label.pixel_y)
    if ground is not None:
        label.lat, label.lon = ground
    label.masks = []
    if (label.orientation_px_rad is not None
            and not math.isclose(sx, sy, rel_tol=1e-3)):
        # The drawn vector, stretched as the image is. (The angle is
        # atan2(-rows, columns): rows grow downward.)
        angle = label.orientation_px_rad
        label.orientation_px_rad = principal_angle_rad(
            0.0, 0.0, math.cos(angle) * sx, -math.sin(angle) * sy)


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
        verified, reasons, other_size = [], [], {}
        for key, image in candidates:
            why, size_here = _difference(image, gt_image)
            if why is None:
                verified.append(key)
                other_size[key] = size_here
            else:
                reasons.append(why)
        if not verified:
            match.status, match.note = MISMATCH, reasons[0]
            continue
        same_size = [key for key in verified if other_size[key] is None]
        if same_size and len(same_size) < len(verified):
            # The GT's own files AND their other-resolution mirror are
            # both loaded: the labels belong on the files they were made
            # on.
            verified = same_size
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
        if other_size[verified[0]] is not None:
            match.local_size = other_size[verified[0]]
            match.gt_size = (gt_image.original_width,
                             gt_image.original_height)

    # One image here, two GT images claiming it: whichever is right, the
    # other's labels would land on the wrong image.
    claims = Counter(m.local_path for m in plan.matched)
    for match in plan.matched:
        if claims[match.local_path] > 1:
            match.status, match.local_path = AMBIGUOUS, None
            match.note = ("another image in the GT fits the same file here")

    for match in plan.matched:
        new = [label for label in gt.images[match.gt_key].labels
               if label.unique_id not in here]
        match.new_labels = len(new)
        if match.rescaled:
            match.masks_left_out = sum(len(label.masks) for label in new)
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
        if match.rescaled:
            result.rescaled_images += 1
            if not _has_record(local):
                # It was matched on what its file says; the labels are
                # placed by that too, and the entry keeps it.
                probe = _from_file(local)
                if probe is not None:
                    local.original_width = probe.original_width
                    local.original_height = probe.original_height
                    local.affine_coeffs = probe.affine_coeffs
                    local.crs_epsg = probe.crs_epsg
                    local.crs_wkt = probe.crs_wkt
        for label in gt_image.labels:
            if label.unique_id in here:
                result.duplicates += 1
                continue
            placed = copy.deepcopy(label)
            if match.rescaled:
                result.masks_left_out += len(placed.masks)
                _fit_label(placed, gt_image, local)
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
        if plan.rescaled:
            line = (f"{_plural(len(plan.rescaled), 'image')} here "
                    f"{'is' if len(plan.rescaled) == 1 else 'are'} at "
                    "another resolution than in the GT: each label is put "
                    "at the same place on the ground.")
            if plan.masks_left_out:
                line += (f" {_plural(plan.masks_left_out, 'mask')} painted "
                         "at the GT's resolution will be left out, to be "
                         "redrawn.")
            lines.append(line)
        lines.append("Images are matched by filename and checked against "
                     "the coordinate system the GT recorded. One of another "
                     "size is taken to be the same imagery at another "
                     "resolution - check the sizes below are what you "
                     "expect.")
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
                if match.rescaled:
                    where += (f"  -  {match.local_size[0]}x"
                              f"{match.local_size[1]} here, "
                              f"{match.gt_size[0]}x{match.gt_size[1]} in "
                              f"the GT (x{match.scale:.3g})")
                    if match.masks_left_out:
                        where += (f"; {_plural(match.masks_left_out, 'mask')}"
                                  " left out")
            else:
                count = str(match.labels)
                where = match.note
            status = (RESCALED if match.status == MATCHED and match.rescaled
                      else match.status)
            item = QTreeWidgetItem([match.name, status, count, where])
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
