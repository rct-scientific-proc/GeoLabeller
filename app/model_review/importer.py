"""The verdicts into the project.

Every chip given a class becomes a label on its image: at the chip's
centre, with lon/lat through the image's georeferencing where it has
any, attributed to the current user, and carrying a description that
says what the model said - "model: vessel 0.97, round3.json" - and,
after " - ", whatever the file said of the chip in words (2.9.16) - so
"this became that" can be read off the label itself, in the Snippet
Editor and wherever descriptions go. Ignored chips make no label.

A chip labelled already (2.9.4) - a label on its image within the
distance, not its own - gives its verdict to that label: the label
takes the class, if different, and the chip is tied to it; unless the
chip asks for a new label beside it (``as_new``), the case of two
objects close together. Ignore on such a chip leaves the label alone.

A chip the file's note says was reviewed before, given a new verdict:
the label it made changes class, is removed for Ignore, or is made for
a chip ignored before; one re-centred has its label moved.

Every chip given a verdict comes back in ``ImportResult.outcomes`` as
the note the window writes into the review file (outcomes.py), which
is the memory now; nothing is written to the project but the labels.
Nothing here is undoable by itself: the main window wraps
``apply_verdicts`` in one history step that carries the labels it
creates or changes.

Qt-free.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .. import identity
from .ledger import IGNORE, Verdict
from .source import called


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
    adopted: int = 0            # verdicts given to a label already there
    as_new: int = 0             # new labels beside one already there
    changed_ids: list = field(default_factory=list)
    removed_ids: list = field(default_factory=list)
    as_new_ids: list = field(default_factory=list)      # spared the
                                                        # duplicate check
    outcomes: dict = field(default_factory=dict)        # chip key -> Verdict

    def summary(self, source: str) -> str:
        parts = [f"{self.labels_added} label"
                 f"{'' if self.labels_added == 1 else 's'} from {source}"]
        if self.classes:
            parts[0] += " (" + ", ".join(
                f"{n} {name}" for name, n in sorted(
                    self.classes.items(), key=lambda item: -item[1])) + ")"
        if self.ignored:
            parts.append(f"{self.ignored} ignored")
        if self.adopted:
            parts.append(f"{self.adopted} onto label"
                         f"{'' if self.adopted == 1 else 's'} already there")
        if self.as_new:
            parts.append(f"{self.as_new} as new label"
                         f"{'' if self.as_new == 1 else 's'} beside one")
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
    """What the model said, as the label's description - or, for a
    detector's candidate, that it was found and how strongly - and the
    file's own words on the chip after " - ", when it has any."""
    text = called(chip)
    if source:
        text = f"{text}, {source}"
    if chip.description:
        text = f"{text} - {chip.description}"
    return text


def _describe(old, description: str):
    """The file's words onto a label already there, once: appended
    after " - " unless the label's description holds them already."""
    if description and description not in (old.description or ""):
        old.description = (f"{old.description} - {description}"
                           if old.description else description)


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


def outcome_for(chip, label_id, stamp, became) -> Verdict:
    """The note for the chip's verdict, as the review file keeps it:
    the centre as the file gave it, what the chip became, the label it
    made or took over, who and when."""
    return Verdict(
        image=chip.image_name, pixel_x=float(chip.source_x),
        pixel_y=float(chip.source_y), predicted=chip.predicted,
        score=float(chip.score), became=became, label_id=label_id,
        by=stamp["by"] if stamp else "", at=stamp["at"] if stamp else "",
        group=chip.group or "")


def _reclass(old, verdict: str, project, stamp, result: ImportResult,
             description: str = ""):
    """The label takes the class, if it has not got it - and the
    chip's description, if it has not got that (2.9.16)."""
    if verdict not in project.classes:
        project.classes.append(verdict)
    if old.class_name != verdict:
        old.class_name = verdict
        if stamp is not None:
            old.attribution["class"] = dict(stamp)
        result.labels_changed += 1
        result.classes[verdict] += 1
    _describe(old, description)


def apply_verdicts(project, chips, source: str) -> ImportResult:
    """Add the labels the chips' verdicts call for, and give back a
    note per chip for the review file.

    ``chips`` with no verdict are left alone - unless reviewed before
    and re-centred (2.8.11): the label they made moves. A chip reviewed
    before given a new verdict (2.8.5): the label it made changes
    class, is removed for Ignore, or is made for a chip ignored before.
    A chip labelled already (2.9.4) gives its verdict to the nearest
    label, or makes a new one beside it when it asks to.
    """
    result = ImportResult()
    stamp = identity.stamp()
    for chip in chips:
        earlier = chip.earlier if chip.reviewed_before() else None
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
            old = None
            if earlier.label_id is not None:
                old_image, old = project.get_label_by_id(earlier.label_id)
                if old is not None and old_image is not image:
                    # The note names a label on another image of the
                    # same name: neither the label nor the note is this
                    # chip's (2.8.17).
                    old = None
            if verdict == IGNORE:
                if old is not None:
                    project.remove_label(old.id)
                    result.labels_removed += 1
                    result.removed_ids.append(old.id)
                result.ignored += 1
            elif old is not None:
                _reclass(old, verdict, project, stamp, result,
                         chip.description)
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
                if old.id not in result.changed_ids:
                    # Touched, changed or not: not one of the new.
                    result.changed_ids.append(old.id)
            else:
                label = _label_for(project, image, chip, source, stamp,
                                   verdict)
                label_id = label.id
                result.labels_added += 1
                result.classes[verdict] += 1
                result.label_ids.append(label.id)
            result.outcomes[chip.key] = outcome_for(chip, label_id, stamp,
                                                    verdict)
            continue
        if verdict == IGNORE:
            # On a chip labelled already the label stays as it is.
            result.ignored += 1
        elif chip.labelled_already() and not chip.as_new:
            nearest = chip.nearest()
            old_image, old = project.get_label_by_id(nearest.label_id)
            if old is None or old_image is not image:
                # Gone since the window looked: a new label after all.
                label = _label_for(project, image, chip, source, stamp,
                                   verdict)
                label_id = label.id
                result.labels_added += 1
                result.classes[verdict] += 1
                result.label_ids.append(label.id)
            else:
                _reclass(old, verdict, project, stamp, result,
                         chip.description)
                label_id = old.id
                result.adopted += 1
                result.label_ids.append(old.id)
                if old.id not in result.changed_ids:
                    result.changed_ids.append(old.id)
        else:
            label = _label_for(project, image, chip, source, stamp, verdict)
            label_id = label.id
            result.labels_added += 1
            result.classes[verdict] += 1
            result.label_ids.append(label.id)
            if chip.labelled_already():
                result.as_new += 1
                result.as_new_ids.append(label.id)
        result.outcomes[chip.key] = outcome_for(chip, label_id, stamp,
                                                verdict)
    return result
