"""Built-in custom readers for geolabel.

Each module in this package should expose:

1. `reader(filename)` - Required. Reads full image data and GCPs:

    def reader(filename) -> tuple[np.ndarray, list]:
        '''Read a file and return (image_data, gcps).

        Args:
            filename: Path to the file to read.

        Returns:
            Tuple of (image_data, gcps) where:
                - image_data: numpy array (H, W) or (H, W, C) for grayscale/RGB/RGBA
                - gcps: list of [x, y, lat, lon] ground control points (at least 3)
        '''
        ...

2. `get_gcps(filename)` - Required. Returns only GCPs and image dimensions for lazy loading:

    def get_gcps(filename) -> tuple[list, int, int]:
        '''Read only the GCPs and image dimensions (no pixel data).

        This is used for lazy loading to compute bounds without loading
        the full image into memory.

        Args:
            filename: Path to the file to read.

        Returns:
            Tuple of (gcps, width, height) where:
                - gcps: list of [x, y, lat, lon] ground control points
                - width: image width in pixels
                - height: image height in pixels
        '''
        ...

3. Module-level metadata constants (optional but recommended):

    DISPLAY_NAME = "My Reader"  # Human-readable name for menus
    DEFAULT_EXTENSION = "dat"   # Default file extension (without dot)
"""
import importlib
import importlib.util
from pathlib import Path
from typing import Callable


def _discover_builtin_readers() -> list[str]:
    """Discover all reader modules in the app/readers directory.

    Returns:
        List of module names (without .py extension).
    """
    readers_dir = Path(__file__).parent
    readers = []

    for py_file in readers_dir.glob("*.py"):
        # Skip __init__.py and any private modules
        if py_file.name.startswith("_"):
            continue

        module_name = py_file.stem

        # Verify the module has a reader function
        try:
            module = importlib.import_module(
                f".{module_name}", package="app.readers")
            if hasattr(module, "reader"):
                readers.append(module_name)
        except ImportError:
            # Skip modules that can't be imported
            pass

    return sorted(readers)


def get_reader(name_or_path: str) -> Callable[[str], tuple]:
    """Load a reader function by name or path.

    Args:
        name_or_path: Either a built-in reader name (e.g., "custom_hdf5")
                      or a path to a Python script (e.g., "./my_reader.py")

    Returns:
        The reader function.

    Raises:
        ValueError: If the reader cannot be loaded.
    """
    # Check if it's a path (contains separator or ends with .py)
    if "/" in name_or_path or "\\" in name_or_path or name_or_path.endswith(
            ".py"):
        # External script path
        from ..custom_reader import load_reader_function
        return load_reader_function(name_or_path)

    # Built-in reader
    try:
        module = importlib.import_module(
            f".{name_or_path}", package="app.readers")
        if not hasattr(module, "reader"):
            raise ValueError(
                f"Reader module '{name_or_path}' has no 'reader' function")
        return module.reader
    except ImportError as e:
        raise ValueError(
            f"Could not load built-in reader '{name_or_path}': {e}")


def get_gcps_func(name_or_path: str) -> Callable[[str], tuple] | None:
    """Load a get_gcps function by reader name or path.

    Args:
        name_or_path: Either a built-in reader name (e.g., "custom_hdf5")
                      or a path to a Python script (e.g., "./my_reader.py")

    Returns:
        The get_gcps function, or None if not available.
    """
    # Check if it's a path (contains separator or ends with .py)
    if "/" in name_or_path or "\\" in name_or_path or name_or_path.endswith(
            ".py"):
        # External script path
        from ..custom_reader import load_gcps_function
        return load_gcps_function(name_or_path)

    # Built-in reader
    try:
        module = importlib.import_module(
            f".{name_or_path}", package="app.readers")
        if hasattr(module, "get_gcps"):
            return module.get_gcps
        return None
    except ImportError:
        return None


def list_builtin_readers() -> list[str]:
    """Return list of available built-in reader names."""
    return _discover_builtin_readers()


def get_reader_info(name: str) -> dict:
    """Get metadata about a built-in reader.

    Args:
        name: The reader module name.

    Returns:
        Dict with keys:
            - name: module name
            - display_name: human-readable name for menus
            - extension: default file extension (without dot)
    """
    try:
        module = importlib.import_module(f".{name}", package="app.readers")

        # Get metadata with defaults
        display_name = getattr(module, "DISPLAY_NAME", name)
        extension = getattr(module, "DEFAULT_EXTENSION", "dat")

        return {
            "name": name,
            "display_name": display_name,
            "extension": extension,
        }
    except ImportError:
        return {
            "name": name,
            "display_name": name,
            "extension": "dat",
        }
