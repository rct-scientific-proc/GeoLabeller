# Custom Reader Function

This file explains the expected shape and behavior of a custom reader function for geolabel.

## Required Functions

### 1. `reader(filename)` - Required

The main reader function that loads the full image data and GCPs.

- **Signature**: `reader(filename) -> tuple[np.ndarray, list]`
- **Returns**: A 2-tuple `(image_data, gcps)`:
  - **`image_data`**: a NumPy array representing the image in row-major order. Valid shapes are `(H, W)` for grayscale or `(H, W, C)` for color images where `C` is 3 (RGB) or 4 (RGBA).
  - **`gcps`**: a list of ground control points. Each GCP is a list/tuple with four numeric values `[x, y, lat, lon]` where:
    - `x`: pixel x coordinate (column, 0-based)
    - `y`: pixel y coordinate (row, 0-based)
    - `lat`: latitude in decimal degrees
    - `lon`: longitude in decimal degrees

### 2. `get_gcps(filename)` - Required

A lightweight function that returns only the GCPs and image dimensions without loading pixel data. This enables efficient lazy loading for operations like "Zoom to Layer" without loading the full image.

- **Signature**: `get_gcps(filename) -> tuple[list, int, int]`
- **Returns**: A 3-tuple `(gcps, width, height)`:
  - **`gcps`**: list of ground control points (same format as above)
  - **`width`**: image width in pixels
  - **`height`**: image height in pixels

## Notes

- Provide at least 4 well-distributed GCPs for reliable georeferencing (more is allowed).
- The functions should raise an informative exception if they cannot read the file.

## Minimal Example

```python
import numpy as np
from PIL import Image

def get_gcps(filename):
    """Read only the GCPs and image dimensions (no pixel data).
    
    This is used for lazy loading to compute bounds without loading
    the full image into memory.
    """
    # Get image dimensions without loading full data
    img = Image.open(filename)
    width, height = img.size
    img.close()
    
    # Return GCPs and dimensions
    gcps = [
        [0, 0, 40.0000, -105.0000],
        [width - 1, 0, 40.0000, -104.9900],
        [width - 1, height - 1, 39.9900, -104.9900],
        [0, height - 1, 39.9900, -105.0000],
    ]
    return gcps, width, height

def reader(filename):
    """Read `filename` and return (image_data, gcps).

    Replace the GCPs below with values read from your file's metadata.
    """
    # Load image (Pillow used here as a simple example loader)
    img = Image.open(filename)
    image_data = np.array(img)

    # Example ground control points - replace with real values.
    # Format: [x_pixel, y_pixel, latitude, longitude]
    gcps = [
        [0, 0, 40.0000, -105.0000],                                           # top-left
        [image_data.shape[1] - 1, 0, 40.0000, -104.9900],                     # top-right
        [image_data.shape[1] - 1, image_data.shape[0] - 1, 39.9900, -104.9900],  # bottom-right
        [0, image_data.shape[0] - 1, 39.9900, -105.0000],                     # bottom-left
    ]

    return image_data, gcps
```

## Advanced Readers

- If your file contains embedded georeferencing (GeoTIFF, world file, etc.), read that metadata and construct accurate GCPs.
- You may use libraries such as `rasterio` or `GDAL`; just ensure those dependencies are installed in your environment.

## Return Expectations

| Function      | Return Type                              | Notes                                    |
|---------------|------------------------------------------|------------------------------------------|
| `reader`      | `(np.ndarray, list[list[float]])`        | Image array + GCPs                       |
| `get_gcps`    | `(list[list[float]], int, int)`          | GCPs + width + height (for lazy loading) |

| Field         | Type / Shape                            | Notes                                    |
|---------------|-----------------------------------------|------------------------------------------|
| `image_data`  | `np.ndarray` - `(H, W)` or `(H, W, C)`  | dtype typically `uint8` or `float32`     |
| `gcps`        | `list[list[float]]` - at least 4 items  | Coordinates in decimal degrees (WGS 84)  |
| `width`       | `int`                                   | Image width in pixels                    |
| `height`      | `int`                                   | Image height in pixels                   |
