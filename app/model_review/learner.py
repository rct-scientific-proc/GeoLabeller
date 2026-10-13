"""A small classifier learned from the verdicts, to sort what is left.

Asked for 2026-10-13: once a few chips have verdicts, train on them and
have the model suggest a class and a confidence for every chip still to
look at, so the grid can be ordered by what the user is likeliest to
agree with, and the hopeless chips dealt with in a stroke.

What it learns from: the verdicts given in the window, the project's
memory of earlier rounds (Ignore included), and the project's own labels
of those classes, up to a cap per class. Ignore is a class like any
other. A fifth of each class is held out and the accuracy reported is on
that slice - the only honest number. The net is small (four conv blocks,
some 80k weights) and sees every chip at 64 px, flipped and rotated at
random while training, so a few hundred examples are enough to be useful
on a CPU in well under a minute.

PyTorch is imported by name, through importlib, only when asked for: the
installed build leaves it out (build/setup.py excludes it) and the import
gate (build/check_frozen_imports.py) reads import statements. Training
is for a source run with requirements-ml.txt installed. Everything up to
the tensors - gathering, reading, resizing - is torch-free, and so can be
tested without it.
"""
from __future__ import annotations

import importlib
import math
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from ..snippets import read_label_snippet
from .ledger import IGNORE

INPUT_PX = 64                   # every chip is seen at this size
MIN_CLASSES = 2
MIN_PER_CLASS = 5
HOLD_OUT = 0.2                  # of each class, for the accuracy reported
HOLD_OUT_FROM = 30              # examples; fewer, and nothing is held out
SELECT_FROM = 10                # held-out chips; fewer, and they pick no model
LABELS_PER_CLASS = 500          # the project's own labels, at most
DEFAULT_EPOCHS = 20
MIN_STEPS = 300                 # optimiser steps at least, however few chips
PATIENCE_STEPS = 120            # then: steps without a better held-out loss
BATCH = 64
INFER_BATCH = 256
REPORT_EVERY = 200              # chips read between progress reports


class Cancelled(Exception):
    """Training or suggesting was stopped."""


def name_of(class_name: str) -> str:
    return "Ignore" if class_name == IGNORE else class_name


_checked: "tuple | None" = None


def available() -> tuple:
    """(device, text): the device training would run on - "cuda" or
    "cpu" - or None with the reason it cannot. Found out once: the
    import is seconds, and a failed one fails again."""
    global _checked
    if _checked is None:
        _checked = _availability()
    return _checked


def _availability() -> tuple:
    if getattr(sys, "frozen", False):
        return None, ("Training needs a source run: the installed GeoLabeller "
                      "leaves PyTorch out. See requirements-ml.txt.")
    try:
        torch = importlib.import_module("torch")
    except ImportError:
        return None, ("PyTorch is not installed: pip install -r "
                      "requirements-ml.txt (the README has the GPU build).")
    except OSError as exc:
        # Its DLLs would not load: on Windows, almost always the
        # Microsoft C++ runtime being older than PyTorch was built with.
        return None, (f"PyTorch is installed but could not be loaded "
                      f"({exc}). On Windows, install the latest Microsoft "
                      "Visual C++ Redistributable.")
    if torch.cuda.is_available():
        return "cuda", (f"PyTorch {torch.__version__} on "
                        f"{torch.cuda.get_device_name(0)}")
    return "cpu", f"PyTorch {torch.__version__} on the CPU"


# -- what there is to learn from ---------------------------------------------

@dataclass(frozen=True)
class Example:
    image_path: str
    pixel_x: float
    pixel_y: float
    class_name: str             # a class, or IGNORE
    origin: str                 # "verdict", "memory" or "label"


