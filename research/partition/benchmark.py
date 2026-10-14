"""A synthetic review with known truth, for running the harness where
no real review exists and for checking it end to end.

Four shape classes the "model" knows (disc, ring, bar, cross), one it
does not (triangle: the reviewer has to create the class), and clutter
(the reviewer says Ignore), drawn with random turn, size, brightness and
texture on a noisy ground, into a few GeoTIFFs. A simulated model
confuses the look-alike pairs (disc/ring, bar/cross), calls triangles
crosses or bars, and clutter anything. The review file lists every
object as the model saw it; the project's ledger holds the truth as the
verdict on each, as if the reviewer had already been through it all.

The shapes are far simpler than overhead imagery; the benchmark shows
the harness working and gives a first ranking of the techniques, not
their worth on real chips.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS

from app.labels import LabelProject
from app.model_review.ledger import IGNORE, Verdict

KNOWN = ("disc", "ring", "bar", "cross")
NEW = "triangle"
CLUTTER = "clutter"
# Per image of 100 cells.
MIX = {"disc": 22, "ring": 18, "bar": 22, "cross": 18, NEW: 10, CLUTTER: 10}
# What the model says, by what the object is.
CONFUSION = {
    "disc": (("disc", 0.30), ("ring", 0.50), ("cross", 0.20)),
    "ring": (("ring", 0.30), ("disc", 0.50), ("cross", 0.20)),
    "bar": (("bar", 0.30), ("cross", 0.55), ("disc", 0.15)),
    "cross": (("cross", 0.30), ("bar", 0.50), ("ring", 0.20)),
    NEW: (("cross", 0.50), ("bar", 0.30), ("disc", 0.20)),
    CLUTTER: tuple((c, 0.25) for c in KNOWN),
}
CELL = 96                       # pixels per object cell
CHIP_PX = 96                    # the chip read around each object
IMAGE_SIZE = 1024
UTM = CRS.from_epsg(32618)


def _ground(rng, size: int) -> np.ndarray:
    """A mottled greenish-brown ground: low-frequency blobs plus grain."""
    coarse = rng.normal(0, 1, (size // 32 + 1, size // 32 + 1))
    ys, xs = np.mgrid[0:size, 0:size] / 32.0
    y0, x0 = ys.astype(int), xs.astype(int)
    fy, fx = ys - y0, xs - x0
    top = coarse[y0, x0] * (1 - fx) + coarse[y0, x0 + 1] * fx
    bottom = coarse[y0 + 1, x0] * (1 - fx) + coarse[y0 + 1, x0 + 1] * fx
    smooth = top * (1 - fy) + bottom * fy
    grain = rng.normal(0, 1, (size, size))
    base = np.stack([smooth * 14 + 96, smooth * 16 + 104, smooth * 10 + 76],
                    axis=2)
    base += grain[..., None] * np.array([7.0, 8.0, 6.0])
    return base


def _mask(shape: str, r: float, angle: float, rng) -> np.ndarray:
    """A (2s+1, 2s+1) float mask of the shape, turned by ``angle``."""
    s = int(math.ceil(r * 1.6)) + 2
    ys, xs = np.mgrid[-s:s + 1, -s:s + 1].astype(float)
    c, sn = math.cos(angle), math.sin(angle)
    u = xs * c + ys * sn
    v = -xs * sn + ys * c
    if shape == "disc":
        mask = (u * u + v * v) <= r * r
    elif shape == "ring":
        d = u * u + v * v
        mask = (d <= r * r) & (d >= (0.55 * r) ** 2)
    elif shape == "bar":
        mask = (np.abs(u) <= 1.4 * r) & (np.abs(v) <= 0.3 * r)
    elif shape == "cross":
        mask = ((np.abs(u) <= 1.2 * r) & (np.abs(v) <= 0.28 * r)) | (
            (np.abs(v) <= 1.2 * r) & (np.abs(u) <= 0.28 * r))
    elif shape == NEW:
        # An equilateral triangle: inside all three half-planes.
        h = r * 1.3
        mask = (v >= -h / 2) & (v <= h - u * math.sqrt(3)) & (
            v <= h + u * math.sqrt(3))
    else:                       # clutter: a few faint specks
        mask = np.zeros_like(u, dtype=bool)
        for _ in range(rng.integers(3, 7)):
            cx, cy = rng.uniform(-r, r, 2)
            rr = rng.uniform(1.5, 3.5)
            mask |= ((xs - cx) ** 2 + (ys - cy) ** 2) <= rr * rr
    # A soft edge, so the shapes are not aliased squares of pixels.
    soft = mask.astype(float)
    soft = (soft + np.roll(soft, 1, 0) + np.roll(soft, -1, 0)
            + np.roll(soft, 1, 1) + np.roll(soft, -1, 1)) / 5.0
    return soft


def _paint(image: np.ndarray, cx: int, cy: int, mask: np.ndarray,
           colour: np.ndarray, rng):
    s = mask.shape[0] // 2
    y0, y1 = cy - s, cy + s + 1
    x0, x1 = cx - s, cx + s + 1
    patch = image[y0:y1, x0:x1]
    texture = 1.0 + rng.normal(0, 0.08, mask.shape)[..., None]
    patch[:] = patch * (1 - mask[..., None]) + (
        colour * texture) * mask[..., None]


def build(out_dir, n_images: int = 4, seed: int = 0) -> dict:
    """Write the imagery, the project (with every verdict) and the
    review file under ``out_dir``; return their paths and the chip size."""
    out_dir = Path(out_dir)
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    project = LabelProject()
    for class_name in (*KNOWN, NEW):
        project.add_class(class_name)
    review: list = []
    verdicts: list = []
    cells = IMAGE_SIZE // CELL
    for i in range(n_images):
        image = _ground(rng, IMAGE_SIZE)
        kinds = [k for k, n in MIX.items() for _ in range(n)]
        rng.shuffle(kinds)
        places = [(r, c) for r in range(cells) for c in range(cells)]
        rng.shuffle(places)
        name = f"scene_{i:02d}.tif"
        path = images_dir / name
        for kind, (row, col) in zip(kinds, places):
            r = rng.uniform(11, 17) if kind != CLUTTER else 12.0
            cx = col * CELL + CELL // 2 + int(rng.integers(-12, 13))
            cy = row * CELL + CELL // 2 + int(rng.integers(-12, 13))
            mask = _mask(kind, r, rng.uniform(0, math.pi), rng)
            if kind == CLUTTER:
                colour = np.array([150, 150, 140]) + rng.normal(0, 10, 3)
                mask *= 0.45
            else:
                grey = rng.choice([40.0, 60.0, 180.0, 215.0, 240.0])
                colour = np.full(3, grey) + rng.normal(0, 12, 3)
            _paint(image, cx, cy, mask, colour, rng)
            options, weights = zip(*CONFUSION[kind])
            predicted = str(rng.choice(options, p=np.array(weights)
                                       / sum(weights)))
            if kind == CLUTTER:
                score = rng.uniform(0.5, 0.8)
            elif predicted == kind:
                score = rng.uniform(0.75, 0.98)
            else:
                score = rng.uniform(0.6, 0.95)
            review.append({"image": name, "x": cx, "y": cy,
                           "class": predicted, "score": round(score, 3)})
            verdicts.append(Verdict(
                image=name, pixel_x=float(cx), pixel_y=float(cy),
                predicted=predicted, score=score,
                became=IGNORE if kind == CLUTTER else kind,
                source="review.json"))
        data = np.clip(image, 0, 255).astype(np.uint8)
        transform = Affine(0.5, 0, 500000 + i * IMAGE_SIZE * 0.5,
                           0, -0.5, 4650000)
        with rasterio.open(path, "w", driver="GTiff", width=IMAGE_SIZE,
                           height=IMAGE_SIZE, count=3, dtype="uint8",
                           crs=UTM, transform=transform) as dst:
            for b in range(3):
                dst.write(data[..., b], b + 1)
        project.add_image(str(path), path.stem, "synthetic",
                          IMAGE_SIZE, IMAGE_SIZE)
    project.model_review.extend(verdicts)
    project_path = out_dir / "synthetic.geolabel"
    project.save(project_path)
    review_path = out_dir / "review.json"
    review_path.write_text(json.dumps({"chips": review}, indent=1),
                           encoding="utf-8")
    return {"project": str(project_path), "review": str(review_path),
            "chip_size": CHIP_PX, "objects": len(review)}
