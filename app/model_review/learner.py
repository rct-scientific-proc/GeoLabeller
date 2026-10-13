"""A small classifier learned from the verdicts, to sort what is left.

Asked for 2026-10-13: once a few chips have verdicts, train on them and
have the model suggest a class and a confidence for every chip still to
look at, so the grid can be ordered by what the user is likeliest to
agree with, and the hopeless chips dealt with in a stroke.

What it learns from: the verdicts given in the window, the project's
memory of earlier rounds (Ignore included), and the project's own labels
- of the verdict classes, and of every class the file's model does not
predict - up to a cap per class. Ignore is a class like any other. From
thirty examples a fifth of each class is held out and the accuracy
reported is on that slice; from ten held out they also choose the model.

Two backbones. The small net (four conv blocks, some 80k weights) is
trained from scratch and sees every chip at INPUT_PX, flipped and turned
at random while training. ResNet18 (torchvision, ImageNet weights) is
frozen: each chip's 512 features are taken once - eight turned and
mirrored views of each training chip - and a linear head is trained on
them, which is what a few labels can support. Both train by steps, not
passes: a pass over forty chips is one step.

Chips come through the snippet store (snippet_store.py): read once from
the imagery with the file opened once, saved small, read from there
after. PyTorch and torchvision are imported by name, through importlib,
only when asked for: the installed build leaves them out (build/setup.py
excludes torch) and the import gate reads import statements. Training is
for a source run with requirements-ml.txt installed. Everything up to the
tensors is torch-free. Progress is reported as (text, done, total).
"""
from __future__ import annotations

import importlib
import math
import random
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .ledger import IGNORE, image_key
from .snippet_store import INPUT_PX, Cancelled, SnippetStore

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
FEATURE_BATCH = 64
SMALL, RESNET18 = "small", "resnet18"
BACKBONES = ((SMALL, "Small net, from scratch"),
             (RESNET18, "ResNet18, pretrained"))
VIEWS = 8                       # turned and mirrored views of a training chip
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)

__all__ = ["Cancelled", "INPUT_PX"]


class WeightsMissing(Exception):
    """ResNet18's weights are not here and could not be fetched."""


def name_of(class_name: str) -> str:
    return "Ignore" if class_name == IGNORE else class_name


_checked: "tuple | None" = None
_check_lock = threading.Lock()


def checked() -> "tuple | None":
    """What available() found, if it has run; None before."""
    return _checked


def available() -> tuple:
    """(device, text): the device training would run on - "cuda" or
    "cpu" - or None with the reason it cannot. Found out once: the
    import is seconds, and a failed one fails again."""
    global _checked
    if _checked is None:
        with _check_lock:               # one import, whoever asks first
            if _checked is None:
                _checked = _availability()
    return _checked


def probe_in_background() -> "threading.Thread | None":
    """Begin importing PyTorch on a thread of its own, so the first
    Model Review window opens at once instead of waiting on the import
    - a second here, several with CUDA, more on a cold disk (asked for
    2026-10-13). None when there is nothing to import: the installed
    build, or no PyTorch."""
    if getattr(sys, "frozen", False) \
            or importlib.util.find_spec("torch") is None:
        return None
    thread = threading.Thread(target=available, name="pytorch-probe",
                              daemon=True)
    thread.start()
    return thread


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
        text = f"PyTorch {torch.__version__} on {torch.cuda.get_device_name(0)}"
        device = "cuda"
    else:
        text, device = f"PyTorch {torch.__version__} on the CPU", "cpu"
    why = backbone_reason(RESNET18)
    return device, text + ("" if why is None else f"; {why}")


def backbone_reason(backbone: str) -> "str | None":
    """Why this backbone cannot be used, or None."""
    if backbone == RESNET18 and importlib.util.find_spec("torchvision") is None:
        return "ResNet18 needs torchvision (requirements-ml.txt)"
    return None


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
        by_name.setdefault(image_key(path), []).append(path)
    classes = {example.class_name for example in examples.values()}
    for verdict in getattr(project, "model_review", []) or []:
        if not verdict.became:
            continue
        classes.add(verdict.became)
        paths = by_name.get(image_key(verdict.image), [])
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
    epochs: int                     # the pass the model is from
    seconds: float
    device: str
    backbone: str = SMALL
    steps: int = 0
    history: list = field(default_factory=list)   # a dict per pass
    suggested: int = 0              # chips given a suggestion after

    def precision_recall(self) -> dict:
        """class -> (precision, recall) on the held-out slice, nan where
        undefined."""
        out = {}
        for c in self.classes:
            true_c = sum(n for (t, _p), n in self.confusion.items() if t == c)
            said_c = sum(n for (_t, p), n in self.confusion.items() if p == c)
            right = self.confusion.get((c, c), 0)
            out[c] = (right / said_c if said_c else math.nan,
                      right / true_c if true_c else math.nan)
        return out

    def summary(self) -> str:
        trained = ", ".join(f"{n} {name_of(c)}" for c, n in sorted(
            self.counts.items(), key=lambda item: -item[1]))
        which = dict(BACKBONES).get(self.backbone, self.backbone)
        text = (f"Trained {which.split(',')[0]} on "
                f"{sum(self.counts.values())} chips ({trained}) in "
                f"{self.seconds:.0f} s on {self.device}, {self.steps} steps")
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


