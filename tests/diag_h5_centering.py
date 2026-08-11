"""Diagnose H5 snippet centring against the source rasters.

Reads a GeoLabeller project file, and for every label recomputes the centred
snippet DIRECTLY from the source GeoTIFF - exactly the window the sub-image
GeoTIFF export writes - then looks for that crop inside an exported .h5.

It answers three questions in one run:

1. Are the label pixel coordinates sane? (printed per label, with where the
   label lands inside its centred crop - half the snippet size means centred)
2. Does the .h5 actually contain the correctly centred crop for each label?
3. Does the .h5 contain STALE rows - snippets that match no current label,
   left over from earlier exports appended into the same file?

Usage:
    python tests/diag_h5_centering.py --project my.geoproj --h5 dataset.h5
    python tests/diag_h5_centering.py --project my.geoproj --h5 dataset.h5 --dump out_dir

``--dump`` writes each label's expected crop and the closest .h5 example as
PNGs side by side, so a mismatch can be seen rather than inferred.

Requires: rasterio, numpy, h5py (+ pillow for --dump).
"""
import argparse
import json
import sys
from pathlib import Path

import h5py
import numpy as np
import rasterio
from rasterio.windows import Window

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.h5_export import centered_window, _band_scaling, _window_pixels


def locate_snippet(src, snippet, max_dim: int = 256):
    """Find where a snippet actually came from in the source raster.

    Template-matches the snippet against a decimated copy of band 1 using
    normalised cross-correlation, so it works even when the snippet's pixel
    values were scaled differently from the source (a contrast stretch, or the
    old modulo-wrapped uint8 cast). Returns ``(x, y, score, step)`` - the
    best-matching top-left corner in source pixels, the correlation in
    [-1, 1], and the search precision in pixels.
    """
    step = max(1, int(np.ceil(max(src.width, src.height) / max_dim)))
    base = np.asarray(src.read(1, out_shape=(max(1, src.height // step),
                                  max(1, src.width // step)))).astype("float32")
    tmpl = snippet[::step, ::step, 0].astype("float32")
    th, tw = tmpl.shape
    if th < 2 or tw < 2 or base.shape[0] < th or base.shape[1] < tw:
        return None
    tmpl = tmpl - tmpl.mean()
    tnorm = float(np.sqrt((tmpl ** 2).sum()))
    if tnorm == 0:
        return None
    from numpy.lib.stride_tricks import sliding_window_view
    win = sliding_window_view(base, (th, tw))
    # Correlate in blocks of rows to keep the materialised window small.
    best = (-2.0, 0, 0)
    for r in range(win.shape[0]):
        block = win[r].reshape(win.shape[1], -1).astype("float32")
        block = block - block.mean(axis=1, keepdims=True)
        norms = np.sqrt((block ** 2).sum(axis=1))
        norms[norms == 0] = np.inf
        scores = (block @ tmpl.ravel()) / (norms * tnorm)
        c = int(np.argmax(scores))
        if scores[c] > best[0]:
            best = (float(scores[c]), c * step, r * step)
    return best[1], best[2], best[0], step


def load_project(path: Path):
    """Yield (image_path, width, height, [(label_id, class, px, py), ...])."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    version = str(data.get("version", "1.0"))
    for img in data.get("images", []):
        w = int(img.get("original_width", 0) or 0)
        h = int(img.get("original_height", 0) or 0)
        labels = []
        for lab in img.get("labels", []):
            raw_x = lab.get("pixel_x", lab.get("x", 0))
            raw_y = lab.get("pixel_y", lab.get("y", 0))
            # Mirrors PointLabel.from_dict: 2.1+ stores fractions of the image.
            if version >= "2.1" and w > 0 and h > 0:
                px, py = raw_x * w, raw_y * h
            else:
                px, py = raw_x, raw_y
            labels.append((lab.get("id"), lab.get("class_name", "?"),
                           float(px), float(py)))
        if labels:
            yield img.get("path", ""), w, h, labels


def main() -> int:
    """Compare every label's expected centred crop with the exported .h5."""
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, type=Path,
                    help="GeoLabeller project file.")
    ap.add_argument("--h5", required=True, type=Path,
                    help="Exported HDF5 dataset to check.")
    ap.add_argument("--dump", type=Path,
                    help="Directory to write expected/actual crops as PNGs.")
    ap.add_argument("--locate", type=int, default=0, metavar="N",
                    help="Template-match the first N example snippets back "
                         "against the source raster to find the window the "
                         "export actually used (works even if the pixel "
                         "values were scaled differently).")
    args = ap.parse_args()

    with h5py.File(args.h5, "r") as f:
        images = f["images"][:]
        gt = f["gt"][:] if "gt" in f else np.ones(len(images), bool)
        classes = [c.decode() if isinstance(c, bytes) else str(c)
                   for c in f["classes"][:]] if "classes" in f else []
        labels_ds = f["labels"][:] if "labels" in f else None
        n, H, W = images.shape[0], images.shape[1], images.shape[2]
        C = images.shape[3] if images.ndim == 4 else 1
        attrs = {k: f.attrs[k] for k in f.attrs}
    ex_idx = [i for i in range(n) if gt[i]]
    print(f"h5: {n} snippets ({len(ex_idx)} examples) of {W}x{H}x{C}")
    print(f"    attrs: { {k: attrs[k] for k in sorted(attrs)} }\n")

    if args.dump:
        args.dump.mkdir(parents=True, exist_ok=True)

    matched_rows, total, centred, found = set(), 0, 0, 0
    for img_path, iw, ih, labels in load_project(args.project):
        p = Path(img_path)
        if not p.exists():
            print(f"!! missing image {img_path}")
            continue
        with rasterio.open(p) as src:
            scaling = _band_scaling(src)
            note = ""
            if (iw and src.width != iw) or (ih and src.height != ih):
                note = (f"  ** project says {iw}x{ih} but file is "
                        f"{src.width}x{src.height} - label coords will be "
                        "WRONG **")
            print(f"{p.name}  {src.width}x{src.height}  dtype={src.dtypes[0]}"
                  f"  labels={len(labels)}{note}")
            for lid, cname, px, py in labels:
                total += 1
                x0, y0 = centered_window(px, py, W, H, src.width, src.height)
                inx, iny = px - x0, py - y0
                is_centred = abs(inx - W // 2) <= 1 and abs(iny - H // 2) <= 1
                centred += is_centred
                expect = _window_pixels(
                    src, Window(x0, y0, W, H), C, src.nodata, scaling=scaling)
                # Find this crop among the .h5 examples.
                hit = None
                if expect is not None:
                    # Prefer a row not already claimed: two labels on identical
                    # ground produce identical crops, and each should map to
                    # its own row rather than both to the first match.
                    for i in ex_idx:
                        if i not in matched_rows and np.array_equal(
                                images[i], expect):
                            hit = i
                            matched_rows.add(i)
                            break
                found += hit is not None
                edge = "" if is_centred else "  (shifted off an image edge)"
                cls = (classes[int(labels_ds[hit])]
                       if hit is not None and labels_ds is not None
                       and classes else "")
                print(f"   label {lid} '{cname}': px=({px:.1f},{py:.1f}) "
                      f"-> window ({x0},{y0}), label at ({inx:.1f},{iny:.1f}) "
                      f"of {W}x{H}{edge}  h5 row: "
                      f"{hit if hit is not None else 'NOT FOUND'}"
                      f"{' [' + cls + ']' if cls else ''}")
                if args.dump and expect is not None:
                    from PIL import Image
                    arr = expect[:, :, 0] if C == 1 else expect[:, :, :3]
                    Image.fromarray(arr).save(
                        args.dump / f"label{lid}_expected.png")
                    if hit is not None:
                        a2 = (images[hit][:, :, 0] if C == 1
                              else images[hit][:, :, :3])
                        Image.fromarray(a2).save(
                            args.dump / f"label{lid}_h5row{hit}.png")

            # Where did the .h5's own snippets actually come from?
            if args.locate:
                print(f"   -- locating the first {args.locate} example "
                      "snippet(s) of this image in the source --")
                expected_origins = {
                    centered_window(px, py, W, H, src.width, src.height)
                    for _lid, _c, px, py in labels}
                for i in ex_idx[:args.locate]:
                    found_at = locate_snippet(src, images[i])
                    if found_at is None:
                        print(f"      h5 row {i}: could not locate")
                        continue
                    fx, fy, score, stp = found_at
                    near = min((abs(fx - ex) + abs(fy - ey), (ex, ey))
                               for ex, ey in expected_origins)
                    if score < 0.5:
                        verdict = ("LOW SCORE - the pixel VALUES don't match "
                                   "the source, so this row was written with a "
                                   "different dtype conversion (an old export "
                                   "before the contrast-stretch fix). Its "
                                   "location above is unreliable.")
                    elif near[0] <= 2 * stp:
                        verdict = "matches the expected centred window - OK"
                    else:
                        verdict = ("WRONG WINDOW - values match the source but "
                                   "the crop was taken somewhere else.")
                    print(f"      h5 row {i}: matches source window "
                          f"~({fx},{fy}) +/-{stp}px (score {score:.3f}); "
                          f"nearest expected centre window {near[1]}, "
                          f"off by ~{near[0]}px\n"
                          f"          -> {verdict}")
                if args.dump:
                    from PIL import Image
                    for i in ex_idx[:args.locate]:
                        a2 = (images[i][:, :, 0] if C == 1
                              else images[i][:, :, :3])
                        Image.fromarray(a2).save(
                            args.dump / f"h5row{i}_actual.png")

    stale = [i for i in ex_idx if i not in matched_rows]
    print(f"\nlabels: {total} | centred (not edge-shifted): {centred} | "
          f"present in h5: {found}")
    print(f"h5 example rows matching no current label: {len(stale)}"
          f"{' <-- STALE ROWS from earlier appends' if stale else ''}")
    if found < total:
        print("\nSome labels are missing from the h5. Likely causes: the "
              "export scope skipped that image, the snippet size differs "
              "from this h5, or the file was written before the label "
              "existed.")
    if stale:
        print("Re-export to a BRAND NEW .h5 - appending keeps every snippet "
              "ever written, including ones from earlier (buggy) runs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
