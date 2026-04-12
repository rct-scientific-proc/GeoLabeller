"""File reader registry for custom file format support.

Register custom readers to extend GeoLabel with support for additional file
formats beyond GeoTIFF.  Each reader is a callable that takes a file path and
returns a :class:`ReaderResult` with the image data and optional geo metadata.

Example — registering an HDF5 reader::

    from app.readers import registry, ReaderResult
    import h5py, numpy as np

    def read_hdf5(file_path: str) -> ReaderResult:
        with h5py.File(file_path, "r") as f:
            data = f["image"][:]
        h, w = data.shape[:2]
        if data.ndim == 2:
            rgba = np.zeros((h, w, 4), dtype=np.uint8)
            rgba[:, :, 0] = rgba[:, :, 1] = rgba[:, :, 2] = data
            rgba[:, :, 3] = 255
        else:
            rgba = np.zeros((h, w, 4), dtype=np.uint8)
            rgba[:, :, :3] = data[:, :, :3]
            rgba[:, :, 3] = 255
        return ReaderResult(rgba=rgba, width=w, height=h)

    registry.register(".h5", "custom_hdf5", read_hdf5)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from rasterio.crs import CRS
from affine import Affine


@dataclass
class ReaderResult:
    """Standardised output returned by every custom reader.

    Attributes:
        rgba: RGBA uint8 image array with shape (height, width, 4).
        width: Image width in pixels (after any decimation).
        height: Image height in pixels (after any decimation).
        src_width: Original (on-disk) image width.
        src_height: Original (on-disk) image height.
        crs: Optional CRS of the source image.
        transform: Optional affine transform (pixel → projected coordinates).
        nodata_mask: Optional boolean mask (True = nodata/transparent pixels).
    """

    rgba: np.ndarray
    width: int
    height: int
    src_width: int = 0
    src_height: int = 0
    crs: Optional[CRS] = None
    transform: Optional[Affine] = None
    nodata_mask: Optional[np.ndarray] = None


# Type alias for a reader callable.
# Signature: (file_path: str, decimation_factor: int) -> ReaderResult
ReaderCallable = Callable[[str, int], ReaderResult]


@dataclass
class BoundsResult:
    """Lightweight metadata returned by a bounds-only reader.

    Returned by ``ReaderRegistry.read_bounds()`` when a reader provides a
    fast path that reads only the file header (dimensions, CRS, transform)
    without decoding pixel data.

    Attributes:
        width: Image width in pixels.
        height: Image height in pixels.
        src_width: Original (on-disk) image width.
        src_height: Original (on-disk) image height.
        crs: Optional CRS of the source image.
        transform: Optional affine transform (pixel → projected coordinates).
    """

    width: int
    height: int
    src_width: int = 0
    src_height: int = 0
    crs: Optional[CRS] = None
    transform: Optional[Affine] = None


# Signature: (file_path: str) -> BoundsResult
BoundsCallable = Callable[[str], BoundsResult]


@dataclass
class _ReaderEntry:
    """Internal bookkeeping for a registered reader."""
    name: str
    callback: ReaderCallable
    bounds_callback: Optional[BoundsCallable] = None
    extensions: list[str] = field(default_factory=list)


class ReaderRegistry:
    """Central registry mapping file extensions to reader implementations."""

    def __init__(self) -> None:
        # extension (lowercase, with dot) → _ReaderEntry
        self._by_ext: dict[str, _ReaderEntry] = {}
        # reader name → _ReaderEntry
        self._by_name: dict[str, _ReaderEntry] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        extension: str,
        name: str,
        callback: ReaderCallable,
        bounds_callback: Optional[BoundsCallable] = None,
    ) -> None:
        """Register a reader for a file extension.

        Args:
            extension: File extension *including* the dot, e.g. ``".h5"``.
                       Case-insensitive.
            name: Human-readable reader name (must be unique).
            callback: ``(file_path, decimation_factor) -> ReaderResult``.
            bounds_callback: Optional fast path ``(file_path) -> BoundsResult``
                that reads only header/metadata without decoding pixels.
                Used for lazy loading and async imports.

        Raises:
            ValueError: If the extension is already registered.
        """
        ext = extension.lower()
        if not ext.startswith("."):
            ext = f".{ext}"

        if ext in self._by_ext:
            raise ValueError(
                f"Extension '{ext}' is already registered to reader "
                f"'{self._by_ext[ext].name}'"
            )

        if name in self._by_name:
            entry = self._by_name[name]
            entry.extensions.append(ext)
            if bounds_callback is not None:
                entry.bounds_callback = bounds_callback
        else:
            entry = _ReaderEntry(
                name=name, callback=callback,
                bounds_callback=bounds_callback, extensions=[ext])
            self._by_name[name] = entry

        self._by_ext[ext] = entry

    def unregister(self, extension: str) -> None:
        """Remove a reader registration for *extension*."""
        ext = extension.lower()
        if not ext.startswith("."):
            ext = f".{ext}"
        entry = self._by_ext.pop(ext, None)
        if entry is not None:
            entry.extensions.remove(ext)
            if not entry.extensions:
                self._by_name.pop(entry.name, None)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get_reader(self, file_path: str) -> Optional[_ReaderEntry]:
        """Return the reader entry for *file_path*, or ``None`` for default."""
        ext = Path(file_path).suffix.lower()
        return self._by_ext.get(ext)

    def get_reader_by_name(self, name: str) -> Optional[_ReaderEntry]:
        """Look up a reader by its registered name."""
        return self._by_name.get(name)

    def can_read(self, file_path: str) -> bool:
        """Return ``True`` if a custom reader is registered for this file."""
        return self.get_reader(file_path) is not None

    def read(self, file_path: str, decimation_factor: int = 1) -> ReaderResult:
        """Read *file_path* using its registered reader.

        Raises:
            ValueError: If no reader is registered for this file type.
        """
        entry = self.get_reader(file_path)
        if entry is None:
            raise ValueError(
                f"No reader registered for '{Path(file_path).suffix}'"
            )
        return entry.callback(file_path, decimation_factor)

    def has_bounds_reader(self, file_path: str) -> bool:
        """Return ``True`` if a fast bounds-only reader exists for this file."""
        entry = self.get_reader(file_path)
        return entry is not None and entry.bounds_callback is not None

    def read_bounds(self, file_path: str) -> BoundsResult:
        """Read only metadata/bounds from *file_path* (no pixel data).

        Falls back to the full ``read()`` if no bounds callback is registered,
        copying the relevant fields into a ``BoundsResult``.

        Raises:
            ValueError: If no reader is registered for this file type.
        """
        entry = self.get_reader(file_path)
        if entry is None:
            raise ValueError(
                f"No reader registered for '{Path(file_path).suffix}'"
            )
        if entry.bounds_callback is not None:
            return entry.bounds_callback(file_path)
        # Fallback: do a full read and discard pixel data
        result = entry.callback(file_path, 1)
        return BoundsResult(
            width=result.width,
            height=result.height,
            src_width=result.src_width,
            src_height=result.src_height,
            crs=result.crs,
            transform=result.transform,
        )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def registered_extensions(self) -> list[str]:
        """List of all registered extensions (lowercase, with dot)."""
        return list(self._by_ext.keys())

    @property
    def registered_names(self) -> list[str]:
        """List of all registered reader names."""
        return list(self._by_name.keys())

    def reader_info(self, file_path: str) -> dict[str, str]:
        """Return ``{extension: reader_name}`` dict suitable for ``ImageData.reader``.

        Returns an empty dict when the default (rasterio) reader is used.
        """
        entry = self.get_reader(file_path)
        if entry is None:
            return {}
        ext = Path(file_path).suffix.lower()
        return {ext.lstrip("."): entry.name}

    def file_dialog_filter(self) -> str:
        """Build a Qt file-dialog filter string including custom extensions.

        Returns a string like:
        ``"All Supported (*.tif *.tiff *.h5);;GeoTIFF (*.tif *.tiff);;HDF5 (*.h5)"``
        """
        # Hardcoded defaults (always available via rasterio)
        default_exts = ["*.tif", "*.tiff"]
        custom_groups: list[str] = []
        all_exts = list(default_exts)

        for name, entry in self._by_name.items():
            globs = [f"*{e}" for e in entry.extensions]
            all_exts.extend(globs)
            custom_groups.append(f"{name} ({' '.join(globs)})")

        parts = [f"All Supported ({' '.join(all_exts)})"]
        parts.append(f"GeoTIFF ({' '.join(default_exts)})")
        parts.extend(custom_groups)
        parts.append("All Files (*)")
        return ";;".join(parts)

    def all_extensions(self) -> list[str]:
        """Return all supported extensions (including default .tif/.tiff)."""
        defaults = [".tif", ".tiff"]
        return defaults + [e for e in self._by_ext if e not in defaults]

    def all_glob_patterns(self) -> list[str]:
        """Return glob patterns for all supported file types (e.g. ``['*.tif', '*.h5']``)."""
        return [f"*{e}" for e in self.all_extensions()]


# Singleton registry used throughout the application.
registry = ReaderRegistry()

# Auto-register bundled readers
from app.readers import gim as _gim  # noqa: F401, E402