# -- the nets ----------------------------------------------------------------

def _small_net(torch, n_classes: int):
    nn = torch.nn

    def block(i, o):
        return nn.Sequential(nn.Conv2d(i, o, 3, padding=1),
                             nn.BatchNorm2d(o), nn.ReLU(inplace=True),
                             nn.MaxPool2d(2))

    return nn.Sequential(block(3, 16), block(16, 32), block(32, 64),
                         block(64, 96), nn.AdaptiveAvgPool2d(1),
                         nn.Flatten(), nn.Dropout(0.3),
                         nn.Linear(96, n_classes))


def _resnet18(torch):
    """ImageNet's ResNet18 without its last layer, frozen; raises
    WeightsMissing when the weights are not here and cannot be fetched."""
    torchvision = importlib.import_module("torchvision")
    weights = torchvision.models.ResNet18_Weights.DEFAULT
    try:
        net = torchvision.models.resnet18(weights=weights)
    except Exception as exc:  # noqa: BLE001 - offline, most likely
        where = Path(torch.hub.get_dir()) / "checkpoints"
        raise WeightsMissing(
            f"ResNet18's weights are not here and could not be fetched "
            f"({type(exc).__name__}). Download {weights.url} and put it in "
            f"{where}.") from exc
    net.fc = torch.nn.Identity()
    net.eval()
    for parameter in net.parameters():
        parameter.requires_grad_(False)
    return net


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


def _each_view(torch, batch):
    """The VIEWS dihedral views of the chips, one (N, 3, H, W) at a
    time - all eight at once was a gigabyte for a few thousand chips."""
    made = 0
    for turn in range(4):
        turned = torch.rot90(batch, turn, dims=(2, 3))
        for view in (turned, torch.flip(turned, dims=(3,))):
            if made < VIEWS:
                made += 1
                yield view


def _logits(torch, model, x, device):
    model.eval()
    pieces = []
    with torch.no_grad():
        for start in range(0, len(x), INFER_BATCH):
            pieces.append(model(x[start:start + INFER_BATCH].to(device)).cpu())
    return torch.cat(pieces) if pieces else torch.empty((0, 1))


# -- the learner -------------------------------------------------------------

