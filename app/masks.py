"""Named binary snippet masks: encoding, anchoring and statistics.

A mask is painted on a label's snippet and stored ON the label as a named
binary layer:

    {"name": "hull", "x0": 128, "y0": 64, "width": 224, "height": 224,
     "rle": [312, 5, 214, ...]}

The window (x0, y0, width, height) is the snippet crop IN SOURCE PIXELS at
paint time, from the shared snippet_frame - so the mask stays anchored to
the imagery rather than to whatever snippet size happens to be selected
later, and can be re-projected into any other window (the H5 export's, for
one) by pure translation.

The compression, precisely: "rle" is a run-length encoding of the binary
window read row-major (row 0 left to right, then row 1, ...). Runs
alternate between 0s and 1s and ALWAYS start with a run of 0s - a mask
whose first pixel is set therefore begins "0,n,...". The runs must sum to
width*height exactly; decoders reject anything else rather than render a
shifted mask.

The runs are serialized as ONE comma-separated string, not a JSON array:
the project file is pretty-printed, and an array of integers costs a full
indented line (~20 bytes) per run - a few thousand runs ballooned into
tens of kilobytes of whitespace. As a string, the same mask is one line at
a few bytes per run. Readers accept the pre-release array form too, so
projects written before the change still load.

Masks are independent binary layers: several can overlap on one snippet,
which a single labelled-component array could never represent.
"""
import numpy as np


def encode_rle(mask: np.ndarray) -> list:
    """Run-length encode a binary mask (row-major, zeros first)."""
    flat = np.asarray(mask, dtype=bool).ravel()
    if flat.size == 0:
        return []
    changes = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    boundaries = np.concatenate(([0], changes, [flat.size]))
    runs = np.diff(boundaries).tolist()
    if flat[0]:
        runs.insert(0, 0)   # the encoding always starts with a zero-run
    return [int(r) for r in runs]


def decode_rle(runs: list, width: int, height: int) -> np.ndarray:
    """Decode :func:`encode_rle` output back to a (height, width) bool array.

    Raises ValueError when the runs do not cover exactly width*height
    pixels - a corrupt mask must fail loudly, not render shifted.
    """
    total = width * height
    if sum(runs) != total:
        raise ValueError(
            f"mask RLE covers {sum(runs)} pixels, window has {total}")
    flat = np.zeros(total, dtype=bool)
    pos = 0
    value = False
    for run in runs:
        if run:
            flat[pos:pos + run] = value
            pos += run
        value = not value
    return flat.reshape(height, width)


def mask_entry(name: str, x0: int, y0: int, mask: np.ndarray) -> dict:
    """Build the serialized form of one named mask."""
    h, w = mask.shape
    return {"name": name, "x0": int(x0), "y0": int(y0),
            "width": int(w), "height": int(h),
            "rle": ",".join(str(r) for r in encode_rle(mask))}


def entry_runs(entry: dict) -> list:
    """The run list of a serialized mask (string form or legacy array)."""
    runs = entry["rle"]
    if isinstance(runs, str):
        return [int(token) for token in runs.split(",") if token]
    return [int(r) for r in runs]


def entry_array(entry: dict) -> np.ndarray:
    """The (height, width) bool array of a serialized mask."""
    return decode_rle(entry_runs(entry), entry["width"], entry["height"])


def entry_in_window(entry: dict, x0: int, y0: int,
                    width: int, height: int) -> np.ndarray:
    """A stored mask re-anchored into another source-pixel window.

    Both the mask and the requested window are source-pixel rectangles, so
    this is a pure translate-and-intersect: pixels of the mask that fall
    inside the window land at their true source position, everything else is
    False. This is what keeps a mask painted at one snippet size correct
    under any other - and what aligns it with the H5 export's window.
    """
    out = np.zeros((height, width), dtype=bool)
    stored = entry_array(entry)
    sx, sy = entry["x0"], entry["y0"]
    left = max(sx, x0)
    top = max(sy, y0)
    right = min(sx + entry["width"], x0 + width)
    bottom = min(sy + entry["height"], y0 + height)
    if right <= left or bottom <= top:
        return out
    out[top - y0:bottom - y0, left - x0:right - x0] = \
        stored[top - sy:bottom - sy, left - sx:right - sx]
    return out


