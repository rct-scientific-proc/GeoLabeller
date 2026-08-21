"""Finding a project's images again on a machine where the paths changed.

Projects are shared between machines that hold the same imagery under
different paths - another user profile, another drive letter, a copied
directory tree. The project file stores absolute paths, so on the second
machine the images are "missing" even though they are all there.

Three ways back, from cheapest to most explicit:

- ``silently_resolve``: try each missing path against the project file's own
  folder, matching progressively longer path tails. When the imagery was
  copied or synced alongside the project, this fixes everything before the
  user sees a single warning.
- ``infer_prefix_rule`` / ``apply_prefix_rule``: the user locates ONE missing
  file by hand; the differing prefixes of the old and new locations become a
  rule that relocates every sibling without searching anything.
- ``build_basename_index`` / ``match_missing``: the user points at a base
  directory; one walk indexes it and each missing image is matched by
  filename, verified against the metadata the project already stores.

Matching is deliberately conservative. The project knows each image's
original dimensions (and CRS when georeferenced), so a candidate that merely
shares a filename is checked against them before it is trusted; when several
candidates survive, the one whose path tail agrees most with the old path
wins, and a genuine tie resolves nothing rather than guessing. A wrong match
would silently attach every label to the wrong image, which is far worse
than asking the user to look.

The pure functions live Qt-free at the top so the whole policy is testable
headlessly; the dialog at the bottom is only chrome around them.
"""
import os
import re
from dataclasses import dataclass
from pathlib import PurePath, PurePosixPath, PureWindowsPath

import rasterio

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)


# ---------------------------------------------------------------------------
# Pure matching logic (no Qt)
# ---------------------------------------------------------------------------

def _parse(path: str) -> PurePath:
    """Parse a STORED project path with its authoring OS's rules.

    A bare PurePath uses the running OS's flavor, so a Windows-authored
    path opened on Linux parsed as one giant component and the whole
    matching stack went inert. The flavor is decided by the path itself:
    backslashes or a drive letter mean Windows, otherwise POSIX.
    """
    if "\\" in path or re.match(r"^[A-Za-z]:", path):
        return PureWindowsPath(path)
    return PurePosixPath(path)


FOUND = "found"
AMBIGUOUS = "ambiguous"
MISSING = "missing"


@dataclass
class Resolution:
    """Where one missing image ended up after a matching pass."""
    old_path: str
    status: str                 # FOUND / AMBIGUOUS / MISSING
    new_path: str | None = None
    note: str = ""


def _tail_overlap(a: str, b: str) -> int:
    """How many trailing path components two paths share (case-insensitive)."""
    pa = [p.lower() for p in _parse(a).parts]
    pb = [p.lower() for p in _parse(b).parts]
    n = 0
    while n < len(pa) and n < len(pb) and pa[-1 - n] == pb[-1 - n]:
        n += 1
    return n


def verify_candidate(candidate: str, image) -> bool:
    """Does this file plausibly BE the project's image, not just share a name?

    Checks the stored original dimensions and, when the project recorded one,
    the CRS. A project written before those fields existed verifies on the
    filename alone - the caller's tie-breaking still applies.
    """
    width = getattr(image, "original_width", 0)
    height = getattr(image, "original_height", 0)
    epsg = getattr(image, "crs_epsg", None)
    if not width and not height and epsg is None:
        return True   # nothing recorded to check against
    try:
        with rasterio.open(candidate) as src:
            if width and src.width != width:
                return False
            if height and src.height != height:
                return False
            if epsg is not None:
                src_epsg = src.crs.to_epsg() if src.crs is not None else None
                if src_epsg != epsg:
                    return False
    except Exception:
        return False
    return True


def resolve_against_dir(missing_path: str, base_dir: str) -> str | None:
    """Resolve a missing path by matching its tail under *base_dir*.

    Tries the basename, then parent/basename, and so on - so an image that
    lived in ``.../survey/north/a.tif`` is found at
    ``<base>/survey/north/a.tif`` before ``<base>/a.tif`` is even considered
    ... in fact the longest tail that exists wins.
    """
    parts = _parse(missing_path).parts
    best = None
    for n in range(1, len(parts)):
        candidate = os.path.join(base_dir, *parts[len(parts) - n:])
        if os.path.isfile(candidate):
            best = candidate    # keep looking: longer tails are surer
    return best


def silently_resolve(project, project_dir: str, verify=verify_candidate) -> int:
    """Fix missing image paths relative to the project file's folder.

    The zero-UI case: the imagery travelled with the project. Returns how
    many images were relocated; anything unresolved is left for the dialog.
    """
    relocated = 0
    for old_path in list(project.images):
        if os.path.exists(old_path):
            continue
        candidate = resolve_against_dir(old_path, project_dir)
        if candidate and verify(candidate, project.images[old_path]):
            if project.relocate_image(old_path, os.path.abspath(candidate)):
                relocated += 1
    return relocated


