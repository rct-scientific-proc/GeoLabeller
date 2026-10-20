"""Re-seating labels on imagery whose files changed size (2.8.6).

A project shared with someone whose copies of the same GeoTIFFs were
cropped - columns cut from the left and the right at widths nobody
wrote down, the georeferencing updated to match - puts every label and
mask a fixed number of pixels off. What the project records is enough
to put them right: each image's size, affine and CRS as they were, and
each label's latitude and longitude.

Tiers, by what the file's header says against the record:

  shift    the same CRS, pixel size and rotation, the origin moved by a
           whole number of pixels - or not at all, for a crop on the
           right or the bottom only: every label and mask moves by that
           offset, labels now off the image are dropped, and nothing
           else is lost.
  regrid   the pixel size or the rotation changed: each label is placed
           from its latitude and longitude; masks, painted on the old
           pixels, are dropped.
  same     the file matches the record.
  unknown  nothing to go by: no georeferencing on one side or the other,
           or a file that cannot be read.

Without a recorded affine (a project from before they were stored) the
labels themselves say: placed from their latitude and longitude on the
file, they should all land the same whole number of pixels from where
they were recorded. That consensus is the offset; a spread means a
regrid.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from affine import Affine
from PyQt5 import sip
from PyQt5.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt5.QtWidgets import (QApplication, QDialog, QDialogButtonBox,
                             QLabel, QPlainTextEdit, QVBoxLayout)

from . import gdal_config
from .debug_log import debug
from .labels import ImageData
from .masks import shift_entry

SAME = "same"
SHIFT = "shift"
REGRID = "regrid"
UNKNOWN = "unknown"
DECLINED = "declined"           # a regrid the user said no to
TOLERANCE_PX = 0.05             # how far from a whole pixel an offset may lie
SCALE_TOLERANCE = 1e-6          # relative: "the same pixel size"


@dataclass
class Header:
    """What a file says of itself."""
    width: int
    height: int
    affine: "Affine | None"     # None for an image without georeferencing
    crs: object                 # a rasterio CRS, or None

    def pixel_size(self) -> float:
        a = self.affine
        return math.hypot(a.a, a.d) if a is not None else 0.0


@dataclass
class Plan:
    """What re-seating one image comes to."""
    path: str
    name: str
    kind: str
    header: "Header | None" = None
    old_width: int = 0
    old_height: int = 0
    old_pixel_size: float = 0.0
    dx: int = 0                 # columns cut from the left (negative: added)
    dy: int = 0                 # rows cut from the top
    moves: dict = field(default_factory=dict)      # label id -> (x, y)
    dropped: list = field(default_factory=list)    # label ids off the image
    masks_moved: int = 0
    masks_lost: int = 0
    reason: str = ""

    def changes(self) -> bool:
        return self.kind in (SHIFT, REGRID)

    def decline(self):
        """A regrid the user would not have."""
        if self.kind == REGRID:
            self.kind = DECLINED

    def describe(self) -> str:
        """One line for the report."""
        n = len(self.moves)
        labels = f"{n} label{'' if n == 1 else 's'}"
        if self.kind == SAME:
            return f"{self.name}: matches the record."
        if self.kind == UNKNOWN:
            return f"{self.name}: not re-seated - {self.reason}."
        if self.kind == DECLINED:
            return (f"{self.name}: the pixel size changed; left as it was "
                    "at your word.")
        h = self.header
        # An image without labels: nothing to move, the record brought up
        # to date with the file (2.8.8).
        unlabelled = not self.moves and not self.dropped
        if self.kind == SHIFT:
            cuts = []
            right = self.old_width - h.width - self.dx
            bottom = self.old_height - h.height - self.dy
            for amount, where in ((self.dx, "the left"), (right, "the right"),
                                  (self.dy, "the top"), (bottom, "the bottom")):
                what = "rows" if where in ("the top", "the bottom") else "columns"
                if amount > 0:
                    cuts.append(f"{amount} {what} cut from {where}")
                elif amount < 0:
                    cuts.append(f"{-amount} {what} added at {where}")
            text = f"{self.name}: " + (", ".join(cuts) or "the same extent")
            if unlabelled:
                text += "; no labels, the record updated"
            else:
                text += f"; {labels}"
                if self.masks_moved:
                    text += (f" and {self.masks_moved} mask"
                             f"{'' if self.masks_moved == 1 else 's'}")
                text += " moved"
        else:
            text = (f"{self.name}: the grid changed ({self.old_pixel_size:.4g} "
                    f"to {h.pixel_size():.4g} per pixel)")
            if unlabelled:
                text += "; no labels, the record updated"
            else:
                text += f"; {labels} placed by latitude and longitude"
                if self.masks_lost:
                    text += (f"; {self.masks_lost} mask"
                             f"{'' if self.masks_lost == 1 else 's'} dropped")
        if self.dropped:
            d = len(self.dropped)
            text += f"; {d} label{'' if d == 1 else 's'} off the image dropped"
        return text + "."


# -- reading and planning -----------------------------------------------------

def read_header(path: str) -> "Header | None":
    """The file's size and georeferencing, or None when it cannot be read."""
    try:
        with gdal_config.opened(path) as src:
            transform = src.transform
            affine = (None if transform is None or transform.is_identity
                      else transform)
            return Header(int(src.width), int(src.height), affine, src.crs)
    except Exception as exc:  # noqa: BLE001 - a header, not a failure
        debug(f"re-seat: cannot read {path}: {type(exc).__name__}: {exc}")
        return None


