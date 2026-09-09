# GeoLabeller

A PyQt5 desktop application for viewing georeferenced (and non-georeferenced)
raster imagery and building point-annotation datasets for machine learning:
place labels, link them across images, measure and orient objects, paint
per-object masks, and export training data as HDF5 or GeoTIFF crops.

## Installation

**Windows**: download the MSI installer from the
[latest GitHub release](../../releases/latest). It upgrades an existing
install in place.

**From source** (any platform):

```bash
# Create the pinned conda environment
conda env create -f environment.yml
conda activate geolabel

# Or install the dependencies yourself
pip install PyQt5 rasterio numpy pyproj h5py affine

python main.py
```

Runs on FIPS-enabled systems (no md5/sha1 anywhere; the test suite enforces
this).

## Supported File Formats

GeoTIFF (`.tif`, `.tiff`), read via rasterio — georeferenced or not
(non-georeferenced images display in a separate pixel zone), any band count,
uint8 / uint16 / float32 pixels. Non-byte imagery is shown through a
per-image 2–98 percentile contrast stretch; exports can keep the native
values (see below).

## Preparing GeoTIFFs for Fast Rendering

GeoLabeller draws large rasters with tiled, level-of-detail rendering: when you
zoom out it draws from **pyramid overviews** (reduced-resolution copies stored
inside the file) instead of decoding full-resolution pixels, and it loads
overview levels in the background so the UI stays responsive. Files **without**
overviews force the app to read full resolution at every zoom level — the main
cause of slow panning and zooming on large images.

For the fastest experience, give each GeoTIFF **internal tiling** and **internal
overviews** before loading it. The simplest option is a Cloud-Optimized GeoTIFF
(COG), which is tiled and overviewed by definition.

**Recommendations**
- **Internal overviews** with power-of-two decimation factors down to ~256 px on
  the long side — e.g. `2 4 8 16 32 64`.
- **Internal tiling** with 512×512 blocks (256×256 also works well).
- **Overview resampling**: `average` (or `gauss`) for imagery; `nearest` for
  categorical / label rasters.
- **Compression** such as `DEFLATE` (with `PREDICTOR=2`) or `LZW` keeps files
  small without meaningfully slowing rendering.

**Add overviews to an existing file, in place** (GDAL):

```bash
gdaladdo -r average --config COMPRESS_OVERVIEW DEFLATE image.tif 2 4 8 16 32 64
```

**Or convert to a Cloud-Optimized GeoTIFF** (GDAL 3.1+):

```bash
gdal_translate input.tif image_cog.tif -of COG \
  -co COMPRESS=DEFLATE -co PREDICTOR=2 \
  -co BLOCKSIZE=512 -co OVERVIEW_RESAMPLING=AVERAGE
```

**Or with rasterio:**

```python
import rasterio
from rasterio.enums import Resampling

with rasterio.open("image.tif", "r+") as ds:
    ds.build_overviews([2, 4, 8, 16, 32, 64], Resampling.average)
    ds.update_tags(ns="rio_overview", resampling="average")
```

> Very large single images benefit the most: without overviews the first
> zoomed-out view has to decode the entire raster, whereas with overviews the app
> reads only a small decimated level.

Avoid sidecar files (`.tfw`, external `.ovr`, `.aux.xml`) next to the imagery
when you can: a sidecar-free directory imports noticeably faster, because
GeoLabeller can then skip GDAL's per-file directory scan.

## Features

### Viewing and organizing
- Load single GeoTIFFs or whole directories (recursive; folders become
  groups). Bulk imports read only headers — pixels load on demand.
- Layer panel: hierarchical groups with drag-and-drop, tristate group
  checkboxes that mirror their layers, preload/free per group, per-group
  **location tags** (right-click → Set Location…).
- Projects with 10k+ images reopen in seconds: layer geometry is rebuilt
  from metadata stored in the project, with zero file reads.
- A project copied to another machine alongside its imagery relocates every
  image automatically on open; **File → Locate Missing Images** handles
  anything that moved elsewhere.
- Waypoints (named geographic bookmarks), go-to-coordinates, scale bar,
  live WGS84 coordinate readout, auto-save with crash recovery.

### Labeling
- Point labels in custom classes (`1`–`9` quick-switch), placed with a click
  in Label mode or any cycle mode.
- **Linking**: labels of the same real-world object across images share an
  `object_id` — link via right-click, chain-linking (`K`), or box-linking
  (right-click a label → Link by Box…). Linked groups always wear
  per-group colored halo rings, plus a shared editable group name.
- **Measurements**: length/width per object (`M` on a label, or Shift+drag
  to measure any ground distance), stored in metres.
- **Orientation editor** (Labels menu): draw per-label headings on
  snippets; headings can auto-propagate to linked labels through each
  image's own georeferencing.
- **Mask editor** (Labels menu): paint named binary masks over a label's
  snippet (zoom to single-pixel precision, flood-fill closed outlines) and
  compare the object's raw pixel distribution against the background.
  Masks are stored in the project as compact full-image RLE.
- Hard-negative sources: flag whole images that contain confusers but no
  true positives (right-click the image on the canvas).

### Modes
Pan (`P`), Label (`L`), Cycle through a group (`C`), View Cycle (`V`),
Waterfall (`W`) — a group's images stacked vertically, glide with Space,
with labeling and measuring still live — and Ruler (`R`).

### Exports (Export menu)
- **HDF5 Dataset** — sliding-window CNN training data: labeled example
  crops (with an offset ring) plus hard-negative windows, train/val/test
  splits, painted masks and location tags carried per sample. Pixels as
  stretched uint8 or native-scale float32 grayscale. Appendable.
- **Ground Truth** (all or labeled-only) — JSON ground truth.
- **Sub-images** — one GeoTIFF crop per label, source dtype and
  georeferencing preserved.
- **Optimized GeoTIFFs** — re-tile/overview imagery in place for speed.

## Documentation

- **Help → Keyboard Shortcuts (`F1`)** — the complete, always-current key
  reference (the tables live in `app/shortcuts.py`; this README does not
  duplicate them).
- **Help → ICD** — the Interface Control Document
  ([docs/GeoLabeller-ICD.pdf](docs/GeoLabeller-ICD.pdf)): the authoritative
  specification of the `.geolabel` project format (currently format 4.1),
  the mask RLE encoding, every export product, and the coordinate
  conventions. If you are writing a reader or consumer, start there.

## Project Files

Projects are single JSON files (`.geolabel`) holding classes, image
references (with georeferencing metadata), labels, links, measurements,
orientations, masks, waypoints and location tags — everything except the
pixels. The full schema and its version history are specified in the ICD;
old project files always load unchanged.

## Requirements

- Python 3.10+ (the pinned environment uses 3.14)
- PyQt5, rasterio, numpy, pyproj, h5py, affine
