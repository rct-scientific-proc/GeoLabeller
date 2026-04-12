# GeoLabeller

A PyQt5-based desktop application for viewing georeferenced and non-georeferenced raster images and creating point annotations for machine learning datasets. Supports GeoTIFF out of the box, with a plugin-style reader registry for adding custom file formats (e.g. GIM, HDF5).

## Features

### Image Viewing
- Load individual images or entire directories (GeoTIFF + any registered custom format)
- **Custom file reader registry** — register readers for new formats with a few lines of Python (see [Extending with Custom Readers](#extending-with-custom-readers))
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
- `Ctrl+N` - New Project
- `Ctrl+O` - Add GeoTIFF
- `Ctrl+Shift+O` - Add Directory (creates root group from folder name)
- `Ctrl+S` - Save Project
- `Ctrl+Shift+S` - Save Project As
- `Ctrl+Shift+P` - Open Project
- `P` - Pan mode
- `L` - Label mode
- `C` - Cycle mode
- `Space` - Next layer (in Cycle mode)
- `F1` - Show Help/Shortcuts
- `Escape` - Cancel link mode

### Layer Panel Features
- **Checkbox** - Toggle layer/group visibility
- **Right-click group** - Select All, Unselect All, Expand All, Collapse All
- **Right-click layer** - Zoom to layer, Remove
- **Drag & Drop** - Reorder layers between groups

### Labeling Workflow
1. Load GeoTIFF images via File menu
2. Create label classes via Labels → Edit Classes
3. Select a class from the toolbar dropdown
4. Click the Label Mode button (or use the Labels menu)
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

### Image Fields
- `reader`: Dict mapping file extension to reader name (e.g. `{"gim": "gim_reader"}`). Empty or `{"tif": "default"}` for standard GeoTIFF.

## Extending with Custom Readers

GeoLabel supports loading arbitrary raster formats via the reader registry in `app/readers/`. To add a new format:

1. Create a new module in `app/readers/` (e.g. `app/readers/myformat.py`).
2. Implement a read function returning a `ReaderResult` and, optionally, a fast bounds-only function returning a `BoundsResult`.
3. Register the reader at module level.
4. Import the module from `app/readers/__init__.py`.

```python
# app/readers/myformat.py
import numpy as np
from app.readers import registry, ReaderResult, BoundsResult

def read_myformat_bounds(file_path: str) -> BoundsResult:
    """Fast path: read only header metadata (no pixel data)."""
    # ... parse header for width, height, CRS, transform ...
    return BoundsResult(width=w, height=h, src_width=w, src_height=h,
                        crs=my_crs, transform=my_affine)

def read_myformat(file_path: str, decimation_factor: int = 1) -> ReaderResult:
    """Full read: decode pixels into an RGBA uint8 array."""
    # ... load and convert image data ...
    return ReaderResult(rgba=rgba, width=w, height=h,
                        src_width=orig_w, src_height=orig_h,
                        crs=my_crs, transform=my_affine)

registry.register(".myf", "my_format", read_myformat,
                   bounds_callback=read_myformat_bounds)
```

The file dialog, directory scanner, async loader, and project persistence will automatically pick up the new extension.

## Supported File Formats

| Format | Extension | Reader | Notes |
|--------|-----------|--------|-------|
| GeoTIFF | `.tif`, `.tiff` | Built-in (rasterio) | Georeferenced and non-georeferenced |
| GIM | `.gim` | `gim_reader` | Binary format with GCPs; see `app/readers/gim.py` |

## Requirements

- Python 3.10+
- PyQt5
- rasterio
- numpy
- affine