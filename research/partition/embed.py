"""Chips and their embeddings.

Chips are read from the imagery with the application's own snippet
reader (the frame the Snippet Editor and the exports use), at the chip
size the review window reads, then resized for the backbone. Three
embeddings:

  pixels    a 16x16 grey thumbnail through PCA - the floor every learned
            feature has to beat;
  resnet18  ImageNet ResNet18 without its last layer, 512 numbers, the
            backbone Model Review already trains a head on;
  dinov2    DINOv2 ViT-S/14's class token, 384 numbers (Oquab et al.
            2023): the self-supervised features that category discovery
            and slice discovery papers cluster on.

The two networks see the eight turned and mirrored views of a chip and
the features are averaged (overhead imagery has no up); every embedding
is unit length, so distances are cosine distances.
"""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

from app.model_review.learner import IMAGENET_MEAN, IMAGENET_STD, _resnet18
from app.snippets import read_label_snippets

EMBEDDINGS = ("pixels", "resnet18", "dinov2")
INPUT_PX = {"resnet18": 128, "dinov2": 224}
BATCH = 32


def cache_key(places, chip_size: int, out_px: int) -> str:
    text = json.dumps([[p, round(x, 1), round(y, 1)] for p, x, y in places]
                      + [chip_size, out_px])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def read_chips(places, chip_size: int, out_px: int = 128,
               cache_dir: "Path | None" = None, log=print) -> tuple:
    """(chips, ok): an (N, out_px, out_px, 3) uint8 array and a boolean
    mask of the places whose chip could be read (the rest are zeros)."""
    cache = None
    if cache_dir is not None:
        cache = Path(cache_dir) / f"chips-{cache_key(places, chip_size, out_px)}.npz"
        if cache.exists():
            data = np.load(cache)
            return data["chips"], data["ok"]
    by_path: dict = {}
    for i, (path, x, y) in enumerate(places):
        by_path.setdefault(path, []).append((i, x, y))
    chips = np.zeros((len(places), out_px, out_px, 3), dtype=np.uint8)
    ok = np.zeros(len(places), dtype=bool)
    for n, (path, items) in enumerate(by_path.items(), 1):
        log(f"  reading {len(items)} chips of {Path(path).name} "
            f"({n}/{len(by_path)})")
        arrays = read_label_snippets(path, [(x, y) for _i, x, y in items],
                                     chip_size)
        for (i, _x, _y), arr in zip(items, arrays):
            if arr is None or arr.size == 0:
                continue
            image = Image.fromarray(np.ascontiguousarray(arr[..., :3]))
            chips[i] = np.asarray(image.resize((out_px, out_px),
                                               Image.BILINEAR))
            ok[i] = True
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(cache, chips=chips, ok=ok)
    return chips, ok


def cached(path, compute) -> np.ndarray:
    """The array at ``path``, or ``compute()`` saved there."""
    path = Path(path)
    if path.exists():
        return np.load(path)
    X = compute()
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, X)
    return X


def _unit(X: np.ndarray) -> np.ndarray:
    X = np.asarray(X, dtype=np.float32)
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    return X / np.maximum(norms, 1e-8)


def embed_pixels(chips: np.ndarray, dims: int = 32) -> np.ndarray:
    from sklearn.decomposition import PCA
    grey = chips.astype(np.float32).mean(axis=3)
    thumbs = np.stack([np.asarray(Image.fromarray(g.astype(np.uint8))
                                  .resize((16, 16), Image.BILINEAR))
                       for g in grey]).reshape(len(chips), -1)
    thumbs = (thumbs - thumbs.mean(axis=1, keepdims=True)) / (
        thumbs.std(axis=1, keepdims=True) + 1e-6)
    dims = max(2, min(dims, len(chips) - 1, thumbs.shape[1]))
    return _unit(PCA(n_components=dims, random_state=0).fit_transform(thumbs))


def _views(torch, batch):
    """The eight dihedral views of an (N, 3, H, W) batch, one at a time."""
    for k in range(4):
        turned = torch.rot90(batch, k, dims=(2, 3))
        yield turned
        yield torch.flip(turned, dims=(3,))


def _network_features(net, chips: np.ndarray, px: int, mean, std,
                      tta: bool, log=print, what="") -> np.ndarray:
    torch = importlib.import_module("torch")
    out = []
    n = len(chips)
    mean_t = torch.tensor(mean).view(1, 3, 1, 1)
    std_t = torch.tensor(std).view(1, 3, 1, 1)
    with torch.no_grad():
        for start in range(0, n, BATCH):
            block = chips[start:start + BATCH]
            if block.shape[1] != px:
                block = np.stack([np.asarray(Image.fromarray(c).resize(
                    (px, px), Image.BILINEAR)) for c in block])
            x = torch.from_numpy(block.astype(np.float32) / 255.0
                                 ).permute(0, 3, 1, 2).contiguous()
            x = (x - mean_t) / std_t
            views = list(_views(torch, x)) if tta else [x]
            features = sum(net(v).float() for v in views) / len(views)
            out.append(features.cpu().numpy())
            log(f"  {what}: {min(n, start + BATCH)}/{n}")
    return _unit(np.concatenate(out)) if out else np.zeros((0, 1), np.float32)


def embed_resnet18(chips: np.ndarray, tta: bool = True, log=print):
    torch = importlib.import_module("torch")
    net = _resnet18(torch)
    return _network_features(net, chips, INPUT_PX["resnet18"], IMAGENET_MEAN,
                             IMAGENET_STD, tta, log, "resnet18")


def embed_dinov2(chips: np.ndarray, tta: bool = True, log=print):
    torch = importlib.import_module("torch")
    net = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14",
                         verbose=False)
    net.eval()
    for parameter in net.parameters():
        parameter.requires_grad_(False)
    return _network_features(net, chips, INPUT_PX["dinov2"], IMAGENET_MEAN,
                             IMAGENET_STD, tta, log, "dinov2")


def embed(name: str, chips: np.ndarray, tta: bool = True, log=print):
    if name == "pixels":
        return embed_pixels(chips)
    if name == "resnet18":
        return embed_resnet18(chips, tta, log)
    if name == "dinov2":
        return embed_dinov2(chips, tta, log)
    raise ValueError(f"unknown embedding {name!r}; one of {EMBEDDINGS}")
