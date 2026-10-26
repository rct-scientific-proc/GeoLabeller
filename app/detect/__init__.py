"""Candidates found by a detector, for the user to check (2.9.0).

A detector proposes places on an image that stand out from their
surroundings - bright or dark patches of a size the user gives - and
scores them. The candidates go into a file beside the project in the
Model Review file format, where they are checked like the ML team's
mistakes: a verdict each, Import makes the labels, and the memory keeps
what was dismissed off the grid next time. Any imagery: the detectors
work on the source values of the bands the display draws, so sonar,
radar and optical are alike to them; sizes are metres where the image
is georeferenced and pixels where it is not.

One detector per module, each documenting its own controls:

- contrast.py: local contrast against a robust background (the CFAR
  family): what stands out from its surroundings, whatever its shape.
- blob.py: difference of Gaussians over the size range: compact,
  round-ish patches of about the sizes asked for.

base.py holds what they share, filters.py the numpy primitives (box and
Gaussian blurs, block statistics, local maxima - numpy only: scipy would
add forty megabytes to the installer for four functions), run.py reads
the imagery and writes the file, evaluate.py checks a sensitivity
against the project's own labels. The dialog is detect_dialog.py; the
main window opens the result in Model Review.
"""
from .blob import Blob
from .contrast import Contrast

DETECTORS = (Contrast, Blob)


def by_key(key: str):
    """The detector class with this key; Contrast for one not known."""
    for detector in DETECTORS:
        if detector.key == key:
            return detector
    return Contrast


__all__ = ["DETECTORS", "Blob", "Contrast", "by_key"]