def infer_prefix_rule(old_path: str, new_path: str) -> tuple[str, str] | None:
    """The prefix substitution that maps *old_path* onto *new_path*.

    "Same imagery, different user profile" is almost always one prefix swap;
    locating a single file by hand yields the rule for every sibling. Returns
    ``None`` when even the filenames differ - there is no rule to infer.
    """
    shared = _tail_overlap(old_path, new_path)
    if shared == 0:
        return None
    old_parts = _parse(old_path).parts
    new_parts = _parse(new_path).parts
    old_prefix = str(PurePath(*old_parts[:len(old_parts) - shared])) \
        if len(old_parts) > shared else ""
    new_prefix = str(PurePath(*new_parts[:len(new_parts) - shared])) \
        if len(new_parts) > shared else ""
    return old_prefix, new_prefix


def apply_prefix_rule(missing_path: str, rule: tuple[str, str]) -> str | None:
    """The rule applied to one path, or None when it does not apply."""
    old_prefix, new_prefix = rule
    norm_path = _parse(missing_path)
    norm_old = _parse(old_prefix)
    old_parts = norm_old.parts
    if [p.lower() for p in norm_path.parts[:len(old_parts)]] != \
            [p.lower() for p in old_parts]:
        return None
    candidate = os.path.join(new_prefix, *norm_path.parts[len(old_parts):])
    return candidate if os.path.isfile(candidate) else None


def build_basename_index(base_dir: str, wanted_names: set[str]) -> dict:
    """One walk of *base_dir*: {lowercase basename: [full paths]}.

    Only names in *wanted_names* (lowercased) are collected, so indexing a
    huge tree costs one traversal and stores next to nothing.
    """
    index: dict[str, list[str]] = {}
    for root, _dirs, files in os.walk(base_dir):
        for name in files:
            key = name.lower()
            if key in wanted_names:
                index.setdefault(key, []).append(os.path.join(root, name))
    return index


def match_missing(images: list, index: dict,
                  verify=verify_candidate) -> list[Resolution]:
    """Match each missing image against a basename index.

    One verified candidate wins outright. Several survivors fall back to the
    largest path-tail overlap with the old location; a tie stays AMBIGUOUS -
    relocating a label set onto the wrong twin image is the one outcome this
    feature must never produce.
    """
    results = []
    for image in images:
        old_path = image.path
        candidates = index.get(_parse(old_path).name.lower(), [])
        survivors = [c for c in candidates if verify(c, image)]
        if not survivors:
            results.append(Resolution(
                old_path, MISSING,
                note=(f"{len(candidates)} name match(es), none verified"
                      if candidates else "no file of this name")))
            continue
        if len(survivors) == 1:
            results.append(Resolution(old_path, FOUND, survivors[0]))
            continue
        scored = sorted(survivors,
                        key=lambda c: _tail_overlap(c, old_path),
                        reverse=True)
        if _tail_overlap(scored[0], old_path) > _tail_overlap(scored[1],
                                                              old_path):
            results.append(Resolution(
                old_path, FOUND, scored[0],
                note=f"chosen from {len(survivors)} verified candidates"))
        else:
            results.append(Resolution(
                old_path, AMBIGUOUS,
                note=f"{len(survivors)} equally plausible candidates"))

    # Two missing images may both have matched the SAME file - twin images
    # whose only surviving candidate is one file cannot both be it. Award it
    # to the claimant whose old path agrees strictly best, or to nobody:
    # merging two label sets onto one image is exactly the kind of guess
    # this module refuses to make.
    claims: dict[str, list[Resolution]] = {}
    for res in results:
        if res.status == FOUND:
            claims.setdefault(os.path.normcase(res.new_path), []).append(res)
    for claimants in claims.values():
        if len(claimants) < 2:
            continue
        claimants.sort(key=lambda r: _tail_overlap(r.new_path, r.old_path),
                       reverse=True)
        best, runner_up = claimants[0], claimants[1]
        keep = (_tail_overlap(best.new_path, best.old_path)
                > _tail_overlap(runner_up.new_path, runner_up.old_path))
        for res in claimants[0 if not keep else 1:]:
            res.status = AMBIGUOUS
            res.new_path = None
            res.note = (f"{len(claimants)} missing images matched this "
                        "same file")
    return results


# ---------------------------------------------------------------------------
# The dialog
# ---------------------------------------------------------------------------

