# GeoLabeller

A PyQt5-based desktop application for viewing GeoTIFF images and creating point annotations for machine learning datasets.

## Features

### Image Viewing
- Load individual GeoTIFF files or entire directories
- Organize images into groups with drag-and-drop support
- Toggle layer visibility on/off
- Zoom to specific layers
- Pan and zoom navigation with mouse wheel
- Real-time coordinate display (WGS84) with nearest image info in status bar
- **Scale bar** in the top-right corner showing distance at current zoom level
- **Duplicate detection** prevents loading the same image file twice

### Labeled Images Panel
- Separate panel below the layer panel showing all labeled images
- Labels grouped by **object ID** (linked labels appear together)
- Individual labels displayed with their unique label IDs
- Click to zoom directly to any label location
- Synchronized visibility toggle with main layer panel
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
pip install PyQt5 rasterio numpy
```

## Usage

```bash
python main.py
```

### Keyboard Shortcuts
- `Ctrl+N` - New Project
- `Ctrl+O` - Add GeoTIFF
- `Ctrl+Shift+O` - Add Directory
- `Ctrl+S` - Save Project
- `Ctrl+Shift+P` - Open Project
- `Escape` - Cancel link mode

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
  "version": "2.0",
  "classes": ["car", "building", "tree"],
  "images": [
    {
      "path": "/path/to/image.tif",
      "name": "image",
      "group": "folder/subfolder",
      "labels": [
        {
          "id": 1,
          "class_name": "car",
          "pixel_x": 256.5,
          "pixel_y": 128.3,
          "lon": -73.985,
          "lat": 40.748,
          "object_id": "uuid-v4-string"
        }
      ]
    }
  ]
}
```

## Requirements

- Python 3.10+
- PyQt5
- rasterio
- numpy