def _same_grid(old: Affine, new: Affine) -> bool:
    return all(math.isclose(a, b, rel_tol=SCALE_TOLERANCE, abs_tol=1e-12)
               for a, b in ((old.a, new.a), (old.b, new.b),
                            (old.d, new.d), (old.e, new.e)))


def _as_file(image, header: Header) -> ImageData:
    """An image entry with the file's georeferencing, for placing by
    latitude and longitude."""
    temp = ImageData(path=image.path, name=image.name, group=image.group,
                     original_width=header.width,
                     original_height=header.height)
    temp.set_affine(header.affine, header.crs)
    return temp


def _start(image, kind: str, header: "Header | None") -> Plan:
    old = image.get_affine()
    return Plan(image.path, image.name, kind, header,
                int(image.original_width or 0), int(image.original_height or 0),
                math.hypot(old.a, old.d) if old is not None else 0.0)


def _shift(image, header: Header, dx: int, dy: int) -> Plan:
    p = _start(image, SHIFT, header)
    p.dx, p.dy = int(dx), int(dy)
    for label in image.labels:
        x, y = label.pixel_x - dx, label.pixel_y - dy
        if 0 <= x < header.width and 0 <= y < header.height:
            p.moves[label.id] = (x, y)
            p.masks_moved += len(label.masks)
        else:
            p.dropped.append(label.id)
    return p


def _regrid(image, header: Header) -> Plan:
    p = _start(image, REGRID, header)
    temp = _as_file(image, header)
    for label in image.labels:
        pixel = temp.latlon_to_pixel(label.lat, label.lon)
        if pixel is None:
            p.dropped.append(label.id)
            continue
        x, y = pixel
        if 0 <= x < header.width and 0 <= y < header.height:
            p.moves[label.id] = (float(x), float(y))
            p.masks_lost += len(label.masks)
        else:
            p.dropped.append(label.id)
    return p


def _by_affine(image, header: Header, old: Affine) -> Plan:
    new = header.affine
    if _same_grid(old, new):
        # The file's pixel (0, 0), in the old grid: that is the offset.
        col, row = ~old * (new.c, new.f)
        dx, dy = round(col), round(row)
        if abs(col - dx) <= TOLERANCE_PX and abs(row - dy) <= TOLERANCE_PX:
            return _shift(image, header, dx, dy)
    return _regrid(image, header)