def gather_examples(project, chips, labels_per_class: int = LABELS_PER_CLASS,
                    seed: int = 0) -> list:
    """Everything there is to learn from, one example per place and
    class: the chips given a verdict here, the memory's chips (an ignored
    one at the file's centre; a labelled one through its label, where
    the user put it), and the project's labels - of the classes seen in
    verdicts, and of every class that is not one the file's model
    predicts (its chips are never that: the labels of a predicted class
    would teach a verdict no chip can take) - at most
    ``labels_per_class`` of each, drawn at random."""
    examples: dict = {}

    def add(path, x, y, class_name, origin):
        key = (path, int(round(x)), int(round(y)), class_name)
        if key not in examples:
            examples[key] = Example(path, float(x), float(y), class_name,
                                    origin)

    for chip in chips:
        if chip.verdict is not None and not chip.reviewed_before():
            add(chip.image_path, chip.pixel_x, chip.pixel_y, chip.verdict,
                "verdict")
    images = getattr(project, "images", {}) or {}
    by_name: dict = {}
    for path in images:
        by_name.setdefault(Path(path).name, []).append(path)
    classes = {example.class_name for example in examples.values()}
    for verdict in getattr(project, "model_review", []) or []:
        if not verdict.became:
            continue
        classes.add(verdict.became)
        paths = by_name.get(verdict.image, [])
        if len(paths) == 1 and (verdict.became == IGNORE
                                or verdict.label_id is None):
            add(paths[0], verdict.pixel_x, verdict.pixel_y, verdict.became,
                "memory")
    classes.discard(IGNORE)
    predicted = {chip.predicted for chip in chips}
    pool: dict = {}
    for path, image in images.items():
        for label in getattr(image, "labels", []) or []:
            if label.class_name in classes \
                    or label.class_name not in predicted:
                pool.setdefault(label.class_name, []).append(
                    (path, label.pixel_x, label.pixel_y))
    rng = random.Random(seed)
    for class_name in sorted(pool):
        places = pool[class_name]
        if len(places) > labels_per_class:
            places = rng.sample(places, labels_per_class)
        for path, x, y in places:
            add(path, x, y, class_name, "label")
    return list(examples.values())


def readiness(examples) -> "str | None":
    """Why training cannot start yet, or None when it can."""
    counts = Counter(example.class_name for example in examples)
    enough = [c for c, n in counts.items() if n >= MIN_PER_CLASS]
    if len(enough) >= MIN_CLASSES:
        return None
    sofar = ", ".join(f"{n} {name_of(c)}" for c, n in counts.most_common())
    return (f"Not enough to learn from yet: {MIN_CLASSES} classes with at "
            f"least {MIN_PER_CLASS} chips each are needed, Ignore counting "
            f"as a class. So far: {sofar or 'nothing'}.")


# -- pixels ------------------------------------------------------------------

def chip_pixels(image_path: str, x: float, y: float, chip_size: int,
                cache: "dict | None" = None) -> "np.ndarray | None":
    """The chip as the model sees it: (INPUT_PX, INPUT_PX, 3) uint8, the
    grid's own read scaled down. None when it cannot be read."""
    key = (image_path, int(round(x)), int(round(y)), int(chip_size))
    if cache is not None and key in cache:
        return cache[key]
    arr = read_label_snippet(image_path, x, y, chip_size)
    if arr is not None and arr.shape[:2] != (INPUT_PX, INPUT_PX):
        arr = np.asarray(Image.fromarray(arr).resize((INPUT_PX, INPUT_PX),
                                                     Image.BOX))
    if cache is not None:
        cache[key] = arr
    return arr


# -- what came of it ---------------------------------------------------------

