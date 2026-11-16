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
     "review": {"became": "vessel", "label_id": 12,
                "label_x": 1820.0, "label_y": 644.5,
                "by": "Ryan", "at": "2026-10-27T10:00:00Z"}}

"became" is a class name, or "ignore". "label_x" / "label_y" is where
the label was when the note was written, in pixels at the project's
resolution (2.10.1): what the chip finds its own label by on the next
open, whatever the chip size is set to. An open writes the place of a
note of before 2.10.1, which has none (2.10.2), and never rewrites one
a note stores (2.10.3). The file is rewritten whole and
atomically with every other field as it was - and every key of another
tool's inside a note (2.10.3) - so the model team's file
stays theirs with a note per chip. A file that cannot be written - read
only, a share without rights - is said once; the chips keep their
outcomes for the session. A detector's candidates file is the same
format and gets the same notes, and a new run carries the old file's
notes onto the candidates it finds again (carry_over) and keeps the
rest of what the file remembers (keep_earlier).

Qt-free.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

from .ledger import (NOTE_KEYS, OUTCOME_KEY, LedgerIndex, Verdict,
                     image_key, outcome_to_dict)
from .source import (_CLASS_KEYS, _IMAGE_KEYS, _LIST_KEYS, _X_KEYS,
                     _Y_KEYS, _first, _number, _tail_overlap,
                     parse_candidates)


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


def write_outcomes(path, outcomes: dict, misplaced=None,
                   anchors=None) -> int:
    """Write ``outcomes`` - record index -> Verdict, or None to take a
    note away - into the file at ``path``, every other field kept, and
    atomically: the file is whole or untouched. Returns how many
    records changed. OSError when the file cannot be written. A record
    that is no longer the chip's - the file replaced since it was
    opened, its image another - takes no note; its index goes into
    ``misplaced`` when a list is given (2.9.6).

    ``anchors`` - record index -> (image name, x, y, predicted class),
    the chip's record as it was opened - tells the chip's record from
    another at its index: Find Candidates rewrites its file in place,
    and with the first run's verdicts kept, a note written by index and
    image alone went onto the second run's spots, which then hid as
    done and one of which claimed the label (2.9.15)."""
    path = Path(path)
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    records = _records_of(data)
    anchors = anchors or {}
    changed = 0
    for index, verdict in outcomes.items():
        if not (0 <= index < len(records)) or not isinstance(records[index],
                                                              dict):
            if misplaced is not None:
                misplaced.append(index)
            continue
        record = records[index]
        anchor = anchors.get(index)
        image = verdict.image if verdict is not None else (
            anchor[0] if anchor else None)
        if not _same_record(record, image, anchor):
            if misplaced is not None:
                misplaced.append(index)
            continue
        if verdict is None:
            if record.pop(OUTCOME_KEY, None) is not None:
                changed += 1
        else:
            note_into(record, verdict)
            changed += 1
    if not changed:
        return 0
    temp = path.with_name(path.name + ".tmp")
    try:
        with open(temp, "w", encoding="utf-8") as handle:
            # The team's own characters kept, not escaped: the file stays
            # theirs to read (2.9.6).
            json.dump(data, handle, indent=1, ensure_ascii=False)
        os.replace(temp, path)
    finally:
        # Only a write that failed leaves one: a read-only file, which
        # an open may now try to write (the labels' places, 2.10.2),
        # would gain a copy of itself beside it at every open.
        temp.unlink(missing_ok=True)
    return changed


def note_into(record: dict, verdict):
    """The verdict's note onto the record, merged into the note it has
    (retest 2026-11-16 R2, 2.10.3): the keys GeoLabeller writes are set,
    or taken away where the verdict has none; any other key - another
    tool's, inside "review" - is kept. Replaced whole, such a key went
    at the first write, and since 2.10.2 an open writes old notes."""
    note = outcome_to_dict(verdict)
    old = record.get(OUTCOME_KEY)
    if not isinstance(old, dict):
        record[OUTCOME_KEY] = note
        return
    for key in NOTE_KEYS:
        if key not in note:
            old.pop(key, None)
    old.update(note)


