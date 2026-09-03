"""Label data model and storage for point annotations."""
import json
import math
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from affine import Affine
from pyproj import Transformer
from rasterio.crs import CRS

# WGS84 CRS (EPSG:4326)
WGS84 = CRS.from_epsg(4326)

# Earth's mean radius in meters (WGS84)
EARTH_RADIUS_M = 6371008.8


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate geodesic distance between two WGS84 points using Haversine formula.

    Args:
        lat1, lon1: First point (degrees)
        lat2, lon2: Second point (degrees)

    Returns:
        Distance in meters
    """
    # Convert to radians
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    # Haversine formula
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_M * c


@dataclass
class PointLabel:
    """A single point label annotation."""

    # Unique identifier (sequential, used internally)
    id: int

    # Class/category name
    class_name: str

    # Pixel coordinates relative to the image (absolute pixel values)
    pixel_x: float  # column (x)
    pixel_y: float  # row (y)

    # Coordinates in WGS84
    lon: float
    lat: float

    # Unique ID for this specific label (UUID v4) - always unique per label
    unique_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Object ID for linking labels across images (UUID v4)
    # Linked labels share the same object_id
    object_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Physical dimensions in meters (user-supplied or auto-measured).
    # None means the dimension has not been set for this label.
    length_m: Optional[float] = None
    width_m: Optional[float] = None

    # Free text describing this particular labelled object.
    #
    # Deliberately per-label, not per-object: linking labels makes them the
    # same object seen in different images, and what is worth writing down is
    # usually what differs between those views. Nothing copies this across an
    # object group - see MainWindow._on_label_measured, which does copy the
    # measurements when the user asks it to.
    description: str = ""

    # The object's orientation, drawn by the user in the orientation editor.
    #
    # orientation_px_rad: unit-circle principal angle of the drawn
    # start->end vector in un-warped source-pixel space, radians in
    # (-pi, pi] (x = +column, y = up the columns; top-right to bottom-left
    # is about -3*pi/4). orientation_deg: the same vector as a TRUE-north
    # heading, degrees clockwise in [0, 360); None for non-geo imagery.
    # Per label even when linked - two views of one object are two
    # measurements, and their disagreement is signal.
    orientation_px_rad: Optional[float] = None
    orientation_deg: Optional[float] = None
    # True when this orientation was NOT drawn by hand but propagated from a
    # linked label: the group's shared true-north heading was re-derived into
    # THIS image's pixel space through its georeferencing. Cleared the moment
    # the user draws over it. Lets consumers (and the editor's colouring)
    # tell measured orientations from inherited ones.
    orientation_derived: bool = False

    # Named binary masks painted on this label's snippet in the mask editor.
    # Each entry is a dict {name, x0, y0, width, height, rle}: the window is
    # the snippet crop IN SOURCE PIXELS at paint time, the rle a row-major
    # run-length encoding of the binary layer (see app/masks.py for the
    # exact encoding). Masks are independent layers - several may overlap on
    # one snippet - and per label, like every other per-view annotation.
    masks: list = field(default_factory=list)

    # Human-readable name for the linked-object group - the readable
    # companion to object_id's UUID. The exact opposite contract to
    # description: this MUST always be identical across every label sharing
    # an object_id. Only LabelProject.set_group_id may change it (it applies
    # the change to the whole group), and link/unlink keep it consistent.
    group_id: str = ""

    def to_dict(self, image_width: int = 0, image_height: int = 0) -> dict:
        """Convert to dictionary for serialization.

        Args:
            image_width: Original image width for percentage calculation
            image_height: Original image height for percentage calculation
        """
        # Calculate percentage coordinates if dimensions are provided
        if image_width > 0 and image_height > 0:
            pct_x = self.pixel_x / image_width
            pct_y = self.pixel_y / image_height
        else:
            # Fallback to absolute if dimensions unknown
            pct_x = self.pixel_x
            pct_y = self.pixel_y

        d = {
            "id": self.id,
            "unique_id": self.unique_id,
            "class_name": self.class_name,
            "pixel_x": pct_x,
            "pixel_y": pct_y,
            "lon": self.lon,
            "lat": self.lat,
            "object_id": self.object_id
        }
        if self.length_m is not None:
            d["length_m"] = self.length_m
        if self.width_m is not None:
            d["width_m"] = self.width_m
        # Written only when set, so projects without descriptions are
        # unchanged and older readers see exactly what they saw before.
        if self.description:
            d["description"] = self.description
        if self.group_id:
            d["group_id"] = self.group_id
        if self.orientation_px_rad is not None:
            d["orientation_px_rad"] = self.orientation_px_rad
        if self.orientation_deg is not None:
            d["orientation_deg"] = self.orientation_deg
        if self.orientation_derived:
            d["orientation_derived"] = True
        if self.masks:
            d["masks"] = self.masks
        return d

    @classmethod
    def from_dict(cls, data: dict, image_width: int = 0, image_height: int = 0,
                  version: str = "2.1") -> "PointLabel":
        """Create from dictionary.

        Args:
            data: Dictionary with label data
            image_width: Original image width for converting percentages back to pixels
            image_height: Original image height for converting percentages back to pixels
            version: Project version for interpreting pixel coordinates
        """
        raw_x = data.get("pixel_x", data.get("x", 0))
        raw_y = data.get("pixel_y", data.get("y", 0))

        # Version 2.1+ stores percentages, convert back to absolute pixels
        if version >= "2.1" and image_width > 0 and image_height > 0:
            pixel_x = raw_x * image_width
            pixel_y = raw_y * image_height
        else:
            # Older versions store absolute pixel coordinates
            pixel_x = raw_x
            pixel_y = raw_y

        return cls(
            id=data["id"],
            class_name=data["class_name"],
            pixel_x=pixel_x,
            pixel_y=pixel_y,
            lon=data["lon"],
            lat=data["lat"],
            # Generate UUIDs if not present (backwards compatibility)
            unique_id=data.get("unique_id") or str(uuid.uuid4()),
            object_id=data.get("object_id") or str(uuid.uuid4()),
            length_m=data.get("length_m"),
            width_m=data.get("width_m"),
            description=data.get("description", ""),
            group_id=data.get("group_id", ""),
            orientation_px_rad=data.get("orientation_px_rad"),
            orientation_deg=data.get("orientation_deg"),
            orientation_derived=bool(data.get("orientation_derived", False)),
            masks=[
                # Normalize the pre-release integer-array rle to the compact
                # string form on load, so any load-and-save migrates the
                # file (an array cost one pretty-printed line per run).
                (dict(m, rle=",".join(str(int(r)) for r in m["rle"]))
                 if isinstance(m.get("rle"), list) else dict(m))
                for m in data.get("masks", [])
            ]
        )


@dataclass
class Waypoint:
    """A named geographic bookmark, independent of any image.

    Waypoints record a place worth returning to (a site to revisit, a
    reference point, somewhere to check later). Unlike labels they belong to
    the project rather than to an image, carry no class, and are never
    exported as training data - they exist purely for navigation.
    """

    # Sequential identifier, unique within the project.
    id: int

    # Short user-facing name; auto-generated as "WP n" and freely renamed.
    name: str

    # Position in WGS84 degrees.
    lat: float
    lon: float

    def to_dict(self) -> dict:
        """Convert to a dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "lat": self.lat,
            "lon": self.lon,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Waypoint":
        """Create from a serialized dictionary."""
        return cls(
            id=int(data.get("id", 0)),
            name=str(data.get("name", "")),
            lat=float(data.get("lat", 0.0)),
            lon=float(data.get("lon", 0.0)),
        )


