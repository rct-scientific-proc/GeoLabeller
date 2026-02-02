"""Custom reader for GIM (Georeferenced IMage) binary files.

This reader follows the geolabel custom reader format and can be used
with the GUI's custom reader feature.

This is added as an example of how to load a custom file format into the app.

GIM Binary Format:
    Header (3 doubles, 24 bytes):
        - width: image width in pixels (double)
        - height: image height in pixels (double)
        - channels: number of channels (double)

    GCPs (4 GCPs × 4 values × 8 bytes = 128 bytes):
        - 4 ground control points, each with [x, y, lat, lon] as doubles
        - Stored in row-major order: x0, y0, lat0, lon0, x1, y1, lat1, lon1, ...

    Image Data (width × height × channels × 4 bytes):
        - Float32 pixel values in row-major order (height, width, channels)
        - For grayscale: just (height × width) floats
        - For RGB: (height × width × 3) floats
"""
import struct
import numpy as np

# Reader metadata for dynamic menu generation
DISPLAY_NAME = "GIM Binary"
DEFAULT_EXTENSION = "gim"

# Header size: 3 doubles (width, height, channels)
HEADER_SIZE = 3 * 8  # 24 bytes

# GCPs: 4 control points × 4 values (x, y, lat, lon) × 8 bytes per double
NUM_GCPS = 4
GCP_VALUES = 4  # x, y, lat, lon
GCPS_SIZE = NUM_GCPS * GCP_VALUES * 8  # 128 bytes


def get_gcps(filename: str) -> tuple[list, int, int]:
    """Read only the GCPs and image dimensions (no pixel data).

    This is used for lazy loading to compute bounds without loading
    the full image into memory.

    Args:
        filename: Path to the GIM file.

    Returns:
        Tuple of (gcps, width, height) where:
            - gcps: list of [x, y, lat, lon] for each ground control point
            - width: image width in pixels
            - height: image height in pixels

    Raises:
        ValueError: If file format is invalid.
    """
    with open(filename, "rb") as f:
        # Read header
        header_data = f.read(HEADER_SIZE)
        if len(header_data) < HEADER_SIZE:
            raise ValueError(f"GIM file too small for header: {filename}")

        width, height, channels = struct.unpack("<ddd", header_data)
        width = int(width)
        height = int(height)

        # Read GCPs
        gcps_data = f.read(GCPS_SIZE)
        if len(gcps_data) < GCPS_SIZE:
            raise ValueError(f"GIM file too small for GCPs: {filename}")

        # Unpack GCPs (4 GCPs × 4 doubles each)
        gcp_values = struct.unpack(f"<{NUM_GCPS * GCP_VALUES}d", gcps_data)

        gcps = []
        for i in range(NUM_GCPS):
            offset = i * GCP_VALUES
            x = gcp_values[offset]
            y = gcp_values[offset + 1]
            lat = gcp_values[offset + 2]
            lon = gcp_values[offset + 3]
            gcps.append([x, y, lat, lon])

        return gcps, width, height


def reader(filename: str) -> tuple[np.ndarray, list]:
    """Read a GIM file and return image data with GCPs.

    Args:
        filename: Path to the GIM file.

    Returns:
        Tuple of (image_data, gcps) where:
            - image_data: numpy array of shape (H, W) or (H, W, C)
            - gcps: list of [x, y, lat, lon] for each ground control point

    Raises:
        ValueError: If file format is invalid.
    """
    with open(filename, "rb") as f:
        # Read header
        header_data = f.read(HEADER_SIZE)
        if len(header_data) < HEADER_SIZE:
            raise ValueError(f"GIM file too small for header: {filename}")

        width, height, channels = struct.unpack("<ddd", header_data)
        width = int(width)
        height = int(height)
        channels = int(channels)

        # Read GCPs
        gcps_data = f.read(GCPS_SIZE)
        if len(gcps_data) < GCPS_SIZE:
            raise ValueError(f"GIM file too small for GCPs: {filename}")

        # Unpack GCPs (4 GCPs × 4 doubles each)
        gcp_values = struct.unpack(f"<{NUM_GCPS * GCP_VALUES}d", gcps_data)

        gcps = []
        for i in range(NUM_GCPS):
            offset = i * GCP_VALUES
            x = gcp_values[offset]
            y = gcp_values[offset + 1]
            lat = gcp_values[offset + 2]
            lon = gcp_values[offset + 3]
            gcps.append([x, y, lat, lon])

        # Read image data
        num_pixels = width * height * channels
        expected_size = num_pixels * 4  # float32 = 4 bytes
        image_data = f.read(expected_size)

        if len(image_data) < expected_size:
            raise ValueError(
                f"GIM file image data too small: expected {expected_size} bytes, " f"got {
                    len(image_data)} bytes")

        # Convert to numpy array
        pixels = np.frombuffer(image_data, dtype=np.float32)

        # Reshape to image dimensions
        if channels == 1:
            image = pixels.reshape((height, width))
        else:
            image = pixels.reshape((height, width, channels))

        # Normalize to 0-255 range for display
        # Assume float data is in 0-1 range, scale to 0-255
        if image.max() <= 1.0:
            image = (image * 255).astype(np.uint8)
        else:
            # If data is already in a larger range, normalize
            image = ((image - image.min()) / (image.max() -
                     image.min() + 1e-8) * 255).astype(np.uint8)

        return image, gcps