def fill_enclosed(mask: np.ndarray) -> "tuple[np.ndarray, int]":
    """Fill every region the mask fully encloses; returns (filled, added).

    The complement is flooded inward from the window border; whatever open
    ground the flood cannot reach is enclosed by the mask and gets filled.
    An outline with a gap encloses nothing - its inside leaks to the border
    through the gap - so ``added`` comes back 0 and the mask is returned
    unchanged, which is exactly the "refuse to fill an unclosed hull"
    behaviour the editor wants.

    The flood is 4-connected, which makes the OUTLINE effectively
    8-connected: a hand-drawn one-pixel stroke whose pixels only touch
    diagonally still counts as closed.
    """
    mask = np.asarray(mask, dtype=bool)
    open_ground = ~mask
    outside = np.zeros_like(mask)
    outside[0, :] = open_ground[0, :]
    outside[-1, :] = open_ground[-1, :]
    outside[:, 0] |= open_ground[:, 0]
    outside[:, -1] |= open_ground[:, -1]
    while True:
        grown = outside.copy()
        grown[1:, :] |= outside[:-1, :]
        grown[:-1, :] |= outside[1:, :]
        grown[:, 1:] |= outside[:, :-1]
        grown[:, :-1] |= outside[:, 1:]
        grown &= open_ground
        if np.array_equal(grown, outside):
            break
        outside = grown
    holes = open_ground & ~outside
    added = int(holes.sum())
    if added == 0:
        return mask, 0
    return mask | holes, added


def merged_entry(name: str, x0: int, y0: int, layer: np.ndarray,
                 previous: "dict | None" = None) -> dict:
    """Serialize an edited window WITHOUT losing pixels outside it.

    The editor paints inside one window, but a previously stored mask may
    extend beyond it (painted earlier at a larger snippet size). Committing
    only the visible window would silently crop that content - so the new
    entry covers the UNION of the previous window and the edited one: the
    edited window replaces its region wholesale (erasures included), and
    everything outside it survives untouched.
    """
    if previous is None:
        return mask_entry(name, x0, y0, layer)
    h, w = layer.shape
    ux0 = min(x0, previous["x0"])
    uy0 = min(y0, previous["y0"])
    ux1 = max(x0 + w, previous["x0"] + previous["width"])
    uy1 = max(y0 + h, previous["y0"] + previous["height"])
    union = entry_in_window(previous, ux0, uy0, ux1 - ux0, uy1 - uy0)
    union[y0 - uy0:y0 - uy0 + h, x0 - ux0:x0 - ux0 + w] = layer
    return mask_entry(name, ux0, uy0, union)


def mask_statistics(pixels: np.ndarray, mask: np.ndarray) -> "dict | None":
    """Per-band mean/std of the masked object versus the background.

    ``pixels`` is a (bands, height, width) array of RAW source values (not
    the display stretch - distributions of stretched bytes would be
    meaningless), ``mask`` the (height, width) binary layer. None when the
    mask selects nothing or everything (no comparison to make).
    """
    mask = np.asarray(mask, dtype=bool)
    n_object = int(mask.sum())
    if n_object == 0 or n_object == mask.size:
        return None
    data = np.asarray(pixels, dtype=np.float64)
    inside = data[:, mask]
    outside = data[:, ~mask]
    return {
        "pixels_object": n_object,
        "pixels_background": int(mask.size - n_object),
        "object_mean": inside.mean(axis=1).tolist(),
        "object_std": inside.std(axis=1).tolist(),
        "background_mean": outside.mean(axis=1).tolist(),
        "background_std": outside.std(axis=1).tolist(),
    }
