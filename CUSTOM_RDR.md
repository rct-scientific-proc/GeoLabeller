# Custom Reader Function

This file explains the expected shape and behavior of a custom reader function for geolabel.

## Requirements

- The reader must be a function named `reader` with the signature: `reader(filename)`.
- It must return a 2-tuple: `(image_data, gcps)`.
  - **`image_data`**: a NumPy array representing the image in row-major order. Valid shapes are `(H, W)` for grayscale or `(H, W, C)` for color images where `C` is 3 (RGB) or 4 (RGBA).
  - **`gcps`**: a list of ground control points. Each GCP is a list/tuple with four numeric values `[x, y, lat, lon]` where:
    - `x`: pixel x coordinate (column, 0-based)
    - `y`: pixel y coordinate (row, 0-based)
    - `lat`: latitude in decimal degrees
    - `lon`: longitude in decimal degrees

## Notes

- Provide at least 4 well-distributed GCPs for reliable georeferencing (more is allowed).
- The function should raise an informative exception if it cannot read the file.

## Minimal Example

```python
import numpy as np
from PIL import Image

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

| Return        | Type / Shape                            | Notes                                    |
|---------------|-----------------------------------------|------------------------------------------|
| `image_data`| `np.ndarray` - `(H, W)` or `(H, W, C)` | dtype typically `uint8` or `float32` |
| `gcps`      | `list[list[float]]` - at least 4 items    | Coordinates in decimal degrees (WGS 84)  |
