"""Which labels a confidence model learns from, which it rates, and the
training of a model per class.

A label's confidence is 1-5 from a person, or unset. Per class, a model
learns from the labels people rated and suggests a rating for those nobody
has: a class of its own each, because what makes a labeller sure of a
vessel is not what makes them sure of a buoy.

What is learned from: ratings a person gave - typed in the Snippet Editor,
or corrected in the suggestions window. NOT ratings accepted in bulk from
an earlier round of suggestions (they carry the SUGGESTED_KEY mark): the
model would be trained on its own guesses, and grow surer without growing
righter. Rejected labels (the Review mark) are neither learned from nor
rated.

The rules (Ryan, 2026-11-24): a class needs MIN_RATED rated labels; a
rating is learned only from MIN_PER_RATING examples of it; and there must
be two ratings to tell apart. Every view is rated from its own snippet.

The models are model_review.learner's: its Learner classifies chips by a
"class name", which here is the rating as text. PyTorch is touched only
there. Progress is (text, done, total).
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from ..labels import (CONFIDENCE_MAX, CONFIDENCE_MIN, CONFIDENCE_UNSET,
                      REVIEW_REJECTED, valid_confidence)
from ..model_review import learner as _learner
from ..model_review.learner import Cancelled, Example, Learner

MIN_RATED = 30                      # rated labels a class needs
MIN_PER_RATING = _learner.MIN_PER_CLASS     # examples a rating needs (5)
MIN_RATINGS = _learner.MIN_CLASSES          # ratings to tell apart (2)
CHIP_PX = 224                       # source pixels a snippet is read at
# Bounds on what is held at once (2.11.1): at 20,000 unrated labels one
# suggest() stacked every snippet as floats - some 2 GB - and a class
# with thousands of rated labels trained on all of them. A rating's
# examples are capped (taken evenly through them), and suggestions are
# made a batch at a time, their pixels let go after (the store has them).
EXAMPLES_PER_RATING = 500
SUGGEST_BATCH = 1000
# On a label's attribution: its rating was accepted from a suggestion,
# not given by hand. Read by examples() alone; nothing exports it.
SUGGESTED_KEY = "confidence_suggested"

__all__ = ["Cancelled", "CHIP_PX"]


@dataclass(frozen=True)
class View:
    """One label, as much of it as rating it needs."""
    label_id: int
    image_path: str
    pixel_x: float
    pixel_y: float
    class_name: str


@dataclass
class Suggestion:
    rating: int
    certainty: float                    # the model's probability of it
    probabilities: dict = field(default_factory=dict)   # rating -> p

    def describe(self) -> str:
        """"4 of 5, 0.62 sure (3: 0.30, 5: 0.08)"."""
        rest = sorted(((r, p) for r, p in self.probabilities.items()
                       if r != self.rating), key=lambda item: -item[1])
        text = f"{self.rating} of {CONFIDENCE_MAX}, {self.certainty:.2f} sure"
        if rest:
            text += " (" + ", ".join(f"{r}: {p:.2f}" for r, p in rest) + ")"
        return text


@dataclass
class ClassPlan:
    """Whether a class can be learned, and from what."""
    class_name: str
    counts: Counter                     # rating -> rated labels
    unrated: int = 0

    @property
    def rated(self) -> int:
        return sum(self.counts.values())

    @property
    def learnable(self) -> list:
        """The ratings with examples enough, lowest first."""
        return sorted(r for r, n in self.counts.items()
                      if n >= MIN_PER_RATING)

    @property
    def left_out(self) -> dict:
        """rating -> how many examples it has, for those too few."""
        return {r: n for r, n in sorted(self.counts.items())
                if n < MIN_PER_RATING}

    def reason(self) -> "str | None":
        """Why the class is not learned, or None when it is."""
        if self.rated < MIN_RATED:
            return (f"{self.rated} rated label"
                    f"{'' if self.rated == 1 else 's'}; {MIN_RATED} needed")
        if len(self.learnable) < MIN_RATINGS:
            have = ", ".join(f"{n} rated {r}"
                             for r, n in sorted(self.counts.items()))
            return (f"needs {MIN_RATINGS} ratings with {MIN_PER_RATING} "
                    f"examples each ({have})")
        return None


def _wanted(label) -> bool:
    return label.review != REVIEW_REJECTED


def _view(image, label) -> View:
    return View(label.id, image.path, label.pixel_x, label.pixel_y,
                label.class_name)


def examples(project, corrections: "dict | None" = None) -> dict:
    """class -> [(View, rating)]: what there is to learn from. A
    correction made in the window (label id -> rating) counts as a
    rating given by hand, whatever the label holds."""
    corrections = corrections or {}
    found: dict = {}
    for image in project.images.values():
        for label in image.labels:
            if not _wanted(label):
                continue
            rating = corrections.get(label.id)
            if rating is None:
                rating = valid_confidence(label.confidence)
                if (rating == CONFIDENCE_UNSET
                        or SUGGESTED_KEY in label.attribution):
                    continue
            found.setdefault(label.class_name, []).append(
                (_view(image, label), int(rating)))
    return found


def unrated(project, corrections: "dict | None" = None) -> dict:
    """class -> [View]: the labels nobody has rated, to suggest for."""
    corrections = corrections or {}
    found: dict = {}
    for image in project.images.values():
        for label in image.labels:
            if (_wanted(label) and label.id not in corrections
                    and valid_confidence(label.confidence)
                    == CONFIDENCE_UNSET):
                found.setdefault(label.class_name, []).append(
                    _view(image, label))
    return found


def plans(rated: dict, waiting: dict) -> dict:
    """class -> ClassPlan, for every class with a rated or an unrated
    label, by name."""
    return {name: ClassPlan(name,
                            Counter(r for _v, r in rated.get(name, ())),
                            len(waiting.get(name, ())))
            for name in sorted(set(rated) | set(waiting))}


def readiness(class_plans: dict) -> "str | None":
    """Why nothing can be trained yet, or None when a class can."""
    if any(plan.reason() is None for plan in class_plans.values()):
        return None
    if not class_plans:
        return "There are no labels to learn from."
    return ("No class can be learned yet - a class needs "
            f"{MIN_RATED} rated labels, and {MIN_RATINGS} ratings with "
            f"{MIN_PER_RATING} examples each. "
            + "; ".join(f"{name}: {plan.reason()}"
                        for name, plan in class_plans.items()) + ".")


@dataclass
class ClassResult:
    """What came of training one class."""
    plan: ClassPlan
    report: object = None               # learner.Report, or None
    failed: str = ""                    # why it could not be trained
    suggested: int = 0

    def within_one(self) -> float:
        """Of the held-out labels, the share rated within one of what
        the person gave; nan when nothing was held out."""
        report = self.report
        if report is None or not report.held_out:
            return math.nan
        near = sum(n for (true, said), n in report.confusion.items()
                   if abs(int(true) - int(said)) <= 1)
        return near / report.held_out


@dataclass
class Outcome:
    results: dict = field(default_factory=dict)         # class -> ClassResult
    suggestions: dict = field(default_factory=dict)     # label id -> Suggestion

    def summary(self) -> str:
        trained = [r for r in self.results.values() if r.report is not None]
        if not trained:
            return "No class was trained."
        n = len(self.suggestions)
        return (f"Trained {len(trained)} class"
                f"{'' if len(trained) == 1 else 'es'}; {n} label"
                f"{'' if n == 1 else 's'} given a suggested rating.")


class Trainer:
    """A model per class, kept between trainings so a second one reads
    no snippet twice."""

    def __init__(self, backbone: str = _learner.SMALL, device=None,
                 store=None, chip_px: int = CHIP_PX):
        self.backbone = backbone
        self.device = device
        self.store = store
        self.chip_px = int(chip_px)
        self._learners: dict = {}       # class -> Learner

    def _learner_for(self, class_name: str) -> Learner:
        if class_name not in self._learners:
            self._learners[class_name] = Learner(
                self.chip_px, device=self.device, backbone=self.backbone,
                store=self.store)
        return self._learners[class_name]

    def run(self, prepared: "Prepared", progress=None,
            should_stop=None) -> Outcome:
        """Train every class that can be, and suggest for its unrated
        labels. Raises Cancelled when stopped; a class that fails on its
        own (its snippets unreadable) is reported and the rest go on.
        Touches nothing of the project: ``prepared`` was gathered on the
        GUI thread, this runs off it."""
        progress = progress or (lambda text, done, total: None)
        should_stop = should_stop or (lambda: False)
        outcome = Outcome()
        for name, plan in prepared.plans.items():
            result = outcome.results[name] = ClassResult(plan)
            if plan.reason() is not None:
                continue

            def said(text, done, total, _name=name):
                progress(f"{_name}: {text}", done, total)

            learner = self._learner_for(name)
            try:
                result.report = learner.train(
                    [Example(view.image_path, view.pixel_x, view.pixel_y,
                             str(rating), "label")
                     for view, rating in capped(prepared.rated[name])],
                    said, should_stop)
            except ValueError as exc:   # too few of its snippets readable
                result.failed = str(exc)
                continue
            views = prepared.waiting.get(name, [])
            for start in range(0, len(views), SUGGEST_BATCH):
                if should_stop():
                    raise Cancelled()
                batch = views[start:start + SUGGEST_BATCH]
                places = [(v.image_path, v.pixel_x, v.pixel_y) for v in batch]
                suggested = learner.suggest(places, said, should_stop)
                for view, suggestion in zip(batch, suggested):
                    if suggestion is None:
                        continue
                    outcome.suggestions[view.label_id] = Suggestion(
                        int(suggestion.class_name), suggestion.confidence,
                        {int(r): p
                         for r, p in suggestion.probabilities.items()})
                    result.suggested += 1
                # Their pixels are not wanted again this session.
                pixels = getattr(learner, "_pixels", None)
                if pixels is not None:
                    for place in places:
                        pixels.pop(learner._key(*place), None)
            if result.report is not None:
                # The learner counts only its last batch.
                result.report.suggested = result.suggested
        return outcome


def capped(rated: list, per_rating: "int | None" = None) -> list:
    """``rated`` - [(View, rating)] - with at most ``per_rating`` examples
    of a rating, taken evenly through them (so no image or stretch of the
    survey is all that is learned from)."""
    limit = EXAMPLES_PER_RATING if per_rating is None else per_rating
    by_rating: dict = {}
    for item in rated:
        by_rating.setdefault(item[1], []).append(item)
    kept = []
    for rating in sorted(by_rating):
        items = by_rating[rating]
        if len(items) > limit:
            step = len(items) / limit
            items = [items[int(i * step)] for i in range(limit)]
        kept.extend(items)
    return kept


@dataclass
class Prepared:
    """What a training runs on, gathered from the project at one moment."""
    rated: dict                         # class -> [(View, rating)]
    waiting: dict                       # class -> [View]
    plans: dict                         # class -> ClassPlan

    def views(self) -> dict:
        """label id -> View, of every label waiting for a rating."""
        return {view.label_id: view
                for views in self.waiting.values() for view in views}

    def n_examples(self) -> int:
        return sum(plan.rated for plan in self.plans.values()
                   if plan.reason() is None)


def prepare(project, corrections: "dict | None" = None) -> Prepared:
    """Gather what a training needs. Raises ValueError, with the reason,
    when no class can be learned yet."""
    rated = examples(project, corrections)
    waiting = unrated(project, corrections)
    class_plans = plans(rated, waiting)
    why = readiness(class_plans)
    if why is not None:
        raise ValueError(why)
    return Prepared(rated, waiting, class_plans)


def still_open(project, view: View) -> bool:
    """Whether a suggestion made for ``view`` still stands: its label is
    there, the same class at the same place, and nobody has rated it."""
    _image, label = project.get_label_by_id(view.label_id)
    return (label is not None and label.class_name == view.class_name
            and valid_confidence(label.confidence) == CONFIDENCE_UNSET
            and label.review != REVIEW_REJECTED
            and int(round(label.pixel_x)) == int(round(view.pixel_x))
            and int(round(label.pixel_y)) == int(round(view.pixel_y)))


def valid_rating(value) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)
            and CONFIDENCE_MIN <= value <= CONFIDENCE_MAX)