@dataclass
class Report:
    classes: list
    counts: Counter                 # training examples per class
    left_out: Counter               # classes with too few to learn
    held_out: int
    accuracy: float                 # on the held-out slice; nan if none
    per_class: dict                 # class -> (right, total) held out
    confusion: Counter              # (true, predicted) -> n
    epochs: int
    seconds: float
    device: str
    suggested: int = 0              # chips given a suggestion after

    def summary(self) -> str:
        trained = ", ".join(f"{n} {name_of(c)}" for c, n in sorted(
            self.counts.items(), key=lambda item: -item[1]))
        text = (f"Trained on {sum(self.counts.values())} chips ({trained}) "
                f"in {self.seconds:.0f} s on {self.device}, "
                f"{self.epochs} pass{'' if self.epochs == 1 else 'es'}")
        if self.held_out:
            per_class = ", ".join(f"{name_of(c)} {r}/{t}"
                                  for c, (r, t) in self.per_class.items()
                                  if t)
            text += (f"; held-out accuracy {self.accuracy:.0%} on "
                     f"{self.held_out} ({per_class})")
        else:
            text += f"; nothing held out (fewer than {HOLD_OUT_FROM} chips)"
        if self.left_out:
            text += "; too few to learn: " + ", ".join(
                f"{n} {name_of(c)}" for c, n in self.left_out.items())
        if self.suggested:
            text += f"; {self.suggested} chips given a suggestion"
        return text + "."


@dataclass
class Suggestion:
    class_name: str
    confidence: float
    probabilities: dict = field(default_factory=dict)   # class -> p

    def describe(self) -> str:
        """"rock 0.91 (Ignore 0.07, shadow 0.02)"."""
        text = f"{name_of(self.class_name)} {self.confidence:.2f}"
        rest = sorted(((c, p) for c, p in self.probabilities.items()
                       if c != self.class_name), key=lambda item: -item[1])
        if rest:
            text += " (" + ", ".join(f"{name_of(c)} {p:.2f}"
                                      for c, p in rest) + ")"
        return text


# -- the learner -------------------------------------------------------------

def _build(torch, n_classes: int):
    nn = torch.nn

    def block(i, o):
        return nn.Sequential(nn.Conv2d(i, o, 3, padding=1),
                             nn.BatchNorm2d(o), nn.ReLU(inplace=True),
                             nn.MaxPool2d(2))

    return nn.Sequential(block(3, 16), block(16, 32), block(32, 64),
                         block(64, 96), nn.AdaptiveAvgPool2d(1),
                         nn.Flatten(), nn.Dropout(0.3),
                         nn.Linear(96, n_classes))


def _augment(torch, batch, generator):
    """Each chip turned a random quarter, maybe mirrored, its contrast
    nudged: clutter has no up."""
    n = len(batch)
    turns = torch.randint(0, 4, (n,), generator=generator)
    mirrors = torch.rand(n, generator=generator) < 0.5
    gains = 1.0 + (torch.rand(n, generator=generator) - 0.5) * 0.3
    out = torch.empty_like(batch)
    for i in range(n):
        chip = torch.rot90(batch[i], int(turns[i]), dims=(1, 2))
        if bool(mirrors[i]):
            chip = torch.flip(chip, dims=(2,))
        out[i] = chip * gains[i]
    return out


def _logits(torch, model, x, device):
    model.eval()
    pieces = []
    with torch.no_grad():
        for start in range(0, len(x), INFER_BATCH):
            pieces.append(model(x[start:start + INFER_BATCH].to(device)).cpu())
    return torch.cat(pieces) if pieces else torch.empty((0, 1))


