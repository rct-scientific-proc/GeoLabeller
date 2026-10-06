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
from dataclasses import dataclass, field

from .. import identity
from .ledger import IGNORE, Verdict


@dataclass
class ImportResult:
    labels_added: int = 0
    ignored: int = 0
    skipped: int = 0            # the image left the project meanwhile
    classes: Counter = field(default_factory=Counter)   # class -> labels
    label_ids: list = field(default_factory=list)

    def summary(self, source: str) -> str:
        parts = [f"{self.labels_added} label"
                 f"{'' if self.labels_added == 1 else 's'} from {source}"]
        if self.classes:
            parts[0] += " (" + ", ".join(
                f"{n} {name}" for name, n in sorted(
                    self.classes.items(), key=lambda item: -item[1])) + ")"
        if self.ignored:
            parts.append(f"{self.ignored} ignored")
        if self.skipped:
            parts.append(f"{self.skipped} skipped, their image gone")
        return "; ".join(parts)


def description_for(chip, source: str) -> str:
    """What the model said, as the label's description."""
    text = f"model: {chip.predicted} {chip.score:.2f}"
    return f"{text}, {source}" if source else text


def apply_verdicts(project, chips, source: str) -> ImportResult:
    """Add the labels and ledger entries the chips' verdicts call for.

    ``chips`` with no verdict are left alone. The class of a labelled
    chip is added to the project's classes if it is somehow not there.
    """
    result = ImportResult()
    stamp = identity.stamp()
    for chip in chips:
        if chip.verdict is None:
            continue
        image = project.images.get(chip.image_path)
        if image is None:
            result.skipped += 1
            continue
        label_id = None
        if chip.verdict == IGNORE:
            result.ignored += 1
        else:
            ground = image.pixel_to_latlon(chip.pixel_x, chip.pixel_y)
            lat, lon = ground if ground is not None else (0.0, 0.0)
            if chip.verdict not in project.classes:
                project.classes.append(chip.verdict)
            label = project.add_label(
                chip.verdict, float(chip.pixel_x), float(chip.pixel_y),
                float(lon), float(lat), image.name, image.group or "",
                image.path, description=description_for(chip, source))
            if stamp is not None:
                label.attribution["created"] = dict(stamp)
            label_id = label.id
            result.labels_added += 1
            result.classes[chip.verdict] += 1
            result.label_ids.append(label.id)
        project.model_review.append(Verdict(
            image=chip.image_name, pixel_x=float(chip.pixel_x),
            pixel_y=float(chip.pixel_y), predicted=chip.predicted,
            score=float(chip.score), became=chip.verdict, source=source,
            label_id=label_id, by=stamp["by"] if stamp else "",
            at=stamp["at"] if stamp else ""))
    return result