class RelocateImagesDialog(QDialog):
    """Find a shared project's images on this machine.

    Two ways in: search a base directory (filename match, verified against
    the project's stored metadata), or locate one file by hand and let its
    old-vs-new prefix relocate every sibling. Either way the result is shown
    per image and nothing is applied until the user says so.
    """

    def __init__(self, images: list, parent=None):
        """``images`` are the ImageData objects whose paths are missing."""
        super().__init__(parent)
        self._images = list(images)
        self._resolutions: dict[str, Resolution] = {}
        self.setWindowTitle("Locate Missing Images")
        self.setMinimumSize(700, 400)
        self._build_ui()

    # -- UI -----------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        intro = QLabel(
            f"{len(self._images)} image(s) in this project were not found "
            "at their recorded paths. Search a folder for them by name, or "
            "locate one by hand and its new location will be applied to the "
            "rest. Matches are verified against each image's recorded size "
            "and CRS before being trusted.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Image", "Recorded path", "Result"])
        self.tree.setRootIsDecorated(False)
        for image in self._images:
            item = QTreeWidgetItem(
                [_parse(image.path).name, image.path, "missing"])
            item.setData(0, Qt.UserRole, image.path)
            item.setToolTip(1, image.path)
            self.tree.addTopLevelItem(item)
        self.tree.resizeColumnToContents(0)
        layout.addWidget(self.tree)

        buttons = QHBoxLayout()
        search_btn = QPushButton("Search a folder...")
        search_btn.setToolTip(
            "Walk a directory once and match the missing images by filename.")
        search_btn.clicked.connect(self._search_folder)
        locate_btn = QPushButton("Locate one file...")
        locate_btn.setToolTip(
            "Point at the new location of any one missing image; the path "
            "difference is applied to all of them.")
        locate_btn.clicked.connect(self._locate_one)
        buttons.addWidget(search_btn)
        buttons.addWidget(locate_btn)
        buttons.addStretch()
        layout.addLayout(buttons)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        self._buttons.button(QDialogButtonBox.Apply).setEnabled(False)
        self._buttons.button(QDialogButtonBox.Apply).clicked.connect(
            self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    # -- Matching passes ----------------------------------------------------

    def _search_folder(self):
        base = QFileDialog.getExistingDirectory(
            self, "Search this folder for the missing images")
        if base:
            self.run_directory_search(base)

    def run_directory_search(self, base_dir: str):
        """Index *base_dir* and match every image (separated for testing)."""
        wanted = {_parse(i.path).name.lower() for i in self._images}
        index = build_basename_index(base_dir, wanted)
        self._show_results(match_missing(self._images, index))

    def _locate_one(self):
        current = self.tree.currentItem() or self.tree.topLevelItem(0)
        if current is None:
            return
        old_path = current.data(0, Qt.UserRole)
        chosen, _ = QFileDialog.getOpenFileName(
            self, f"New location of {_parse(old_path).name}",
            "", "Imagery (*.tif *.tiff);;All Files (*)")
        if chosen:
            self.run_prefix_relocation(old_path, chosen)

    def run_prefix_relocation(self, old_path: str, new_path: str):
        """Infer the prefix rule from one located file and apply it to all."""
        rule = infer_prefix_rule(old_path, new_path)
        results = []
        for image in self._images:
            if image.path == old_path:
                # The located file itself still gets verified: pointing at
                # the wrong file must not silently rehome its labels.
                if verify_candidate(new_path, image):
                    results.append(Resolution(image.path, FOUND, new_path))
                else:
                    results.append(Resolution(
                        image.path, MISSING,
                        note="the chosen file does not match this image's "
                             "recorded size/CRS"))
                continue
            candidate = apply_prefix_rule(image.path, rule) if rule else None
            if candidate and verify_candidate(candidate, image):
                results.append(Resolution(image.path, FOUND, candidate))
            else:
                results.append(Resolution(
                    image.path, MISSING, note="not at the inferred location"))
        self._show_results(results)

    def _show_results(self, results: list):
        self._resolutions = {r.old_path: r for r in results}
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            res = self._resolutions.get(item.data(0, Qt.UserRole))
            if res is None:
                continue
            if res.status == FOUND:
                item.setText(2, res.new_path)
                item.setToolTip(2, res.new_path + (
                    f"\n({res.note})" if res.note else ""))
            else:
                item.setText(2, f"{res.status}"
                                f"{' - ' + res.note if res.note else ''}")
        found = sum(1 for r in results if r.status == FOUND)
        self._buttons.button(QDialogButtonBox.Apply).setEnabled(found > 0)
        self._buttons.button(QDialogButtonBox.Apply).setText(
            f"Apply ({found} found)")

    def found_resolutions(self) -> list[Resolution]:
        """The FOUND entries, for the caller to apply on accept."""
        return [r for r in self._resolutions.values() if r.status == FOUND]
