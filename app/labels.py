"""Label data model and storage for point annotations."""
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path


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

        return {
            "id": self.id,
            "unique_id": self.unique_id,
            "class_name": self.class_name,
            "pixel_x": pct_x,
            "pixel_y": pct_y,
            "lon": self.lon,
            "lat": self.lat,
            "object_id": self.object_id
        }

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
            object_id=data.get("object_id") or str(uuid.uuid4())
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

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        d = {
            "path": self.path,
            "name": self.name,
            "group": self.group,
            "labels": [
                l.to_dict(
                    self.original_width,
                    self.original_height) for l in self.labels],
            "original_width": self.original_width,
            "original_height": self.original_height}
        # Always include reader info - use "default" for standard GeoTIFFs
        ext = Path(self.path).suffix.lstrip('.').lower() or "tif"
        if self.reader:
            d["reader"] = self.reader
        else:
            d["reader"] = {ext: "default"}
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
            reader=reader
        )


@dataclass
class LabelProject:
    """Container for all images, labels, and classes in a project."""

    # User-defined class names
    classes: list[str] = field(default_factory=list)

    # Images with their labels (keyed by path for easy lookup)
    images: dict[str, ImageData] = field(default_factory=dict)

    # Custom readers: extension -> reader name or path
    # e.g., {"h5": "h5_gcps", "dat": "./my_reader.py"}
    custom_readers: dict[str, str] = field(default_factory=dict)

    # Auto-increment ID counter for labels
    _next_id: int = 1

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
                  reader: dict[str, str] | None = None) -> ImageData:
        """Add an image to the project (or return existing one).

        Args:
            path: Full file path to the image
            name: Filename without extension
            group: Group path (e.g., "folder/subfolder")
            original_width: Original image width in pixels
            original_height: Original image height in pixels
            reader: Reader info dict {extension: reader_name}, None for default GeoTIFF
        """
        if path not in self.images:
            self.images[path] = ImageData(
                path=path, name=name, group=group,
                original_width=original_width, original_height=original_height,
                reader=reader or {}
            )
        return self.images[path]

    def update_image_group(self, path: str, group: str):
        """Update the group for an image."""
        if path in self.images:
            self.images[path].group = group

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

    def get_labels_for_image(self, image_path: str) -> list[PointLabel]:
        """Get all labels for a specific image."""
        if image_path in self.images:
            return self.images[image_path].labels
        return []

    def get_labels_by_class(
            self, class_name: str) -> list[tuple["ImageData", PointLabel]]:
        """Get all labels with a specific class."""
        result = []
        for image in self.images.values():
            for label in image.labels:
                if label.class_name == class_name:
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

        return object_id

    def unlink_label(self, label_id: int):
        """Remove a label from its object group by giving it a new unique UUID."""
        _, label = self.get_label_by_id(label_id)
        if label:
            self._unindex_object_id(label)
            label.object_id = str(uuid.uuid4())
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

    def save(self, file_path: str | Path):
        """Save project to JSON file."""
        data = {
            "version": "3.1",
            "classes": self.classes,
            "images": [img.to_dict() for img in self.images.values()],
            "_next_id": self._next_id
        }
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, file_path: str | Path) -> "LabelProject":
        """Load project from JSON file."""
        with open(file_path, 'r') as f:
            data = json.load(f)

        project = cls()
        project.classes = data.get("classes", [])
        project._next_id = data.get("_next_id", 1)

        # Load legacy top-level custom_readers if present (v3.0 format)
        legacy_readers = data.get("custom_readers", {})

        version = data.get("version", "1.0")

        if version >= "2.0":
            # Image-centric format (2.0 and later)
            for img_data in data.get("images", []):
                image = ImageData.from_dict(img_data, version)
                project.images[image.path] = image

                # Build custom_readers dict from per-image reader info
                if image.reader:
                    for ext, reader_name in image.reader.items():
                        if reader_name != "default" and ext not in project.custom_readers:
                            project.custom_readers[ext] = reader_name

            # Also include any legacy top-level custom_readers not already
            # present
            for ext, reader_name in legacy_readers.items():
                if ext not in project.custom_readers:
                    project.custom_readers[ext] = reader_name
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

        return project

    def clear(self):
        """Clear all labels but keep images and classes."""
        for image in self.images.values():
            image.labels.clear()
        self._next_id = 1
        self._object_id_index.clear()
        self._label_id_index.clear()

    def clear_all(self):
        """Clear everything."""
        self.classes.clear()
        self.images.clear()
        self.custom_readers.clear()
        self._next_id = 1
        self._object_id_index.clear()
        self._label_id_index.clear()