@dataclass
class ImageData:
    """Data for a single image including its labels."""

    # Full file path to the image
    path: str

    # Filename without extension
    name: str

    # Group path (e.g., "folder/subfolder")
    group: str

    # Labels on this image
    labels: list[PointLabel] = field(default_factory=list)

    # Original image dimensions (as read from disk)
    original_width: int = 0
    original_height: int = 0

    # Reader info: {extension: reader_name} e.g., {"h5": "custom_hdf5"} or
    # {"tif": "default"}
    reader: dict[str, str] = field(default_factory=dict)

    # Affine transform coefficients [a, b, c, d, e, f] mapping pixel -> CRS coordinates
    # X = a*col + b*row + c, Y = d*col + e*row + f
    affine_coeffs: Optional[list[float]] = None

    # CRS EPSG code for the affine transform (e.g., 3857 for Web Mercator)
    crs_epsg: Optional[int] = None

    # This image holds confusers but no true positives, and the user wants the
    # model to see them: the H5 export can opt in to sliding the whole image
    # into gt=False hard negatives even under a labels-only scope.
    hard_negative_source: bool = False

    def get_affine(self) -> Optional[Affine]:
        """Get the Affine transform object, or None if not set."""
        if self.affine_coeffs is None or len(self.affine_coeffs) != 6:
            return None
        return Affine(*self.affine_coeffs)

    def set_affine(self, affine: Affine, crs: CRS):
        """Set the affine transform and CRS.

        Args:
            affine: Affine transform (pixel to projected coordinates)
            crs: Coordinate reference system
        """
        self.affine_coeffs = [affine.a, affine.b, affine.c,
                              affine.d, affine.e, affine.f]
        self.crs_epsg = crs.to_epsg()
        # Invalidate cached transformers so they are rebuilt for the new CRS
        self._to_wgs84_transformer = None
        self._from_wgs84_transformer = None
        self._cached_transformer_epsg = None

    def get_crs(self) -> Optional[CRS]:
        """Get the CRS object, or None if not set."""
        if self.crs_epsg is None:
            return None
        return CRS.from_epsg(self.crs_epsg)

    def _ensure_transformers(self) -> bool:
        """Lazily build and cache pyproj transformers for this image's CRS.

        Returns True if transformers are available, False if no CRS is set.
        Cached transformers are reused across calls and invalidated when the
        image's ``crs_epsg`` changes (e.g. via :meth:`set_affine`).
        """
        if self.crs_epsg is None:
            return False
        # Use private attrs lazily; getattr() avoids needing dataclass fields
        # which would otherwise affect equality/serialization.
        cached_epsg = getattr(self, "_cached_transformer_epsg", None)
        if (cached_epsg != self.crs_epsg
                or getattr(self, "_to_wgs84_transformer", None) is None
                or getattr(self, "_from_wgs84_transformer", None) is None):
            self._to_wgs84_transformer = Transformer.from_crs(
                self.crs_epsg, 4326, always_xy=True
            )
            self._from_wgs84_transformer = Transformer.from_crs(
                4326, self.crs_epsg, always_xy=True
            )
            self._cached_transformer_epsg = self.crs_epsg
        return True

    def pixel_to_latlon(self, pixel_x: float, pixel_y: float) -> Optional[tuple[float, float]]:
        """Convert pixel coordinates to WGS84 lat/lon.

        Args:
            pixel_x: Pixel X coordinate (column)
            pixel_y: Pixel Y coordinate (row)

        Returns:
            Tuple of (lat, lon) in WGS84, or None if transform not available
        """
        affine = self.get_affine()
        if affine is None or not self._ensure_transformers():
            return None

        # Apply affine transform: pixel -> projected coordinates
        x_proj, y_proj = affine * (pixel_x, pixel_y)

        # Transform from image CRS to WGS84 (always_xy: lon, lat order)
        lon, lat = self._to_wgs84_transformer.transform(x_proj, y_proj)
        return (lat, lon)

    def latlon_to_pixel(self, lat: float, lon: float) -> Optional[tuple[float, float]]:
        """Convert WGS84 lat/lon to pixel coordinates.

        Args:
            lat: Latitude in degrees (WGS84)
            lon: Longitude in degrees (WGS84)

        Returns:
            Tuple of (pixel_x, pixel_y), or None if transform not available
        """
        affine = self.get_affine()
        if affine is None or not self._ensure_transformers():
            return None

        # Transform from WGS84 to image CRS (always_xy: lon/lat -> x/y)
        x_proj, y_proj = self._from_wgs84_transformer.transform(lon, lat)

        # Apply inverse affine: projected -> pixel coordinates
        pixel_x, pixel_y = ~affine * (x_proj, y_proj)
        return (pixel_x, pixel_y)

    def get_corner_coords(self) -> Optional[dict[str, tuple[float, float]]]:
        """Get WGS84 lat/lon coordinates for the 4 image corners.

        Returns:
            Dict with keys 'top_left', 'top_right', 'bottom_right', 'bottom_left',
            each containing (lat, lon), or None if transform not available
        """
        if self.original_width <= 0 or self.original_height <= 0:
            return None

        w, h = self.original_width, self.original_height
        corners = {
            'top_left': self.pixel_to_latlon(0, 0),
            'top_right': self.pixel_to_latlon(w, 0),
            'bottom_right': self.pixel_to_latlon(w, h),
            'bottom_left': self.pixel_to_latlon(0, h)
        }

        # Return None if any corner failed
        if any(v is None for v in corners.values()):
            return None
        return corners

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        # Pre-compute the WGS84 coordinate of the left image edge at every
        # label's row in a single vectorised pyproj call, instead of one
        # transform per label. This is the dominant cost when serialising
        # images with many labels.
        left_edge_by_label: dict[int, tuple[float, float]] = {}
        if (self.affine_coeffs is not None and self.crs_epsg is not None
                and self.labels and self._ensure_transformers()):
            affine = self.get_affine()
            # Project pixel (0, pixel_y) to native CRS via affine, then
            # batch-transform all native points to WGS84 in one call.
            xs_proj: list[float] = []
            ys_proj: list[float] = []
            for label in self.labels:
                x_proj, y_proj = affine * (0.0, label.pixel_y)
                xs_proj.append(x_proj)
                ys_proj.append(y_proj)
            lon_arr, lat_arr = self._to_wgs84_transformer.transform(
                xs_proj, ys_proj
            )
            for idx in range(len(self.labels)):
                left_edge_by_label[idx] = (lat_arr[idx], lon_arr[idx])

        # Build label dicts with distance from left edge
        label_dicts = []
        for idx, label in enumerate(self.labels):
            label_dict = label.to_dict(self.original_width, self.original_height)

            # Calculate distance from left edge of image to label position
            left_edge = left_edge_by_label.get(idx)
            if left_edge is not None:
                distance_m = haversine_distance(
                    left_edge[0], left_edge[1],  # left edge lat, lon
                    label.lat, label.lon         # label lat, lon
                )
                label_dict["geodesic_distance"] = round(distance_m, 3)

            label_dicts.append(label_dict)

        d = {
            "path": self.path,
            "name": self.name,
            "group": self.group,
            "labels": label_dicts,
            "original_width": self.original_width,
            "original_height": self.original_height}
        # Always include reader info - use "default" for standard GeoTIFFs
        ext = Path(self.path).suffix.lstrip('.').lower() or "tif"
        if self.reader:
            d["reader"] = self.reader
        else:
            d["reader"] = {ext: "default"}

        # Include transform if available
        if self.affine_coeffs is not None:
            d["affine_coeffs"] = self.affine_coeffs
        if self.crs_epsg is not None:
            d["crs_epsg"] = self.crs_epsg

        # Written only when set, so projects without the flag are unchanged
        # and older readers see exactly what they saw before.
        if self.hard_negative_source:
            d["hard_negative_source"] = True

        # Include corner coordinates in WGS84 for ground truth export
        corners = self.get_corner_coords()
        if corners is not None:
            d["corners_wgs84"] = {
                "top_left": {"lat": corners["top_left"][0], "lon": corners["top_left"][1]},
                "top_right": {"lat": corners["top_right"][0], "lon": corners["top_right"][1]},
                "bottom_right": {"lat": corners["bottom_right"][0], "lon": corners["bottom_right"][1]},
                "bottom_left": {"lat": corners["bottom_left"][0], "lon": corners["bottom_left"][1]}
            }

            # Calculate geodesic width and height in meters using Haversine
            tl = corners["top_left"]
            tr = corners["top_right"]
            bl = corners["bottom_left"]

            geodesic_width = haversine_distance(tl[0], tl[1], tr[0], tr[1])
            geodesic_height = haversine_distance(tl[0], tl[1], bl[0], bl[1])

            d["geodesic_width_m"] = round(geodesic_width, 3)
            d["geodesic_height_m"] = round(geodesic_height, 3)

        return d

    @classmethod
    def from_dict(cls, data: dict, version: str = "2.1") -> "ImageData":
        """Create from dictionary."""
        width = data.get("original_width", 0)
        height = data.get("original_height", 0)

        # Handle reader field - can be dict or legacy reader_ext string
        reader = data.get("reader", {})
        if not reader and data.get("reader_ext"):
            # Convert legacy reader_ext to new format
            reader = {data["reader_ext"]: "custom"}

        return cls(
            path=data["path"],
            name=data["name"],
            group=data.get("group", ""),
            labels=[
                PointLabel.from_dict(
                    l,
                    width,
                    height,
                    version) for l in data.get(
                    "labels",
                    [])],
            original_width=width,
            original_height=height,
            reader=reader,
            affine_coeffs=data.get("affine_coeffs"),
            crs_epsg=data.get("crs_epsg"),
            hard_negative_source=bool(data.get("hard_negative_source", False))
        )


