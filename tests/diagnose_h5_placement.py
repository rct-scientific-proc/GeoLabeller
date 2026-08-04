"""Check whether an HDF5 dataset's examples are centred on a project's labels.

For every label in the project, the snippet the current export rule would cut
(centred on the label) is read straight from the source GeoTIFF and searched
for in the dataset, pixel for pixel. A dataset built by the current rule
contains every one of them; one built by an older rule (or stale snippets from
an append) does not, and the attribute fingerprint says which rule wrote it.

Usage:
    python tests/diagnose_h5_placement.py dataset.h5 project.geolabel
"""
import argparse
import os
import sys

import numpy as np
import h5py
import rasterio
from rasterio.windows import Window

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.labels import LabelProject  # noqa: E402
from app.h5_export import _window_pixels  # noqa: E402


def expected_crop(src, px, py, width, height, channels):
    """The snippet the current rule would cut for a label at (px, py)."""
    x0 = min(max(int(round(px - width / 2.0)), 0), src.width - width)
    y0 = min(max(int(round(py - height / 2.0)), 0), src.height - height)
    return _window_pixels(src, Window(x0, y0, width, height), channels,
                          src.nodata), (x0, y0)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("h5", help="the exported dataset")
    parser.add_argument("project", help="the .geolabel it was exported from")
    args = parser.parse_args()

    project = LabelProject.load(args.project)
    with h5py.File(args.h5, "r") as f:
        attrs = dict(f.attrs)
        images = f["images"]
        n, height, width, channels = images.shape
        gt = f["gt"][:]
        # Positives only; load in bulk (fine for spot-checking datasets).
        pos_idx = np.where(gt)[0]
        positives = images[:][pos_idx] if len(pos_idx) else np.empty(
            (0, height, width, channels), dtype="uint8")

    print(f"{os.path.basename(args.h5)}: {n} samples, "
          f"{len(pos_idx)} examples, {height}x{width}x{channels}")

    # Which export rule wrote this file last.
    has_new = "positive_offset" in attrs
    has_old = "object_radius" in attrs
    if has_new:
        print(f"  written by the current rule "
              f"(positive_offset={int(attrs['positive_offset'])})")
    elif has_old:
        print(f"  WRITTEN BY THE OLD GRID RULE "
              f"(object_radius={int(attrs['object_radius'])}) - re-export to "
              "a fresh file to get centred examples")
    else:
        print("  written before extraction settings were recorded - almost "
              "certainly the old grid rule; re-export to a fresh file")

    found = missing = no_image = 0
    for path, image in project.images.items():
        if not image.labels:
            continue
        if not os.path.exists(path):
            print(f"  ! source missing: {path}")
            no_image += len(image.labels)
            continue
        with rasterio.open(path) as src:
            for label in image.labels:
                crop, (x0, y0) = expected_crop(
                    src, label.pixel_x, label.pixel_y, width, height, channels)
                if crop is None:
                    continue
                hit = np.any(np.all(positives == crop[np.newaxis], axis=(1, 2, 3)))
                if hit:
                    found += 1
                else:
                    missing += 1
                    print(f"  MISSING centred snippet for label "
                          f"{label.id} ({label.class_name}) at "
                          f"pixel ({label.pixel_x:.1f}, {label.pixel_y:.1f}) "
                          f"of {os.path.basename(path)} [crop {x0},{y0}]")

    print(f"\ncentred snippets present: {found}, missing: {missing}"
          + (f", unresolvable (source gone): {no_image}" if no_image else ""))
    if missing and (has_old or not has_new):
        print("The missing snippets are expected for a file written by the "
              "old rule.\nExport the same project to a NEW .h5 and re-run "
              "this check - it should then find every one.")
    elif missing:
        print("A current-rule file should contain every centred snippet - "
              "if sources or labels changed since the export, re-export "
              "first; otherwise this points at a real defect worth a look.")


if __name__ == "__main__":
    main()