class Learner:
    """Trains on examples, then suggests for places. One per window;
    the pixels it reads are kept, so training again reads nothing twice."""

    def __init__(self, chip_size: int, device: "str | None" = None,
                 seed: int = 0):
        self.chip_size = int(chip_size)
        self.device = device
        self.seed = seed
        self.report: "Report | None" = None
        self._cache: dict = {}
        self._model = None
        self._classes: list = []
        self._mean = self._std = None
        self._torch_device = None

    def trained(self) -> bool:
        return self._model is not None

    def classes(self) -> list:
        return list(self._classes)

    def read(self, places, progress=None, should_stop=None) -> list:
        """Pixels for (path, x, y) places, None where unreadable."""
        progress = progress or (lambda text: None)
        should_stop = should_stop or (lambda: False)
        pixels = []
        for i, (path, x, y) in enumerate(places):
            if should_stop():
                raise Cancelled()
            pixels.append(chip_pixels(path, x, y, self.chip_size,
                                      self._cache))
            if (i + 1) % REPORT_EVERY == 0:
                progress(f"Read {i + 1} of {len(places)} chips...")
        return pixels

    def train(self, examples, progress=None, should_stop=None,
              epochs: int = DEFAULT_EPOCHS) -> Report:
        """Train on the examples; the report says what came of it.
        Raises ValueError when there is too little to learn from and
        Cancelled when stopped."""
        torch = importlib.import_module("torch")
        progress = progress or (lambda text: None)
        should_stop = should_stop or (lambda: False)
        started = time.time()
        counts = Counter(example.class_name for example in examples)
        classes = sorted(c for c, n in counts.items() if n >= MIN_PER_CLASS)
        left_out = Counter({c: n for c, n in counts.items()
                            if n < MIN_PER_CLASS})
        if len(classes) < MIN_CLASSES:
            raise ValueError(readiness(examples))
        used = [e for e in examples if e.class_name in classes]
        progress(f"Reading {len(used)} chips...")
        pixels = self.read([(e.image_path, e.pixel_x, e.pixel_y)
                            for e in used], progress, should_stop)
        kept = [i for i, p in enumerate(pixels) if p is not None]
        X = np.stack([pixels[i] for i in kept]).astype(np.float32) / 255.0
        y = np.array([classes.index(used[i].class_name) for i in kept])
        rng = np.random.default_rng(self.seed)
        train_idx: list = []
        val_idx: list = []
        # A tiny set is trained on whole: two chips held out say
        # nothing, and cost a fifth of what there is to learn from.
        hold_out = len(kept) >= HOLD_OUT_FROM
        for c in range(len(classes)):
            idx = np.flatnonzero(y == c)
            rng.shuffle(idx)
            n_val = (int(round(HOLD_OUT * len(idx)))
                     if hold_out and len(idx) >= MIN_PER_CLASS else 0)
            val_idx += idx[:n_val].tolist()
            train_idx += idx[n_val:].tolist()
        mean = X[train_idx].mean(axis=(0, 1, 2))
        std = X[train_idx].std(axis=(0, 1, 2)) + 1e-3
        self._mean, self._std = mean, std
        device = torch.device(self.device or (
            "cuda" if torch.cuda.is_available() else "cpu"))
        self._torch_device = device
        torch.manual_seed(self.seed)
        xt = torch.from_numpy(((X - mean) / std).transpose(0, 3, 1, 2).copy())
        yt = torch.from_numpy(y).long()
        model = _build(torch, len(classes)).to(device)
        shares = np.bincount(y[train_idx], minlength=len(classes))
        weights = torch.tensor(len(train_idx) / (len(classes)
                                                 * np.maximum(shares, 1)),
                               dtype=torch.float32, device=device)
        loss_fn = torch.nn.CrossEntropyLoss(weight=weights)
        optimiser = torch.optim.Adam(model.parameters(), lr=1e-3,
                                     weight_decay=1e-4)
        generator = torch.Generator().manual_seed(self.seed)
        train_t = torch.tensor(train_idx, dtype=torch.long)
        val_t = torch.tensor(val_idx, dtype=torch.long)
        # By steps: a pass over forty chips is one step, and twelve of
        # them taught nothing (2026-10-13). Enough passes for MIN_STEPS,
        # then patience in steps too.
        per_pass = max(1, math.ceil(len(train_idx) / BATCH))
        passes = max(1, int(epochs), math.ceil(MIN_STEPS / per_pass))
        say_every = max(1, passes // 20)
        # A small held-out slice reports but does not choose: picking the
        # pass with the best loss on two chips kept the untrained first
        # pass over three hundred steps of learning.
        select = len(val_idx) >= SELECT_FROM
        best_loss, best_state, best_epoch, best_step = math.inf, None, 0, 0
        step = ran = 0
        for epoch in range(passes):
            if should_stop():
                raise Cancelled()
            model.train()
            order = train_t[torch.randperm(len(train_t), generator=generator)]
            total = 0.0
            for start in range(0, len(order), BATCH):
                batch = order[start:start + BATCH]
                xb = _augment(torch, xt[batch], generator).to(device)
                yb = yt[batch].to(device)
                optimiser.zero_grad()
                loss = loss_fn(model(xb), yb)
                loss.backward()
                optimiser.step()
                total += loss.item() * len(batch)
                step += 1
            ran = epoch + 1
            train_loss = total / max(1, len(order))
            if len(val_idx):
                logits = _logits(torch, model, xt[val_t], device)
                val_loss = float(loss_fn(logits.to(device),
                                         yt[val_t].to(device)))
                val_acc = float((logits.argmax(1) == yt[val_t]).float().mean())
                if ran % say_every == 0 or ran == passes:
                    progress(f"Pass {ran} of {passes}: loss {train_loss:.3f}, "
                             f"held-out accuracy {val_acc:.0%}")
                if not select:
                    best_epoch = ran
                elif val_loss < best_loss - 1e-4:
                    best_loss, best_epoch, best_step = val_loss, ran, step
                    best_state = {k: v.detach().clone()
                                  for k, v in model.state_dict().items()}
                elif step >= MIN_STEPS and step - best_step >= PATIENCE_STEPS:
                    break
            else:
                if ran % say_every == 0 or ran == passes:
                    progress(f"Pass {ran} of {passes}: loss {train_loss:.3f}")
                best_epoch = ran
        if select and best_state is not None:
            model.load_state_dict(best_state)
        model.eval()
        self._model, self._classes = model, classes
        per_class = {c: [0, 0] for c in classes}
        confusion: Counter = Counter()
        right = 0
        if len(val_idx):
            predicted = _logits(torch, model, xt[val_t], device).argmax(1).numpy()
            for truth, guess in zip(y[val_idx], predicted):
                confusion[(classes[truth], classes[guess])] += 1
                per_class[classes[truth]][1] += 1
                if truth == guess:
                    per_class[classes[truth]][0] += 1
                    right += 1
        accuracy = right / len(val_idx) if len(val_idx) else math.nan
        self.report = Report(
            classes=classes,
            counts=Counter(classes[c] for c in y[train_idx]),
            left_out=left_out, held_out=len(val_idx), accuracy=accuracy,
            per_class={c: tuple(v) for c, v in per_class.items()},
            confusion=confusion, epochs=best_epoch,
            seconds=time.time() - started, device=str(device))
        return self.report

    def suggest(self, places, progress=None, should_stop=None) -> list:
        """A Suggestion per (path, x, y) place, None where the chip could
        not be read."""
        torch = importlib.import_module("torch")
        if self._model is None:
            raise RuntimeError("Train first.")
        pixels = self.read(places, progress, should_stop)
        results: list = [None] * len(places)
        kept = [i for i, p in enumerate(pixels) if p is not None]
        if not kept:
            return results
        X = np.stack([pixels[i] for i in kept]).astype(np.float32) / 255.0
        xt = torch.from_numpy(((X - self._mean) / self._std)
                              .transpose(0, 3, 1, 2).copy())
        probabilities = torch.softmax(
            _logits(torch, self._model, xt, self._torch_device), dim=1).numpy()
        for row, i in enumerate(kept):
            p = probabilities[row]
            best = int(p.argmax())
            results[i] = Suggestion(
                self._classes[best], float(p[best]),
                {c: float(p[j]) for j, c in enumerate(self._classes)})
        if self.report is not None:
            self.report.suggested = len(kept)
        return results