@dataclass
class LabelProject:
    """Container for all images, labels, and classes in a project."""

    # User-defined class names
    classes: list[str] = field(default_factory=list)

    # Images with their labels (keyed by path for easy lookup)
    images: dict[str, ImageData] = field(default_factory=dict)

    # Named geographic bookmarks, in the order they were added
    waypoints: list[Waypoint] = field(default_factory=list)

    # Auto-increment ID counter for labels
    _next_id: int = 1

    # Auto-increment ID counter for waypoints (independent of labels)
    _next_waypoint_id: int = 1

    # Index mapping object_id -> set of label_ids for O(1) linked label lookup
    _object_id_index: dict[str, set[int]] = field(default_factory=dict)

    # Index mapping label_id -> (image_path, label) for O(1) label lookup
    _label_id_index: dict[int, tuple[str, PointLabel]] = field(default_factory=dict)

    def _index_object_id(self, label: PointLabel):
        """Add a label to the object_id index only."""
        if label.object_id not in self._object_id_index:
            self._object_id_index[label.object_id] = set()
        self._object_id_index[label.object_id].add(label.id)

    def _unindex_object_id(self, label: PointLabel):
        """Remove a label from the object_id index only."""
        if label.object_id in self._object_id_index:
            self._object_id_index[label.object_id].discard(label.id)
            if not self._object_id_index[label.object_id]:
                del self._object_id_index[label.object_id]

    def _index_label(self, label: PointLabel, image_path: str):
        """Add a label to all indexes."""
        self._index_object_id(label)
        self._label_id_index[label.id] = (image_path, label)

    def _unindex_label(self, label: PointLabel):
        """Remove a label from all indexes."""
        self._unindex_object_id(label)
        if label.id in self._label_id_index:
            del self._label_id_index[label.id]

    def _rebuild_index(self):
        """Rebuild all indexes from scratch (used after loading)."""
        self._object_id_index.clear()
        self._label_id_index.clear()
        for image_path, image in self.images.items():
            for label in image.labels:
                self._index_label(label, image_path)

    def add_class(self, class_name: str) -> bool:
        """Add a new class. Returns True if added, False if already exists."""
        if class_name and class_name not in self.classes:
            self.classes.append(class_name)
            return True
        return False

    def remove_class(self, class_name: str):
        """Remove a class and all labels with that class."""
        if class_name in self.classes:
            self.classes.remove(class_name)
            # Remove labels with this class from all images
            for image in self.images.values():
                # Unindex labels being removed
                for label in image.labels:
                    if label.class_name == class_name:
                        self._unindex_label(label)
                image.labels = [
                    l for l in image.labels if l.class_name != class_name]

    def add_image(self, path: str, name: str, group: str = "",
                  original_width: int = 0, original_height: int = 0,
                  reader: dict[str, str] | None = None,
                  affine: 'Affine | None' = None,
                  crs: 'CRS | None' = None) -> ImageData:
        """Add an image to the project (or return existing one).

        Args:
            path: Full file path to the image
            name: Filename without extension
            group: Group path (e.g., "folder/subfolder")
            original_width: Original image width in pixels
            original_height: Original image height in pixels
            reader: Reader info dict {extension: reader_name}, None for default GeoTIFF
            affine: Optional Affine transform (pixel -> projected coords)
            crs: Optional CRS for the affine transform
        """
        if path not in self.images:
            self.images[path] = ImageData(
                path=path, name=name, group=group,
                original_width=original_width, original_height=original_height,
                reader=reader or {}
            )
        img = self.images[path]
        # Update transform if provided and not already set
        if affine is not None and crs is not None and img.affine_coeffs is None:
            img.set_affine(affine, crs)
        return img

    def update_image_group(self, path: str, group: str):
        """Update the group for an image."""
        if path in self.images:
            self.images[path].group = group

    def relocate_image(self, old_path: str, new_path: str) -> bool:
        """Move an image entry to a new path, labels and flags riding along.

        The images dict is keyed by path, so a relocation re-keys the entry;
        everything about the image - labels, description texts, the
        hard-negative flag - lives inside the ImageData and moves with it.
        Refuses (returns False) when the entry is unknown or the new path is
        already taken by another image, rather than merging two label sets.
        """
        if old_path not in self.images or new_path in self.images:
            return False
        image = self.images.pop(old_path)
        image.path = new_path
        self.images[new_path] = image
        return True

    def set_hard_negative_source(self, path: str, flagged: bool):
        """Mark or unmark an image as a hard-negative source."""
        if path in self.images:
            self.images[path].hard_negative_source = bool(flagged)

    def add_label(
        self,
        class_name: str,
        pixel_x: float,
        pixel_y: float,
        lon: float,
        lat: float,
        image_name: str,
        image_group: str = "",
        image_path: str = ""
    ) -> PointLabel:
        """Add a new point label to an image."""
        # Ensure image exists
        if image_path not in self.images:
            self.add_image(image_path, image_name, image_group)

        label = PointLabel(
            id=self._next_id,
            class_name=class_name,
            pixel_x=pixel_x,
            pixel_y=pixel_y,
            lon=lon,
            lat=lat
        )
        self._next_id += 1
        self.images[image_path].labels.append(label)
        # Register in indexes
        self._index_label(label, image_path)
        return label

    def remove_label(self, label_id: int):
        """Remove a label by ID from its image. O(1) lookup via index."""
        if label_id not in self._label_id_index:
            return
        image_path, label = self._label_id_index[label_id]
        self._unindex_label(label)
        if image_path in self.images:
            self.images[image_path].labels = [
                l for l in self.images[image_path].labels if l.id != label_id
            ]

    def get_all_labels(self) -> list[tuple["ImageData", PointLabel]]:
        """Get all labels with their associated image data."""
        result = []
        for image in self.images.values():
            for label in image.labels:
                result.append((image, label))
        return result

    def get_label_by_id(self,
                        label_id: int) -> tuple["ImageData",
                                                PointLabel] | tuple[None,
                                                                    None]:
        """Get a label and its image by label ID. O(1) via index."""
        if label_id in self._label_id_index:
            image_path, label = self._label_id_index[label_id]
            if image_path in self.images:
                return self.images[image_path], label
        return None, None

    def link_labels(self, label_id1: int, label_id2: int) -> str | None:
        """Link two labels with the same object_id.

        If either label already has an object_id, both labels get that ID.
        If neither has one, a new UUID v4 is generated.

        Returns the object_id used, or None if either label wasn't found.
        """
        _, label1 = self.get_label_by_id(label_id1)
        _, label2 = self.get_label_by_id(label_id2)

        if not label1 or not label2:
            return None

        # Determine which object_id to use
        if label1.object_id:
            object_id = label1.object_id
        elif label2.object_id:
            object_id = label2.object_id
        else:
            object_id = str(uuid.uuid4())

        # If both have different object_ids, merge them (all labels with
        # label2's id get label1's id)
        if label1.object_id and label2.object_id and label1.object_id != label2.object_id:
            old_id = label2.object_id
            # Get all label_ids that need to be moved (from the index)
            labels_to_move = list(self._object_id_index.get(old_id, set()))
            for image in self.images.values():
                for label in image.labels:
                    if label.id in labels_to_move:
                        self._unindex_object_id(label)
                        label.object_id = object_id
                        self._index_object_id(label)
        else:
            # Update object_id index for both labels (label_id index unchanged)
            self._unindex_object_id(label1)
            self._unindex_object_id(label2)
            label1.object_id = object_id
            label2.object_id = object_id
            self._index_object_id(label1)
            self._index_object_id(label2)

        # group_id is shared by contract. The first label's name wins a
        # conflict (mirroring how its object_id wins above); a group with no
        # name adopts whatever the other side brings.
        merged_group_id = label1.group_id or label2.group_id
        if merged_group_id:
            self._apply_group_id(object_id, merged_group_id)

        return object_id

    def _apply_group_id(self, object_id: str, group_id: str):
        """Stamp one group_id onto every label sharing *object_id*."""
        for lid in self._object_id_index.get(object_id, set()):
            _, label = self.get_label_by_id(lid)
            if label is not None:
                label.group_id = group_id

    def set_group_id(self, label_id: int, group_id: str) -> list[int]:
        """Set the shared group name on a label's whole linked group.

        This is the ONLY way group_id should change: the contract is that
        every label sharing an object_id always carries the same group_id,
        so the update is applied to all of them in one move. Returns the ids
        of every label changed (just the one, for an unlinked label).
        """
        _, label = self.get_label_by_id(label_id)
        if label is None:
            return []
        group_id = group_id.strip()
        self._apply_group_id(label.object_id, group_id)
        return sorted(self._object_id_index.get(label.object_id, {label_id}))

    def unlink_label(self, label_id: int):
        """Remove a label from its object group by giving it a new unique UUID.

        The departing label also loses the group's shared name - group_id
        describes the linked group it is no longer part of. The labels that
        stay keep theirs.
        """
        _, label = self.get_label_by_id(label_id)
        if label:
            self._unindex_object_id(label)
            label.object_id = str(uuid.uuid4())
            label.group_id = ""
            self._index_object_id(label)

    def get_linked_labels(
            self, label_id: int) -> list[tuple["ImageData", PointLabel]]:
        """Get all labels linked to the given label (same object_id).

        Returns labels only if there are 2 or more with the same object_id.
        Uses the object_id index for O(1) lookup.
        """
        _, source_label = self.get_label_by_id(label_id)
        if not source_label or not source_label.object_id:
            return []

        # Use the index to get all label_ids with the same object_id
        linked_label_ids = self._object_id_index.get(source_label.object_id, set())

        # Only proceed if there are actually linked labels (more than 1)
        if len(linked_label_ids) <= 1:
            return []

        # Build result list by looking up each label_id
        result = []
        for lid in linked_label_ids:
            image, label = self.get_label_by_id(lid)
            if image and label:
                result.append((image, label))

        return result

    @property
    def label_count(self) -> int:
        """Get total number of labels across all images."""
        return sum(len(img.labels) for img in self.images.values())

    # ------------------------------------------------------------------
    # Waypoints: named geographic bookmarks, independent of the images
    # ------------------------------------------------------------------

    def add_waypoint(self, lat: float, lon: float,
                     name: str = "") -> Waypoint:
        """Add a waypoint, auto-naming it "WP n" when no name is given."""
        wp = Waypoint(
            id=self._next_waypoint_id,
            name=name.strip() or f"WP {self._next_waypoint_id}",
            lat=lat,
            lon=lon,
        )
        self._next_waypoint_id += 1
        self.waypoints.append(wp)
        return wp

    def get_waypoint(self, waypoint_id: int) -> Optional[Waypoint]:
        """Return the waypoint with this id, or None."""
        for wp in self.waypoints:
            if wp.id == waypoint_id:
                return wp
        return None

    def remove_waypoint(self, waypoint_id: int) -> bool:
        """Remove a waypoint by id. Returns True if one was removed."""
        for i, wp in enumerate(self.waypoints):
            if wp.id == waypoint_id:
                del self.waypoints[i]
                return True
        return False

    def rename_waypoint(self, waypoint_id: int, name: str) -> bool:
        """Rename a waypoint. Blank names are ignored, keeping the old one."""
        wp = self.get_waypoint(waypoint_id)
        if wp is None or not name.strip():
            return False
        wp.name = name.strip()
        return True

    @property
    def waypoint_count(self) -> int:
        """Number of waypoints in the project."""
        return len(self.waypoints)

    def to_dict(self) -> dict:
        """The whole project as plain data, ready for json.dump.

        Both save() and the crash-recovery snapshot serialise through this.
        They used to build the dictionary separately, and when waypoints were
        added only save() learned about them - so a crash quietly lost every
        waypoint in the project while restoring the labels. One serialiser
        means a new field cannot reach one path and miss the other.

        Pure Python and no I/O, so the caller can build this on the UI thread
        and hand it to a background writer.
        """
        return {
            "version": "3.9",
            # Copied, not referenced: the recovery snapshot is handed to a
            # background writer and the user carries on editing meanwhile.
            # The image and waypoint entries are freshly built dictionaries,
            # so they are already detached from the project.
            "classes": list(self.classes),
            "images": [img.to_dict() for img in self.images.values()],
            "waypoints": [wp.to_dict() for wp in self.waypoints],
            "_next_id": self._next_id,
            "_next_waypoint_id": self._next_waypoint_id
        }

    def save(self, file_path: str | Path):
        """Save project to JSON file."""
        with open(file_path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, file_path: str | Path) -> "LabelProject":
        """Load project from JSON file."""
        with open(file_path, 'r') as f:
            data = json.load(f)

        project = cls()
        project.classes = data.get("classes", [])
        project._next_id = data.get("_next_id", 1)

        # Waypoints arrived in 3.3; older projects simply have none. The id
        # counter is rebuilt from the data when absent, so a hand-edited file
        # can never hand out an id that is already taken.
        project.waypoints = [Waypoint.from_dict(w)
                             for w in data.get("waypoints", [])]
        project._next_waypoint_id = data.get(
            "_next_waypoint_id",
            max((w.id for w in project.waypoints), default=0) + 1)

        version = data.get("version", "1.0")

        if version >= "2.0":
            # Image-centric format (2.0 and later)
            for img_data in data.get("images", []):
                image = ImageData.from_dict(img_data, version)
                project.images[image.path] = image
        else:
            # Legacy format (version 1.0) - convert from label-centric
            for label_data in data.get("labels", []):
                image_path = label_data.get("image_path", "")
                image_name = label_data.get("image_name", "")
                image_group = label_data.get("image_group", "")

                if image_path and image_path not in project.images:
                    project.images[image_path] = ImageData(
                        path=image_path,
                        name=image_name,
                        group=image_group
                    )

                if image_path:
                    label = PointLabel(
                        id=label_data["id"],
                        class_name=label_data["class_name"],
                        pixel_x=label_data.get(
                            "pixel_x", label_data.get("x", 0)),
                        pixel_y=label_data.get(
                            "pixel_y", label_data.get("y", 0)),
                        lon=label_data["lon"],
                        lat=label_data["lat"]
                    )
                    project.images[image_path].labels.append(label)

            # Also check for image_paths from v1 format
            for path in data.get("image_paths", []):
                if path not in project.images:
                    name = Path(path).stem
                    project.images[path] = ImageData(
                        path=path, name=name, group="")

        # Rebuild the object_id index after loading
        project._rebuild_index()
        # The shared-group_id contract holds for files this application
        # wrote, but a hand-edited or merged file can arrive with a group
        # disagreeing with itself. Repair on load - first non-empty name in
        # the group wins - so every consumer downstream can rely on the
        # invariant instead of re-checking it.
        for object_id, label_ids in project._object_id_index.items():
            if len(label_ids) < 2:
                continue
            names = []
            for lid in sorted(label_ids):
                _, label = project.get_label_by_id(lid)
                if label is not None and label.group_id:
                    names.append(label.group_id)
            if names and len(set(names)) >= 1:
                first = names[0]
                if any(n != first for n in names) or len(names) != len(label_ids):
                    project._apply_group_id(object_id, first)

        return project

    def clear(self):
        """Clear all labels but keep images and classes."""
        for image in self.images.values():
            image.labels.clear()
        self._next_id = 1
        self._object_id_index.clear()
        self._label_id_index.clear()