def _same_record(record: dict, image, anchor) -> bool:
    """Is the record the chip's: its image the note's, by file name -
    the one thing both are sure to agree on - and, with the chip's
    record as opened in hand (``anchor``), at the same spot and of the
    same class, which tell it from the record a rewritten file put at
    its index. Without an anchor, the image is all there is to check."""
    name = None
    for key in _IMAGE_KEYS:
        if isinstance(record.get(key), str):
            name = record[key]
            break
    if (name is not None and image is not None
            and image_key(name) != image_key(image)):
        return False
    if anchor is None:
        return True
    _image, x, y, predicted = anchor
    record_x = _number(_first(record, _X_KEYS))
    record_y = _number(_first(record, _Y_KEYS))
    if (record_x is None or record_y is None
            or abs(record_x - float(x)) > 0.5
            or abs(record_y - float(y)) > 0.5):
        return False
    found = _first(record, _CLASS_KEYS)
    return not isinstance(found, str) or found.strip() == predicted


def read_outcomes(path) -> list:
    """The notes a file holds, as Verdicts at the file's own
    coordinates: what a detector run skips, and what a new run carries
    over. An unreadable or missing file is no notes."""
    try:
        return list(read_records(path)[1].values())
    except OSError:
        return []


def read_records(path) -> tuple:
    """(records, notes): the file's records as they are, and the notes
    among them as read_outcomes gives them, by the record's place in
    the list - what a detector run has to keep (keep_earlier). A
    missing file, or one that is not a file of chips, is ([], {}); one
    that is there and cannot be opened is an OSError - to its writer it
    must not pass for empty."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        records = _records_of(data)
        candidates, _malformed = parse_candidates(data)
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        return [], {}
    return records, {
        candidate.index: verdict_of(candidate, candidate.x, candidate.y,
                                    candidate.image, "")
        for candidate in candidates if candidate.review is not None}


def verdict_of(candidate, pixel_x: float, pixel_y: float, image: str,
               group: str) -> Verdict:
    """The Verdict a record's note makes, placed at (pixel_x, pixel_y)
    on ``image``."""
    note = candidate.review
    return Verdict(image=image, pixel_x=float(pixel_x), pixel_y=float(pixel_y),
                   predicted=candidate.predicted, score=float(candidate.score),
                   became=note["became"], label_id=note.get("label_id"),
                   by=note.get("by", ""), at=note.get("at", ""),
                   group=group or "", label_x=note.get("label_x"),
                   label_y=note.get("label_y"))


def carry_over(records: list, previous: list, tolerance: float,
               found_again: "list | None" = None) -> int:
    """Give each of ``records`` (dicts with image, x, y) the note of the
    nearest of ``previous`` (Verdicts, read_outcomes) within
    ``tolerance`` on the same image, each note once; how many got one.
    A detector run finds the same spots again: what was ignored last
    time stays ignored, what was imported stays imported. The notes
    that went to a record go into ``found_again`` when a list is given:
    those the new records answer for, so their old records can go
    (keep_earlier)."""
    if not previous:
        return 0
    index = LedgerIndex(previous, tolerance)
    taken: set = set()
    given = 0
    records = [r for r in records if isinstance(r, dict)]

    def take(verdict):
        taken.add(id(verdict))
        if found_again is not None:
            found_again.append(verdict)

    def answers(record):
        """The old notes within reach of the record, nearest first:
        the tolerance, or half the candidate's size if that is more -
        the reach the skip has (2.9.6: a big object found a few pixels
        further off kept its note with the skip on and lost it off)."""
        x, y = float(record["x"]), float(record["y"])
        reach = max(float(tolerance), float(record.get("size_px") or 0) / 2)
        found = index.within(image_key(record.get("image", "")), x, y, reach)
        return sorted(found, key=lambda v: math.hypot(v.pixel_x - x,
                                                      v.pixel_y - y))
    # A record written with its note already (a run that kept a
    # reviewed spot aside) answers for a spot the file had: the nearest
    # note in reach that no other record took is spent on it, and it
    # reads as the file has it now - a verdict imported while the run
    # was under way is not written over by the one it replaced
    # (2.10.2). With none left in reach it keeps what it came with.
    for record in records:
        if record.get(OUTCOME_KEY) is None:
            continue
        for verdict in answers(record):
            if id(verdict) not in taken:
                take(verdict)
                note_into(record, verdict)
                break
    for record in records:
        if record.get(OUTCOME_KEY) is not None:
            continue
        for verdict in answers(record):
            if id(verdict) in taken:
                continue
            take(verdict)
            record[OUTCOME_KEY] = outcome_to_dict(verdict)
            given += 1
            break
    return given


def keep_earlier(records: list, earlier: list, notes: dict, covered,
                 images, tolerance: float) -> tuple:
    """What a detector run leaves of the file it replaces, besides its
    own ``records`` (hunt H2, 2.10.2). The candidates file is the review
    memory, and a run knows only the images it looked at: written from
    the run alone, a run on another group - or a stricter one - took
    every ignore and import note it did not find again with it. Of the
    ``earlier`` records (read_records, with their ``notes``):

    - on an image the run looked at (``covered``, paths), one with a
      note stays as it was unless a new record found the spot again -
      the note is then that record's (carry_over) - and one without is
      dropped: the run's own candidates replace it;
    - on any other image - not chosen, not reached before a stop, not
      readable, no longer in the project - every record stays as it
      was, noted or not.

    ``images`` are the paths of the project's images, by which a record
    names its image (_named_among). Returns (kept, answers, elsewhere):
    the records to write after the run's own, in the file's order, and
    how many of them are answers not found again and how many lie on
    images not looked at."""
    named = _named_among(images)
    covered = {named(path) for path in covered} - {None}

    def looked_at(record) -> bool:
        if not isinstance(record, dict):
            return False
        image = _first(record, _IMAGE_KEYS)
        return isinstance(image, str) and named(image) in covered
    there = [looked_at(record) for record in earlier]
    found_again: list = []
    carry_over(records, [verdict for index, verdict in notes.items()
                         if there[index]], tolerance, found_again)
    gone = {id(verdict) for verdict in found_again}

    def answered(index, record) -> bool:
        """Has the record a note that no new record took over. One that
        cannot be read counts: it is not this run's to throw away."""
        if record.get(OUTCOME_KEY) is None:
            return False
        verdict = notes.get(index)
        return verdict is None or id(verdict) not in gone
    kept, answers, elsewhere = [], 0, 0
    for index, record in enumerate(earlier):
        if not there[index]:
            elsewhere += 1
        elif answered(index, record):
            answers += 1
        else:
            continue            # found again, or replaced by the run's
        kept.append(record)
    return kept, answers, elsewhere


def _named_among(paths):
    """A function from an image as a record writes it to the one of
    ``paths`` it names, or None: the path itself, else - the imagery
    moved, the file passed to another machine - by file name as Model
    Review matches a record (source.match_candidates), among several of
    one name by the longest shared folder tail, none when that does not
    tell them apart."""
    def spelled(path) -> str:
        return str(path).replace("\\", "/").lower()
    exact = {spelled(path): path for path in paths}
    by_name: dict = {}
    for path in exact.values():
        by_name.setdefault(image_key(path), []).append(path)
    known: dict = {}            # each spelling worked out once

    def named(written):
        if written not in known:
            found = exact.get(spelled(written))
            if found is None:
                same = by_name.get(image_key(written), [])
                if len(same) > 1:
                    shared = [_tail_overlap(path, written) for path in same]
                    best = max(shared)
                    same = [path for path, n in zip(same, shared)
                            if best and n == best]
                found = same[0] if len(same) == 1 else None
            known[written] = found
        return known[written]
    return named

