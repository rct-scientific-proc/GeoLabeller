"""The review file as the memory (2.9.4).

Model Review used to keep what each chip became in the project file
(top-level "model_review", formats 5.0 to 5.2). The users asked for
that history out of the project: the labels are the truth of what is
labelled, and a chip near one is told so by comparison with them
(labelled.py), every time, as labels come and go. What a label cannot
carry - a chip looked at and ignored, and which label an import made
or took over for a chip - is written back into the review file itself,
on the chip's own record, under "review":

    {"image": "survey_03.tif", "x": 1812.4, "y": 640.9, "class": "vessel",
     "score": 0.97,
     "review": {"became": "vessel", "label_id": 12, "by": "Ryan",
                "at": "2026-10-27T10:00:00Z"}}

"became" is a class name, or "ignore". The file is rewritten whole and
atomically with every other field as it was, so the model team's file
stays theirs with a note per chip. A file that cannot be written - read
only, a share without rights - is said once; the chips keep their
outcomes for the session. A detector's candidates file is the same
format and gets the same notes, and a new run carries the old file's
notes onto the candidates it finds again (carry_over).

Qt-free.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .ledger import (OUTCOME_KEY, LedgerIndex, Verdict, image_key,
                     outcome_to_dict)
from .source import _LIST_KEYS, parse_candidates


def _records_of(data):
    """The list of records inside the decoded JSON - the list itself,
    so a change to a record is a change to ``data``."""
    if isinstance(data, dict):
        for key in _LIST_KEYS:
            if isinstance(data.get(key), list):
                return data[key]
        raise ValueError("not a list of chips, and no list inside")
    if not isinstance(data, list):
        raise ValueError("not a list of chips")
    return data


def write_outcomes(path, outcomes: dict) -> int:
    """Write ``outcomes`` - record index -> Verdict, or None to take a
    note away - into the file at ``path``, every other field kept, and
    atomically: the file is whole or untouched. Returns how many
    records changed. OSError when the file cannot be written."""
    path = Path(path)
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    records = _records_of(data)
    changed = 0
    for index, verdict in outcomes.items():
        if not (0 <= index < len(records)) or not isinstance(records[index],
                                                              dict):
            continue
        if verdict is None:
            if records[index].pop(OUTCOME_KEY, None) is not None:
                changed += 1
        else:
            records[index][OUTCOME_KEY] = outcome_to_dict(verdict)
            changed += 1
    if not changed:
        return 0
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1)
    os.replace(temp, path)
    return changed


def read_outcomes(path) -> list:
    """The notes a file holds, as Verdicts at the file's own
    coordinates: what a detector run skips, and what a new run carries
    over. An unreadable or missing file is no notes."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        candidates, _malformed = parse_candidates(data)
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    found = []
    for candidate in candidates:
        if candidate.review is None:
            continue
        found.append(verdict_of(candidate, candidate.x, candidate.y,
                                candidate.image, ""))
    return found


def verdict_of(candidate, pixel_x: float, pixel_y: float, image: str,
               group: str) -> Verdict:
    """The Verdict a record's note makes, placed at (pixel_x, pixel_y)
    on ``image``."""
    note = candidate.review
    return Verdict(image=image, pixel_x=float(pixel_x), pixel_y=float(pixel_y),
                   predicted=candidate.predicted, score=float(candidate.score),
                   became=note["became"], label_id=note.get("label_id"),
                   by=note.get("by", ""), at=note.get("at", ""),
                   group=group or "")


def carry_over(records: list, previous: list, tolerance: float) -> int:
    """Give each of ``records`` (dicts with image, x, y) the note of the
    nearest of ``previous`` (Verdicts, read_outcomes) within
    ``tolerance`` on the same image, each note once; how many got one.
    A detector run finds the same spots again: what was ignored last
    time stays ignored, what was imported stays imported."""
    if not previous:
        return 0
    index = LedgerIndex(previous, tolerance)
    taken: set = set()
    given = 0
    records = [r for r in records if isinstance(r, dict)]
    # A record written with its note already (a run that kept a
    # reviewed spot aside) holds its own; the note it carries is spent.
    for record in records:
        note = record.get(OUTCOME_KEY)
        if note is None:
            continue
        for _goodness, verdict in index.candidates(
                image_key(record.get("image", "")), float(record["x"]),
                float(record["y"]), record.get("class")):
            if id(verdict) not in taken and outcome_to_dict(verdict) == note:
                taken.add(id(verdict))
                break
    for record in records:
        if record.get(OUTCOME_KEY) is not None:
            continue
        for _goodness, verdict in index.candidates(
                image_key(record.get("image", "")), float(record["x"]),
                float(record["y"]), record.get("class")):
            if id(verdict) in taken:
                continue
            taken.add(id(verdict))
            record[OUTCOME_KEY] = outcome_to_dict(verdict)
            given += 1
            break
    return given

