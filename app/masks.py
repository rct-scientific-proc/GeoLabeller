"""Named binary snippet masks: encoding, anchoring and statistics.

A mask is painted on a label's snippet and stored ON the label as a named
binary layer:

    {"name": "hull", "x0": 0, "y0": 0, "width": 30000, "height": 24000,
     "rle": "361200128,14,29986,..."}

As of project format 4.0 the window IS the full source image: x0/y0 are 0
and width/height equal the image dimensions, so the rle decodes directly
against the image with no anchoring step - the mask is co-registered with
its imagery. (Format 3.9 wrote the paint-time snippet crop instead;
readers honor whatever window an entry records, and 3.9 entries are
migrated to full-image on load.) The run COUNT barely changes between the
two anchorings - only the runs bordering empty ground grow large.

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

A full-image window over survey imagery can cover BILLIONS of pixels, so
nothing in this module ever materializes an entry-sized array: encoding,
windowed decoding, merging and migration all work on the runs themselves
(streams of per-row spans), costing O(number of runs) time and
O(requested window) memory.

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
    if any(run < 0 for run in runs):
        # A negative run can make the sum come out right while the decode
        # walks backwards over pixels it already wrote - exactly the
        # silently shifted mask the exact-cover check exists to prevent.
        raise ValueError("mask RLE contains a negative run")
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
    """Build the serialized form of one named mask (windowed anchoring).

    Format 3.9 wrote entries like this; 4.0 writers use
    :func:`full_image_entry` instead. Kept because readers must accept the
    windowed form, and tests exercise it.
    """
    h, w = mask.shape
    return {"name": name, "x0": int(x0), "y0": int(y0),
            "width": int(w), "height": int(h),
            "rle": ",".join(str(r) for r in encode_rle(mask))}


# ---------------------------------------------------------------------------
# Run/span arithmetic: everything below works on ordered per-row spans of
# 1-pixels, (row, col_start, col_end) with col_end exclusive, in absolute
# SOURCE-image coordinates - never on entry-sized arrays.
# ---------------------------------------------------------------------------

def prepare_entry(entry: dict) -> dict:
    """An entry with its RLE already parsed, for repeated window reads.

    Reading a mask into a window costs one pass over its runs, but the
    PARSE in front of that - splitting the string, int()-ing every token,
    summing the lot to check the cover - was paid again on every call. The
    H5 export asks each mask about each of the nine crops of every label,
    so a densely masked image spent tens of seconds re-reading the same
    strings (measured: 6.95 s for 7,200 crop/mask pairs).

    The result is the entry plus the parsed runs and the bounding box of
    the painted pixels, so a window that cannot touch the mask is rejected
    on four comparisons instead of a walk. It is still an entry - the
    name, anchor and size are all there - and passing it anywhere an entry
    goes works, including straight back into this function.

    The streaming invariant is untouched: runs, not pixels. Nothing here
    is entry-sized.
    """
    if "_runs" in entry:
        return entry
    prepared = dict(entry)
    runs = _validated_runs(entry)
    prepared["_runs"] = runs
    row0 = col0 = None
    row1 = col1 = 0
    for row, c0, c1 in _entry_row_spans(prepared):
        if row0 is None:
            row0, col0 = row, c0       # spans arrive in raster order
        row1 = row + 1
        col0 = min(col0, c0)
        col1 = max(col1, c1)
    prepared["_bbox"] = (None if row0 is None
                         else (row0, row1, col0, col1))
    return prepared


def _validated_runs(entry: dict) -> list:
    """The entry's runs, checked for the two ways an RLE can lie."""
    runs = entry.get("_runs")
    if runs is not None:
        return runs
    ew, eh = int(entry["width"]), int(entry["height"])
    runs = entry_runs(entry)
    if any(run < 0 for run in runs):
        raise ValueError("mask RLE contains a negative run")
    if sum(runs) != ew * eh:
        raise ValueError(
            f"mask RLE covers {sum(runs)} pixels, window has {ew * eh}")
    return runs