def _by_consensus(image, header: Header) -> Plan:
    """No recorded affine: the labels, placed by their latitude and
    longitude on the file, say how far they moved."""
    temp = _as_file(image, header)
    offsets = []
    for label in image.labels:
        pixel = temp.latlon_to_pixel(label.lat, label.lon)
        if pixel is None:
            return _start(image, UNKNOWN, header)
        offsets.append((label.pixel_x - pixel[0], label.pixel_y - pixel[1]))
    if not offsets:
        return _shift(image, header, 0, 0)   # nothing to move; the record
    dx = round(statistics.median(ox for ox, _oy in offsets))
    dy = round(statistics.median(oy for _ox, oy in offsets))
    if all(abs(ox - dx) <= TOLERANCE_PX and abs(oy - dy) <= TOLERANCE_PX
           for ox, oy in offsets):
        return _shift(image, header, dx, dy)
    return _regrid(image, header)


def plan(image, header: "Header | None") -> Plan:
    """What re-seating ``image`` on the file ``header`` describes calls for."""
    if header is None:
        p = _start(image, UNKNOWN, None)
        p.reason = "the file could not be read"
        return p
    if (header.width, header.height) == (int(image.original_width or 0),
                                         int(image.original_height or 0)):
        return _start(image, SAME, header)
    if not image.original_width or not image.original_height:
        p = _start(image, UNKNOWN, header)
        p.reason = "the project recorded no size for it"
        return p
    if header.affine is None or header.crs is None:
        p = _start(image, UNKNOWN, header)
        p.reason = "the file has no georeferencing to go by"
        return p
    old = image.get_affine()
    old_crs = image.get_crs()
    if old is not None and old_crs is not None:
        if old_crs != header.crs:
            p = _start(image, UNKNOWN, header)
            p.reason = "the file's CRS is not the one the project recorded"
            return p
        return _by_affine(image, header, old)
    return _by_consensus(image, header)


def is_cropped_version(image, src) -> bool:
    """Is this open file a crop of the project's image: the same grid,
    its origin a whole number of pixels into the recorded extent, and
    the whole of it within that extent (a pixel of slack)?"""
    old = image.get_affine()
    transform = getattr(src, "transform", None)
    if (old is None or transform is None or transform.is_identity
            or not image.original_width or not image.original_height
            or not _same_grid(old, transform)):
        return False
    col, row = ~old * (transform.c, transform.f)
    dx, dy = round(col), round(row)
    if abs(col - dx) > TOLERANCE_PX or abs(row - dy) > TOLERANCE_PX:
        return False
    return (dx >= -1 and dy >= -1
            and dx + src.width <= image.original_width + 1
            and dy + src.height <= image.original_height + 1)


# -- applying ------------------------------------------------------------------

def apply(project, p: Plan):
    """Move the labels and masks, drop what is off the image, record the
    file's size and georeferencing. Inside the caller's undo step."""
    image = project.images.get(p.path)
    if image is None or not p.changes():
        return
    header = p.header
    for label_id in p.dropped:
        project.remove_label(label_id)
    for label in image.labels:
        move = p.moves.get(label.id)
        if move is None:
            continue
        label.pixel_x, label.pixel_y = float(move[0]), float(move[1])
        if p.kind == SHIFT:
            label.masks = [shift_entry(m, p.dx, p.dy, header.width,
                                       header.height) for m in label.masks]
        else:
            label.masks = []
    image.original_width = int(header.width)
    image.original_height = int(header.height)
    if header.affine is not None and header.crs is not None:
        image.set_affine(header.affine, header.crs)


def regrid_question(plans: list) -> str:
    n = len(plans)
    masks = sum(p.masks_lost for p in plans)
    text = (f"{n} image{'' if n == 1 else 's'} changed pixel size, not just "
            "extent. The labels can be placed by their latitude and "
            "longitude")
    if masks:
        text += (f", but {masks} mask{'' if masks == 1 else 's'} painted on "
                 "the old pixels would be dropped")
    return text + ". Place them?"


def report(plans: list) -> str:
    """A line per image that changed or could not be re-seated, the
    ones that match the record folded into one line at the end: a
    thousand-image project reads as a short list (2.8.7)."""
    lines = [p.describe() for p in plans if p.kind != SAME]
    same = sum(1 for p in plans if p.kind == SAME)
    if same:
        lines.append(f"{same} image{'' if same == 1 else 's'} match"
                     f"{'es' if same == 1 else ''} the record.")
    return "\n".join(lines)