class Learner:
    """Trains on examples, then suggests for places. One per window; the
    pixels it reads are kept for the session, and in the store for
    good, so training again reads nothing twice."""

    def __init__(self, chip_size: int, device: "str | None" = None,
                 seed: int = 0, backbone: str = SMALL,
                 store: "SnippetStore | None" = None):
        self.chip_size = int(chip_size)
        self.device = device
        self.seed = seed
        self.backbone = backbone
        self.store = store if store is not None else SnippetStore()
        self.report: "Report | None" = None
        self._pixels: dict = {}         # (path, rx, ry) -> (INPUT_PX, INPUT_PX, 3)
        self._model = None              # the small net, or the head
        self._features_net = None       # ResNet18, frozen
        self._classes: list = []
        self._mean = self._std = None
        self._torch_device = None

    def trained(self) -> bool:
        return self._model is not None

    def classes(self) -> list:
        return list(self._classes)

    @staticmethod
    def _key(path, x, y):
        return (path, int(round(x)), int(round(y)))

    def read(self, places, progress=None, should_stop=None) -> list:
        """Pixels for (path, x, y) places, None where unreadable: from
        the session, the store, or the imagery, in that order."""
        progress = progress or (lambda text, done, total: None)
        should_stop = should_stop or (lambda: False)
        wanted = [(i, place) for i, place in enumerate(places)
                  if self._key(*place) not in self._pixels]
        if wanted:
            fetched = self.store.fetch([place for _i, place in wanted],
                                       self.chip_size, progress, should_stop)
            for (_i, place), arr in zip(wanted, fetched):
                if arr is not None:     # unreadable now: tried again next time
                    self._pixels[self._key(*place)] = arr
        return [self._pixels.get(self._key(*place)) for place in places]

    # -- training -------------------------------------------------------------

    def train(self, examples, progress=None, should_stop=None,
              epochs: int = DEFAULT_EPOCHS) -> Report:
        """Train on the examples; the report says what came of it.
        Raises ValueError when there is too little to learn from,
        WeightsMissing when ResNet18's weights are out of reach, and
        Cancelled when stopped."""
        torch = importlib.import_module("torch")
        progress = progress or (lambda text, done, total: None)
        should_stop = should_stop or (lambda: False)
        started = time.time()
        counts = Counter(example.class_name for example in examples)
        classes = sorted(c for c, n in counts.items() if n >= MIN_PER_CLASS)
        left_out = Counter({c: n for c, n in counts.items()
                            if n < MIN_PER_CLASS})
        if len(classes) < MIN_CLASSES:
            raise ValueError(readiness(examples))
        used = [e for e in examples if e.class_name in classes]
        pixels = self.read([(e.image_path, e.pixel_x, e.pixel_y)
                            for e in used], progress, should_stop)
        kept = [i for i, p in enumerate(pixels) if p is not None]
        # A class whose chips could not be read - an image missing, or
        # all nodata - is left out, not learned around: trained on the
        # rest, every suggestion would be the rest.
        readable = Counter(used[i].class_name for i in kept)
        unreadable = [c for c in classes if readable[c] < MIN_PER_CLASS]
        if unreadable:
            for c in unreadable:
                left_out[c] = counts[c]
            classes = [c for c in classes if c not in unreadable]
            if len(classes) < MIN_CLASSES:
                raise ValueError(
                    "Not enough chips could be read to learn from: "
                    + ", ".join(f"{readable[c]} of {counts[c]} {name_of(c)}"
                                for c in sorted(counts)) + ".")
            kept = [i for i in kept if used[i].class_name in classes]
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
        device = torch.device(self.device or (
            "cuda" if torch.cuda.is_available() else "cpu"))
        self._torch_device = device
        torch.manual_seed(self.seed)
        yt = torch.from_numpy(y).long()
        shares = np.bincount(y[train_idx], minlength=len(classes))
        weights = torch.tensor(len(train_idx) / (len(classes)
                                                 * np.maximum(shares, 1)),
                               dtype=torch.float32, device=device)
        loss_fn = torch.nn.CrossEntropyLoss(weight=weights)
        history: list = []
        if self.backbone == RESNET18:
            self._mean = self._std = None
            net = self._features_net or _resnet18(torch).to(device)
            self._features_net = net
            xt = torch.from_numpy(X.transpose(0, 3, 1, 2).copy())
            # Features once, of VIEWS views of each training chip and the
            # plain view of each held-out one: the head trains on those.
            train_views = torch.cat([
                self._features(torch, view, device, progress, should_stop,
                               f"Features, view {v + 1} of {VIEWS}")
                for v, view in enumerate(_each_view(torch, xt[train_idx]))])
            items = train_views                   # (VIEWS * n_train, 512)
            labels = yt[train_idx].repeat(VIEWS)
            val_x = (self._features(torch, xt[val_idx], device, progress,
                                    should_stop, "Features, held out")
                     if val_idx else None)
            model = torch.nn.Sequential(torch.nn.Dropout(0.2),
                                        torch.nn.Linear(items.shape[1],
                                                        len(classes))).to(device)

            def make_batch(idx):
                return items[idx]
        else:
            mean = X[train_idx].mean(axis=(0, 1, 2))
            std = X[train_idx].std(axis=(0, 1, 2)) + 1e-3
            self._mean, self._std = mean, std
            xt = torch.from_numpy(((X - mean) / std).transpose(0, 3, 1, 2)
                                  .copy())
            items = xt[train_idx]
            labels = yt[train_idx]
            val_x = xt[val_idx] if val_idx else None
            model = _small_net(torch, len(classes)).to(device)
            generator = torch.Generator().manual_seed(self.seed)

            def make_batch(idx):
                return _augment(torch, items[idx], generator)
        val_y = yt[val_idx] if val_idx else None
        best_epoch, ran, step = self._fit(
            torch, model, len(items), labels, make_batch, val_x, val_y,
            loss_fn, device, epochs, progress, should_stop, history)
        model.eval()
        self._model, self._classes = model, classes
        per_class = {c: [0, 0] for c in classes}
        confusion: Counter = Counter()
        right = 0
        if val_idx:
            predicted = _logits(torch, model, val_x, device).argmax(1).numpy()
            for truth, guess in zip(y[val_idx], predicted):
                confusion[(classes[truth], classes[guess])] += 1
                per_class[classes[truth]][1] += 1
                if truth == guess:
                    per_class[classes[truth]][0] += 1
                    right += 1
        accuracy = right / len(val_idx) if val_idx else math.nan
        self.report = Report(
            classes=classes,
            counts=Counter(classes[c] for c in y[train_idx]),
            left_out=left_out, held_out=len(val_idx), accuracy=accuracy,
            per_class={c: tuple(v) for c, v in per_class.items()},
            confusion=confusion, epochs=best_epoch,
            seconds=time.time() - started, device=str(device),
            backbone=self.backbone, steps=step, history=history)
        return self.report

    def _fit(self, torch, model, n_items, labels, make_batch, val_x, val_y,
             loss_fn, device, epochs, progress, should_stop, history):
        """The step loop: by steps, with the held-out slice reporting,
        and choosing the model only when it is big enough."""
        optimiser = torch.optim.Adam(model.parameters(), lr=1e-3,
                                     weight_decay=1e-4)
        generator = torch.Generator().manual_seed(self.seed)
        # By steps: a pass over forty chips is one step, and twelve of
        # them taught nothing (2026-10-13). Enough passes for MIN_STEPS,
        # then patience in steps too.
        per_pass = max(1, math.ceil(n_items / BATCH))
        passes = max(1, int(epochs), math.ceil(MIN_STEPS / per_pass))
        total_steps = passes * per_pass
        # A small held-out slice reports but does not choose: picking the
        # pass with the best loss on two chips kept the untrained first
        # pass over three hundred steps of learning.
        select = val_x is not None and len(val_y) >= SELECT_FROM
        best_loss, best_state, best_epoch, best_step = math.inf, None, 0, 0
        step = ran = 0
        for epoch in range(passes):
            if should_stop():
                raise Cancelled()
            model.train()
            order = torch.randperm(n_items, generator=generator)
            total = 0.0
            for start in range(0, n_items, BATCH):
                idx = order[start:start + BATCH]
                xb = make_batch(idx).to(device)
                yb = labels[idx].to(device)
                optimiser.zero_grad()
                loss = loss_fn(model(xb), yb)
                loss.backward()
                optimiser.step()
                total += loss.item() * len(idx)
                step += 1
            ran = epoch + 1
            train_loss = total / max(1, n_items)
            entry = {"pass": ran, "step": step, "loss": train_loss,
                     "val_loss": None, "val_acc": None}
            if val_x is not None:
                logits = _logits(torch, model, val_x, device)
                val_loss = float(loss_fn(logits.to(device), val_y.to(device)))
                val_acc = float((logits.argmax(1) == val_y).float().mean())
                entry["val_loss"], entry["val_acc"] = val_loss, val_acc
                progress(f"Training: pass {ran} of {passes}, loss "
                         f"{train_loss:.3f}, held-out accuracy {val_acc:.0%}",
                         step, total_steps)
                if not select:
                    best_epoch = ran
                elif val_loss < best_loss - 1e-4:
                    best_loss, best_epoch, best_step = val_loss, ran, step
                    best_state = {k: v.detach().clone()
                                  for k, v in model.state_dict().items()}
                elif step >= MIN_STEPS and step - best_step >= PATIENCE_STEPS:
                    history.append(entry)
                    break
            else:
                progress(f"Training: pass {ran} of {passes}, loss "
                         f"{train_loss:.3f}", step, total_steps)
                best_epoch = ran
            history.append(entry)
        if select and best_state is not None:
            model.load_state_dict(best_state)
        return best_epoch, ran, step

    def _features(self, torch, x, device, progress, should_stop, what):
        """ResNet18's 512 features of (N, 3, H, W) chips in [0, 1]."""
        mean = torch.tensor(IMAGENET_MEAN).view(1, 3, 1, 1)
        std = torch.tensor(IMAGENET_STD).view(1, 3, 1, 1)
        pieces = []
        n = len(x)
        progress(what, 0, n)
        with torch.no_grad():
            for start in range(0, n, FEATURE_BATCH):
                if should_stop():
                    raise Cancelled()
                batch = ((x[start:start + FEATURE_BATCH] - mean) / std)
                pieces.append(self._features_net(batch.to(device)).cpu())
                progress(what, min(n, start + FEATURE_BATCH), n)
        return torch.cat(pieces) if pieces else torch.empty((0, 512))

    # -- suggesting -----------------------------------------------------------

    def suggest(self, places, progress=None, should_stop=None) -> list:
        """A Suggestion per (path, x, y) place, None where the chip could
        not be read."""
        torch = importlib.import_module("torch")
        progress = progress or (lambda text, done, total: None)
        should_stop = should_stop or (lambda: False)
        if self._model is None:
            raise RuntimeError("Train first.")
        pixels = self.read(places, progress, should_stop)
        results: list = [None] * len(places)
        kept = [i for i, p in enumerate(pixels) if p is not None]
        if not kept:
            return results
        X = np.stack([pixels[i] for i in kept]).astype(np.float32) / 255.0
        if self.backbone == RESNET18:
            xt = self._features(torch, torch.from_numpy(
                X.transpose(0, 3, 1, 2).copy()), self._torch_device,
                progress, should_stop, "Suggesting")
        else:
            progress("Suggesting", 0, len(kept))
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
        progress("Suggesting", len(kept), len(kept))
        if self.report is not None:
            self.report.suggested = len(kept)
        return results