def _entry_row_spans(entry: dict):
    """Yield the 1-runs of a serialized mask as absolute per-row spans."""
    ew = int(entry["width"])
    ex0, ey0 = int(entry["x0"]), int(entry["y0"])
    runs = _validated_runs(entry)
    pos = 0
    value = False
    for run in runs:
        if value and run:
            start, end = pos, pos + run
            r0, r1 = start // ew, (end - 1) // ew
            for r in range(r0, r1 + 1):
                c0 = start - r * ew if r == r0 else 0
                c1 = end - r * ew if r == r1 else ew
                yield (ey0 + r, ex0 + c0, ex0 + c1)
        pos += run
        value = not value


def _mask_row_spans(mask: np.ndarray, x0: int, y0: int):
    """Yield a window array's 1-runs as absolute per-row spans."""
    mask = np.asarray(mask, dtype=bool)
    for r in range(mask.shape[0]):
        row = mask[r]
        if not row.any():
            continue
        idx = np.flatnonzero(row[1:] != row[:-1]) + 1
        bounds = np.concatenate(([0], idx, [row.size]))
        value = bool(row[0])
        for i in range(len(bounds) - 1):
            if value:
                yield (y0 + r, x0 + int(bounds[i]), x0 + int(bounds[i + 1]))
            value = not value


def _spans_to_entry(name: str, spans, image_width: int,
                    image_height: int) -> dict:
    """Serialize ordered absolute spans as a FULL-IMAGE entry.

    Spans must be in raster order; parts outside the image are clipped.
    Touching spans merge, so the runs come out canonical.
    """
    runs = []
    pos = 0
    for row, c0, c1 in spans:
        if row < 0 or row >= image_height:
            continue
        c0, c1 = max(0, c0), min(image_width, c1)
        if c1 <= c0:
            continue
        start = row * image_width + c0
        length = c1 - c0
        gap = start - pos
        if gap < 0:
            raise ValueError("mask spans out of order")
        if gap == 0 and runs:
            runs[-1] += length      # abuts the previous 1-run: extend it
        else:
            runs.append(gap)
            runs.append(length)
        pos = start + length
    tail = image_width * image_height - pos
    if tail:
        runs.append(tail)
    if not runs:
        runs = [image_width * image_height]
    return {"name": name, "x0": 0, "y0": 0,
            "width": int(image_width), "height": int(image_height),
            "rle": ",".join(str(r) for r in runs)}


def full_image_entry(name: str, x0: int, y0: int, mask: np.ndarray,
                     image_width: int, image_height: int) -> dict:
    """Serialize a painted window as a full-image mask entry.

    The runs are composed arithmetically from the window's row spans - the
    image-sized canvas is never built (it can be billions of pixels).
    """
    return _spans_to_entry(name, _mask_row_spans(mask, x0, y0),
                           image_width, image_height)