def summary(plans: list) -> str:
    """One line for the status bar."""
    changed = [p for p in plans if p.changes()]
    labelled = [p for p in changed if p.moves or p.dropped]
    records = len(changed) - len(labelled)     # unlabelled: the record
    labels = sum(len(p.moves) for p in labelled)
    dropped = sum(len(p.dropped) for p in labelled)
    others = len(plans) - len(changed)
    parts = []
    if labelled:
        parts.append(f"Re-seated {labels} label{'' if labels == 1 else 's'} "
                     f"on {len(labelled)} image"
                     f"{'' if len(labelled) == 1 else 's'}")
        if dropped:
            parts.append(f"{dropped} dropped")
    if records:
        parts.append(f"{records} record{'' if records == 1 else 's'} of "
                     "unlabelled images updated")
    if changed:
        parts.append("Ctrl+Z takes it back")
    if others:
        parts.append(f"{others} image{'' if others == 1 else 's'} unchanged")
    return "; ".join(parts) if parts else "Nothing to re-seat."


class ReportDialog(QDialog):
    """The report, scrolling, in a window that fits the screen: a
    message box grows with its text, and one line per image put its
    OK button off the screen for anyone with many images (2.8.7)."""

    def __init__(self, title: str, headline: str, text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        self.headline = QLabel(headline)
        self.headline.setWordWrap(True)
        font = self.headline.font()
        font.setBold(True)
        self.headline.setFont(font)
        layout.addWidget(self.headline)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlainText(text)
        layout.addWidget(self.text, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        self.buttons.accepted.connect(self.accept)
        layout.addWidget(self.buttons)
        width, height = 760, 520
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(width, max(320, available.width() - 80))
            height = min(height, max(240, available.height() - 120))
        self.resize(width, height)


# -- the look at a project's files, off the UI thread ----------------------

def mismatched_paths(project, should_stop=None,
                     labelled_only: bool = False) -> list:
    """The images whose file's size differs from the record: every
    image with a recorded size (the command), or the labelled ones
    (the look after a project opens, which must not turn a
    metadata-only load into a read of every file; an unlabelled
    image's record is brought up to date when its layer first reads
    the file instead, 2.8.8)."""
    should_stop = should_stop or (lambda: False)
    paths = []
    for image in list(project.images.values()):
        if should_stop():
            break
        if not image.original_width or (labelled_only
                                        and not image.labels):
            continue
        header = read_header(image.path)
        if header is not None and (header.width, header.height) != (
                int(image.original_width), int(image.original_height)):
            paths.append(image.path)
    return paths


class _Signals(QObject):
    finished = pyqtSignal(int, object)


class _Runnable(QRunnable):

    def __init__(self, token: int, is_current, project, signals):
        super().__init__()
        self.token, self.is_current = token, is_current
        self.project, self.signals = project, signals

    def run(self):
        paths = mismatched_paths(
            self.project, lambda: not self.is_current(self.token),
            labelled_only=True)
        self.signals.finished.emit(self.token, paths)


class SizeCheck(QObject):
    """Reads the headers of a project's labelled images off the UI
    thread when a project has opened; ``done`` carries the paths whose
    files differ in size from the record. A newer project supersedes."""

    done = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._token = 0
        self._alive: set = set()

    def _is_current(self, token: int) -> bool:
        return token == self._token

    def start(self, project):
        self._token += 1
        if not any(img.labels for img in project.images.values()):
            return
        signals = _Signals()
        self._alive.add(signals)
        signals.finished.connect(
            lambda token, paths, s=signals: self._on_finished(token, paths, s))
        self._pool.start(_Runnable(self._token, self._is_current, project,
                                   signals))

    def cancel(self):
        self._token += 1

    def wait(self, ms: int = 10000) -> bool:
        return self._pool.waitForDone(ms)

    def _on_finished(self, token: int, paths, signals):
        self._alive.discard(signals)
        if sip.isdeleted(self) or token != self._token:
            return
        self.done.emit(paths)
