"""The verdicts into the project.

Every chip given a class becomes a label on its image: at the chip's
centre, with lon/lat through the image's georeferencing where it has
any, attributed to the current user, and carrying a description that
says what the model said - "model: vessel 0.97, round3.json" - so "this
became that" can be read off the label itself, in the Snippet Editor
and wherever descriptions go. Every chip given a verdict, Ignore
included, becomes a ledger entry (ledger.py), which is what keeps it off
the grid next round. Ignored chips make no label.

Nothing here is undoable by itself: the main window wraps
``apply_verdicts`` in one history step that carries the labels it
creates and the project's "model_review" list.

Qt-free.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import astuple, dataclass, field

from .. import identity
from .ledger import IGNORE, Verdict


@dataclass
class ImportResult:
    labels_added: int = 0
    ignored: int = 0
    skipped: int = 0            # the image left the project meanwhile
    classes: Counter = field(default_factory=Counter)   # class -> labels
    label_ids: list = field(default_factory=list)       # made or changed
    labels_changed: int = 0     # a second look (2.8.5): class changed...
    labels_removed: int = 0     # ...or the label gone, for Ignore
    labels_moved: int = 0       # ...or moved, the chip re-centred (2.8.11)
    changed_ids: list = field(default_factory=list)
    removed_ids: list = field(default_factory=list)

    def summary(self, source: str) -> str:
        parts = [f"{self.labels_added} label"
                 f"{'' if self.labels_added == 1 else 's'} from {source}"]
        if self.classes:
            parts[0] += " (" + ", ".join(
                f"{n} {name}" for name, n in sorted(
                    self.classes.items(), key=lambda item: -item[1])) + ")"
        if self.ignored:
            parts.append(f"{self.ignored} ignored")
        if self.labels_changed:
            parts.append(f"{self.labels_changed} label"
                         f"{'' if self.labels_changed == 1 else 's'} "
                         "changed")
        if self.labels_removed:
            parts.append(f"{self.labels_removed} removed")
        if self.labels_moved:
            parts.append(f"{self.labels_moved} moved")
        if self.skipped:
            parts.append(f"{self.skipped} skipped, their image gone")
        return "; ".join(parts)


def description_for(chip, source: str) -> str:
    """What the model said, as the label's description."""
    text = f"model: {chip.predicted} {chip.score:.2f}"
    return f"{text}, {source}" if source else text


def _label_for(project, image, chip, source: str, stamp, verdict=None):
    """A label where the chip is, in its verdict's class."""
    verdict = chip.verdict if verdict is None else verdict
    ground = image.pixel_to_latlon(chip.pixel_x, chip.pixel_y)
    lat, lon = ground if ground is not None else (0.0, 0.0)
    if verdict not in project.classes:
        project.classes.append(verdict)
    label = project.add_label(
        verdict, float(chip.pixel_x), float(chip.pixel_y),
        float(lon), float(lat), image.name, image.group or "",
        image.path, description=description_for(chip, source))
    if stamp is not None:
        label.attribution["created"] = dict(stamp)
    return label


def _ledger_places(project) -> tuple:
    """Where each entry sits in the ledger, by identity and by value:
    found once, so replacing an entry costs nothing however long the
    memory (a scan per chip froze a large round, 2.8.14)."""
    by_id: dict = {}
    by_value: dict = {}
    for i, old in enumerate(project.model_review):
        by_id[id(old)] = i
        by_value.setdefault(astuple(old), i)
    return by_id, by_value


def _remember(project, chip, label_id, source: str, stamp, earlier=None,
              became=None, places=None):
    """The ledger entry for the chip's verdict. The memory keeps the
    centre as the file gave it - what the next round is matched by -
    while the label sits where the chip was re-centred, if it was. In
    place of the earlier round's entry when there is one: a new object,
    so the project's history restores the old. ``places`` is
    _ledger_places(project), built once for a whole import."""
    entry = Verdict(
        image=chip.image_name, pixel_x=float(chip.source_x),
        pixel_y=float(chip.source_y), predicted=chip.predicted,
        score=float(chip.score),
        became=chip.verdict if became is None else became, source=source,
        label_id=label_id, by=stamp["by"] if stamp else "",
        at=stamp["at"] if stamp else "")
    if earlier is not None:
        by_id, by_value = (places if places is not None
                           else _ledger_places(project))
        i = by_id.pop(id(earlier), None)
        if i is None:
            i = by_value.get(astuple(earlier))
        if i is not None and i < len(project.model_review):
            by_value.pop(astuple(earlier), None)
            project.model_review[i] = entry
            return
    project.model_review.append(entry)


def apply_verdicts(project, chips, source: str) -> ImportResult:
    """Add the labels and ledger entries the chips' verdicts call for.

    ``chips`` with no verdict are left alone - unless reviewed before and
    re-centred (2.8.11): the label they made moves. The class of a
    labelled chip is added to the project's classes if it is somehow not
    there. A chip reviewed in an earlier round, given a new verdict
    (2.8.5): the label it made changes class, is removed for Ignore, or
    is made for a chip ignored before; its ledger entry is replaced.
    """
    result = ImportResult()
    stamp = identity.stamp()
    places = _ledger_places(project)
    for chip in chips:
        earlier = chip.earlier
        verdict = chip.verdict
        if verdict is None:
            # Moved since the project last had it - the window's own
            # test (2.8.14). Against the file's centre, a chip put
            # back on it was offered for import and imported nothing.
            if (earlier is None or not chip.moved_since_import()
                    or earlier.became == IGNORE):
                continue
            verdict = earlier.became        # only the centre moved
        image = project.images.get(chip.image_path)
        if image is None:
            result.skipped += 1
            continue
        label_id = None
        if earlier is not None:
            old = (project.get_label_by_id(earlier.label_id)[1]
                   if earlier.label_id is not None else None)
            if verdict == IGNORE:
                if old is not None:
                    project.remove_label(old.id)
                    result.labels_removed += 1
                    result.removed_ids.append(old.id)
                result.ignored += 1
            elif old is not None:
                if verdict not in project.classes:
                    project.classes.append(verdict)
                if old.class_name != verdict:
                    old.class_name = verdict
                    if stamp is not None:
                        old.attribution["class"] = dict(stamp)
                    result.labels_changed += 1
                    result.classes[verdict] += 1
                if chip.moved_since_import() and (
                        old.pixel_x, old.pixel_y) != (chip.pixel_x,
                                                      chip.pixel_y):
                    old.pixel_x = float(chip.pixel_x)
                    old.pixel_y = float(chip.pixel_y)
                    ground = image.pixel_to_latlon(chip.pixel_x, chip.pixel_y)
                    if ground is not None:
                        old.lat, old.lon = float(ground[0]), float(ground[1])
                    if stamp is not None:
                        old.attribution["position"] = dict(stamp)
                    result.labels_moved += 1
                label_id = old.id
                result.label_ids.append(old.id)
                result.changed_ids.append(old.id)
            else:
                label = _label_for(project, image, chip, source, stamp,
                                   verdict)
                label_id = label.id
                result.labels_added += 1
                result.classes[verdict] += 1
                result.label_ids.append(label.id)
            _remember(project, chip, label_id, source, stamp, earlier,
                      became=verdict, places=places)
            continue
        if verdict == IGNORE:
            result.ignored += 1
        else:
            label = _label_for(project, image, chip, source, stamp, verdict)
            label_id = label.id
            result.labels_added += 1
            result.classes[verdict] += 1
            result.label_ids.append(label.id)
        _remember(project, chip, label_id, source, stamp, became=verdict,
                  places=places)
    return result