def entry_to_full_image(entry: dict, image_width: int,
                        image_height: int) -> dict:
    """Re-anchor a (possibly windowed) entry to the full image.

    Already-full entries pass through unchanged; this is the 3.9 -> 4.0
    load-time migration.
    """
    if (int(entry["x0"]) == 0 and int(entry["y0"]) == 0
            and int(entry["width"]) == int(image_width)
            and int(entry["height"]) == int(image_height)):
        return dict(entry)
    return _spans_to_entry(entry["name"], _entry_row_spans(entry),
                           image_width, image_height)


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

    Streams the entry's runs rather than decoding its window (which, for a
    full-image entry over survey imagery, would be a multi-gigabyte array):
    memory is the REQUESTED window only.

    An entry through prepare_entry knows where its painted pixels are, so
    a window that cannot touch them returns here without a walk - which is
    most (crop, mask) pairs of an export, since a mask belongs to one
    object and the crops are spread over the whole raster.
    """
    out = np.zeros((height, width), dtype=bool)
    wy1, wx1 = y0 + height, x0 + width
    if "_bbox" in entry:
        box = entry["_bbox"]
        if box is None:
            return out
        row0, row1, col0, col1 = box
        if row1 <= y0 or row0 >= wy1 or col1 <= x0 or col0 >= wx1:
            return out
    for row, c0, c1 in _entry_row_spans(entry):
        if row >= wy1:
            break               # spans arrive in raster order
        if row < y0:
            continue
        cc0, cc1 = max(c0, x0), min(c1, wx1)
        if cc1 > cc0:
            out[row - y0, cc0 - x0:cc1 - x0] = True
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
                 previous: "dict | None" = None,
                 image_size: "tuple[int, int] | None" = None) -> dict:
    """Serialize an edited window WITHOUT losing pixels outside it.

    The editor paints inside one window, but a previously stored mask may
    extend beyond it (painted earlier at a larger snippet size). Committing
    only the visible window would silently crop that content - so the
    edited window replaces its region wholesale (erasures included) while
    everything outside it survives untouched.

    With ``image_size`` (width, height) the result is a full-image entry,
    spliced run-by-run: the previous entry's spans are clipped AGAINST the
    edited rectangle, the window's own spans dropped in, and the whole lot
    re-serialized - no array larger than the edited window is ever built.
    Without it (legacy callers, old tests) the result covers the union of
    the two windows, as format 3.9 did.
    """
    if image_size is not None:
        iw, ih = image_size
        h, w = layer.shape
        rx0, ry0, rx1, ry1 = x0, y0, x0 + w, y0 + h
        pieces = []
        if previous is not None:
            for row, c0, c1 in _entry_row_spans(previous):
                if ry0 <= row < ry1:
                    # Keep only the parts outside the edited rectangle.
                    if c0 < rx0:
                        pieces.append((row, c0, min(c1, rx0)))
                    if c1 > rx1:
                        pieces.append((row, max(c0, rx1), c1))
                else:
                    pieces.append((row, c0, c1))
        pieces.extend(_mask_row_spans(layer, x0, y0))
        pieces.sort()
        return _spans_to_entry(name, pieces, iw, ih)
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


def mask_statistics(pixels: np.ndarray, mask: np.ndarray,
                    nodata=None) -> "dict | None":
    """Per-band mean/std of the masked object versus the background.

    ``pixels`` is a (bands, height, width) array of RAW source values (not
    the display stretch - distributions of stretched bytes would be
    meaningless), ``mask`` the (height, width) binary layer. None when the
    mask selects nothing or everything (no comparison to make), or when
    either side is entirely nodata.

    Nodata is excluded from both distributions: a single NaN makes a mean
    NaN, so one row of swath exterior used to turn the whole readout into
    "nan", and a sentinel like -9999 drags the average somewhere
    meaningless. NaN is always excluded; ``nodata`` additionally excludes a
    declared sentinel value.
    """
    from .snippets import nodata_mask

    mask = np.asarray(mask, dtype=bool)
    n_object = int(mask.sum())
    if n_object == 0 or n_object == mask.size:
        return None
    data = np.asarray(pixels, dtype=np.float64)
    invalid = nodata_mask(data, nodata)
    if invalid is not None and invalid.any():
        data = np.where(invalid, np.nan, data)

    inside = data[:, mask]
    outside = data[:, ~mask]
    # A band with nothing valid left on one side has no comparison to
    # report, and nanmean of an empty slice is a warning plus a NaN.
    if (np.isfinite(inside).sum(axis=1).min() == 0
            or np.isfinite(outside).sum(axis=1).min() == 0):
        return None
    with np.errstate(all="ignore"):
        return {
            "pixels_object": n_object,
            "pixels_background": int(mask.size - n_object),
            "object_mean": np.nanmean(inside, axis=1).tolist(),
            "object_std": np.nanstd(inside, axis=1).tolist(),
            "background_mean": np.nanmean(outside, axis=1).tolist(),
            "background_std": np.nanstd(outside, axis=1).tolist(),
        }
