# GeoLabeller

A PyQt5-based desktop application for viewing georeferenced and non-georeferenced raster images and creating point annotations for machine learning datasets. Supports GeoTIFF images.


## Supported File Formats

GeoTIFF (`.tif`, `.tiff`), both georeferenced and non-georeferenced, read via rasterio.

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


## Features

### Image Viewing
- Load individual GeoTIFF images or entire directories of them
- **Non-georeferenced image support** — images without a CRS are displayed in a separate pixel zone
- **Async loading** with progress bar for large datasets — UI stays responsive
- **Lazy bounds-only loading** — reads only file headers for fast bulk imports; pixel data loaded on demand
- **Add Directory** creates a root group named after the selected folder
- Organize images into groups with drag-and-drop support
- Toggle layer visibility on/off (layers default to hidden on load)
- **Parent group auto-check**: Turning on a layer automatically enables its parent groups
- Zoom to specific layers
- Pan and zoom navigation with mouse wheel
- Real-time coordinate display (WGS84) with nearest image info in status bar
- **Scale bar** in the top-right corner showing distance at current zoom level
- **Duplicate detection** prevents loading the same image file twice
- **Optimized tile rendering** with O(1) visible tile calculation for smooth performance
- **Auto-save & crash recovery** — project state saved every 60 seconds; automatic recovery prompt on restart after an abnormal shutdown

### Layer Panel
- Hierarchical tree view of all loaded images and groups
- **Expand All / Collapse All** via right-click context menu on groups
- **Select All / Unselect All** children via right-click context menu
- Drag-and-drop to reorganize layers and groups
- Tree collapses by default on project load for cleaner UI
- Visibility changes sync automatically with Labeled Images panel

### Labeled Images Panel
- Separate panel below the layer panel showing all labeled images
- Labels grouped by **object ID** (linked labels appear together)
- Individual labels displayed with their unique label IDs
- Click to zoom directly to any label location
- **Synchronized visibility** toggle with main layer panel (bidirectional)
- Right-click context menu for quick navigation

### Point Labeling
- Create custom label classes with distinct colors
- Switch between Pan and Label modes
- Place point annotations with a single click
- Remove labels via right-click context menu
- Labels store both pixel coordinates and geographic coordinates (WGS84)

### Object Linking
- Link labels across multiple images to track the same real-world object
- Each label has a unique UUID; linked labels share the same UUID
- Right-click menu options: "Link with...", "Unlink", "Show Linked"
- Visual highlighting of linked labels

### Cycle Mode
- Sequential workflow for labeling layers within a selected group
- Select a group, press **C** to enter Cycle mode
- Automatically zooms to the last checked layer in the group
- **Left-click** places labels (same as Label mode)
- **Right-click + drag** to pan around
- **Mouse wheel** to zoom in/out
- **Space** to advance: unchecks current layer and zooms to next
- **Ctrl+Left-click** on a label for context menu (link, remove, etc.)
- Group name displayed in status bar during cycle

### Project Management
- Save/load projects as JSON files (`.geolabel` extension)
- Projects preserve: images, groups, label classes, and all annotations
- Export format includes pixel coordinates, lat/lon, and object IDs

## Installation

```bash
# Create conda environment from environment.yml
conda env create -f environment.yml
conda activate geolabel

# Or install dependencies manually
pip install PyQt5 rasterio numpy pillow
```

## Usage

```bash
python main.py
```

### Keyboard Shortcuts

#### File Operations
| Shortcut | Action |
|----------|--------|
| `Ctrl+N` | New Project |
| `Ctrl+Shift+P` | Open Project |
| `Ctrl+S` | Save Project |
| `Ctrl+Shift+S` | Save Project As |
| `Ctrl+O` | Add Image (GeoTIFF) |
| `Ctrl+Shift+O` | Add Directory |
| `Ctrl+Q` | Exit |

#### Navigation
| Shortcut | Action |
|----------|--------|
| Mouse Wheel | Zoom in/out |
| Click + Drag | Pan (in Pan mode) |
| Right-click | Context menu |

#### Mode Switching
| Shortcut | Action |
|----------|--------|
| `P` | Pan mode |
| `L` | Label mode |
| `C` | Cycle mode (group-based) |
| `V` | View Cycle mode (layers in current view) |

#### Labeling
| Shortcut | Action |
|----------|--------|
| Left-click | Place label (in Label/Cycle mode) |
| Right-click label | Label options (remove, link) |
| `Ctrl`+Left-click | Label options in Cycle mode |
| `1`–`9` | Quick-switch to class 1–9 |
| `Escape` | Cancel link mode |

#### Cycle / View Cycle Mode
| Shortcut | Action |
|----------|--------|
| `Space` | Advance to next layer (unchecks current) |
| `Ctrl+Space` | Go back to previous layer |
| Right-click + drag | Pan around |
| Mouse wheel | Zoom in/out |

#### Help
| Shortcut | Action |
|----------|--------|
| `F1` | Show keyboard shortcuts & tips |

### Layer Panel Features
- **Checkbox** — Toggle layer/group visibility
- **Right-click group** — Select All, Unselect All, Expand All, Collapse All
- **Right-click layer** — Zoom to layer, Remove
- **Drag & Drop** — Reorder layers between groups

### Labeling Workflow
1. Load images via File → Add Image or Add Directory
2. Create label classes via Labels → Edit Classes
3. Select a class from the toolbar dropdown (or press `1`–`9`)
4. Press `L` to enter Label mode
5. Click on images to place point annotations
6. Right-click labels to remove, link, or view linked labels
7. Save project to preserve all work

## Project File Format

Projects are saved as JSON with the following structure:

```json
{
  "version": "2.1",
  "classes": ["car", "building", "tree"],
  "images": [
    {
      "path": "/path/to/image.tif",
      "name": "image",
      "group": "folder/subfolder",
      "original_width": 1024,
      "original_height": 768,
      "labels": [
        {
          "id": 1,
          "unique_id": "uuid-v4-unique-to-label",
          "class_name": "car",
          "pixel_x": 0.25,
          "pixel_y": 0.167,
          "lon": -73.985,
          "lat": 40.748,
          "object_id": "uuid-v4-for-linking"
        }
      ]
    }
  ]
}
```

### Label Fields (v2.1)
- `id`: Sequential integer ID (internal use)
- `unique_id`: UUID v4 string, always unique per label
- `pixel_x`, `pixel_y`: **Percentage** coordinates (0.0-1.0), divide by original image dimensions
- `lon`, `lat`: WGS84 geographic coordinates
- `object_id`: UUID v4 for linking labels across images (shared by linked labels)

## Requirements

- Python 3.10+
- PyQt5
- rasterio
- numpy
- affine