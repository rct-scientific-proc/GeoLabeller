"""Snippets saved small, so training and suggesting read them, not the
imagery.

Asked for 2026-10-13: suggesting for thousands of chips opened the
GeoTIFF again for every one, and on large files over a network that was
most of the time. The store keeps each chip the model sees - the grid's
own read, scaled to INPUT_PX - on disk under the system's temporary
folder, one .npz per image, chip size and display setting, keyed by the
chip's rounded centre. A fetch reads what is there, reads the rest from
the imagery with the file opened once (snippets.read_label_snippets),
across a few threads, and saves it for next time. Save snippets in the
window fills the store ahead of training; Clear empties it.

Qt-free. Progress is reported as (text, done, total).
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import tempfile
import threading
from pathlib import Path

import numpy as np
from PIL import Image

from .. import display_settings
from ..snippets import read_label_snippets

INPUT_PX = 64                   # every chip is seen at this size
WORKERS = 4                     # images read at once
REPORT_EVERY = 100              # chips between progress reports
CHUNK = 200                     # chips read between looks at Stop


class Cancelled(Exception):
    """Stopped at the user's word."""


def default_root() -> Path:
    return Path(tempfile.gettempdir()) / "GeoLabeller" / "snippets"


def _run_across(tasks, workers: int):
    """Run the callables over ``workers`` threads; the first error any
    of them raised is raised here. (concurrent.futures is left out of
    the frozen build, so threading does the job.)"""
    pending: queue.Queue = queue.Queue()
    for task in tasks:
        pending.put(task)
    errors: list = []

    def work():
        while not errors:
            try:
                task = pending.get_nowait()
            except queue.Empty:
                return
            try:
                task()
            except BaseException as exc:  # noqa: BLE001 - raised below
                errors.append(exc)
                return

    threads = [threading.Thread(target=work, daemon=True)
               for _ in range(max(1, min(workers, len(tasks))))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]


def _resized(arr: "np.ndarray | None", px: int) -> "np.ndarray | None":
    if arr is None or arr.shape[:2] == (px, px):
        return arr
    return np.asarray(Image.fromarray(arr).resize((px, px), Image.BOX))


class SnippetStore:
    """One folder of .npz files; see the module."""

    def __init__(self, root: "Path | None" = None, input_px: int = INPUT_PX):
        self.root = Path(root) if root is not None else default_root()
        self.input_px = int(input_px)
        self._lock = threading.Lock()

    # -- where ----------------------------------------------------------------

    def file_for(self, image_path: str, chip_size: int) -> Path:
        """The .npz for this image at this chip size, as drawn now."""
        display = display_settings.for_image(image_path)
        key = json.dumps([os.path.normcase(os.path.abspath(image_path)),
                          int(chip_size), self.input_px, display.to_dict()],
                         sort_keys=True)
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        return self.root / f"{Path(image_path).stem}-{digest}.npz"

    def _load(self, path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            with np.load(path) as data:
                xs, ys, pixels = data["xs"], data["ys"], data["pixels"]
        except (OSError, ValueError, KeyError):
            return {}                   # a broken file is read past
        return {(int(x), int(y)): pixels[i] for i, (x, y) in
                enumerate(zip(xs, ys))}

    def _save(self, path: Path, chips: dict):
        self.root.mkdir(parents=True, exist_ok=True)
        keys = sorted(chips)
        xs = np.array([k[0] for k in keys], dtype=np.int32)
        ys = np.array([k[1] for k in keys], dtype=np.int32)
        pixels = (np.stack([chips[k] for k in keys]) if keys
                  else np.zeros((0, self.input_px, self.input_px, 3), np.uint8))
        # A tmp file of this writer's own: two GeoLabellers sharing the
        # store must not trip over each other's.
        tmp = path.with_name(f"{path.stem}.{os.getpid()}-"
                             f"{threading.get_ident()}.tmp.npz")
        with open(tmp, "wb") as handle:
            np.savez(handle, xs=xs, ys=ys, pixels=pixels)
        try:
            os.replace(tmp, path)
        except OSError:
            # Another writer got there; its chips are as good.
            try:
                tmp.unlink()
            except OSError:
                pass

    # -- what -----------------------------------------------------------------

    def fetch(self, places, chip_size: int, progress=None, should_stop=None,
              save: bool = True) -> list:
        """Pixels for (image_path, x, y) places, in order, None where a
        chip cannot be read: from the store where saved, else from the
        imagery, each file opened once, and saved."""
        progress = progress or (lambda text, done, total: None)
        should_stop = should_stop or (lambda: False)
        by_path: dict = {}
        for i, (path, x, y) in enumerate(places):
            by_path.setdefault(path, []).append((i, float(x), float(y)))
        out: list = [None] * len(places)
        total = len(places)
        counted = [0]

        def one_image(path, items):
            file = self.file_for(path, chip_size)
            with self._lock:
                saved = self._load(file)
            missing = []
            for i, x, y in items:
                key = (int(round(x)), int(round(y)))
                if key in saved:
                    out[i] = saved[key]
                else:
                    missing.append((i, x, y, key))
            fresh = {}
            if missing:
                arrays: list = []
                for start in range(0, len(missing), CHUNK):
                    if should_stop():
                        raise Cancelled()
                    chunk = missing[start:start + CHUNK]
                    arrays += read_label_snippets(
                        path, [(x, y) for _i, x, y, _key in chunk],
                        chip_size)
                for (i, _x, _y, key), arr in zip(missing, arrays):
                    arr = _resized(arr, self.input_px)
                    out[i] = arr
                    if arr is not None:
                        fresh[key] = arr
                if save and fresh:
                    with self._lock:
                        latest = self._load(file)
                        latest.update(fresh)
                        self._save(file, latest)
            with self._lock:
                counted[0] += len(items)
                done = counted[0]
            progress(f"Reading chips ({len(missing)} of {len(items)} new "
                     f"from {Path(path).name})", done, total)

        progress("Reading chips", 0, total)
        _run_across([(lambda p=path, i=items: one_image(p, i))
                     for path, items in by_path.items()], WORKERS)
        return out

    # -- housekeeping ---------------------------------------------------------

    def files(self) -> list:
        if not self.root.exists():
            return []
        return sorted(p for p in self.root.glob("*.npz")
                      if not p.name.endswith(".tmp.npz"))

    def size_bytes(self) -> int:
        return sum(p.stat().st_size for p in self.files())

    def count(self) -> int:
        """Chips saved, over every file."""
        total = 0
        for path in self.files():
            try:
                with np.load(path) as data:
                    total += int(data["xs"].shape[0])
            except (OSError, ValueError, KeyError):
                pass
        return total

    def clear(self) -> int:
        """Delete every saved file; how many there were."""
        files = self.files()
        for path in files:
            try:
                path.unlink()
            except OSError:
                pass
        return len(files)

    def describe(self) -> str:
        n = self.count()
        if not n:
            return f"No snippets saved yet ({self.root})."
        mb = self.size_bytes() / 1e6
        size = f"{mb:.1f} MB" if mb < 10 else f"{mb:.0f} MB"
        return (f"{n:,} snippet{'' if n == 1 else 's'} saved, {size}, "
                f"in {self.root}.")
