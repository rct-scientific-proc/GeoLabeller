"""Label data model and storage for point annotations."""
import json
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class PointLabel:
    """A single point label annotation."""
    
    # Unique identifier
    id: int
    
    # Class/category name
    class_name: str
    
    # Pixel coordinates relative to the image
    pixel_x: float  # column (x)
    pixel_y: float  # row (y)
    
    # Coordinates in WGS84
    lon: float
    lat: float
    
    # Object ID for linking labels across images (UUID v4)
    # Every label has a unique object_id; linked labels share the same one
    object_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "class_name": self.class_name,
            "pixel_x": self.pixel_x,
            "pixel_y": self.pixel_y,
            "lon": self.lon,
            "lat": self.lat,
            "object_id": self.object_id
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "PointLabel":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            class_name=data["class_name"],
            pixel_x=data.get("pixel_x", data.get("x", 0)),
            pixel_y=data.get("pixel_y", data.get("y", 0)),
            lon=data["lon"],
            lat=data["lat"],
            # Generate UUID if not present (backwards compatibility)
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
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "path": self.path,
            "name": self.name,
            "group": self.group,
            "labels": [l.to_dict() for l in self.labels]
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ImageData":
        """Create from dictionary."""
        return cls(
            path=data["path"],
            name=data["name"],
            group=data.get("group", ""),
            labels=[PointLabel.from_dict(l) for l in data.get("labels", [])]
        )


@dataclass
class LabelProject:
    """Container for all images, labels, and classes in a project."""
    
    # User-defined class names
    classes: list[str] = field(default_factory=list)
    
    # Images with their labels (keyed by path for easy lookup)
    images: dict[str, ImageData] = field(default_factory=dict)
    
    # Auto-increment ID counter for labels
    _next_id: int = 1
    
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
                image.labels = [l for l in image.labels if l.class_name != class_name]
    
    def add_image(self, path: str, name: str, group: str = "") -> ImageData:
        """Add an image to the project (or return existing one)."""
        if path not in self.images:
            self.images[path] = ImageData(path=path, name=name, group=group)
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
        return label
    
    def remove_label(self, label_id: int):
        """Remove a label by ID from any image."""
        for image in self.images.values():
            image.labels = [l for l in image.labels if l.id != label_id]
    
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
    
    def get_labels_by_class(self, class_name: str) -> list[tuple["ImageData", PointLabel]]:
        """Get all labels with a specific class."""
        result = []
        for image in self.images.values():
            for label in image.labels:
                if label.class_name == class_name:
                    result.append((image, label))
        return result
    
    def get_label_by_id(self, label_id: int) -> tuple["ImageData", PointLabel] | tuple[None, None]:
        """Get a label and its image by label ID."""
        for image in self.images.values():
            for label in image.labels:
                if label.id == label_id:
                    return image, label
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
        
        # If both have different object_ids, merge them (all labels with label2's id get label1's id)
        if label1.object_id and label2.object_id and label1.object_id != label2.object_id:
            old_id = label2.object_id
            for image in self.images.values():
                for label in image.labels:
                    if label.object_id == old_id:
                        label.object_id = object_id
        else:
            label1.object_id = object_id
            label2.object_id = object_id
        
        return object_id
    
    def unlink_label(self, label_id: int):
        """Remove a label from its object group by giving it a new unique UUID."""
        _, label = self.get_label_by_id(label_id)
        if label:
            label.object_id = str(uuid.uuid4())
    
    def get_linked_labels(self, label_id: int) -> list[tuple["ImageData", PointLabel]]:
        """Get all labels linked to the given label (same object_id).
        
        Returns labels only if there are 2 or more with the same object_id.
        """
        _, source_label = self.get_label_by_id(label_id)
        if not source_label or not source_label.object_id:
            return []
        
        result = []
        for image in self.images.values():
            for label in image.labels:
                if label.object_id == source_label.object_id:
                    result.append((image, label))
        
        # Only return if there are actually linked labels (more than 1)
        return result if len(result) > 1 else []
    
    @property
    def label_count(self) -> int:
        """Get total number of labels across all images."""
        return sum(len(img.labels) for img in self.images.values())
    
    def save(self, file_path: str | Path):
        """Save project to JSON file."""
        data = {
            "version": "2.0",
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
        
        version = data.get("version", "1.0")
        
        if version == "2.0":
            # New image-centric format
            for img_data in data.get("images", []):
                image = ImageData.from_dict(img_data)
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
                        pixel_x=label_data.get("pixel_x", label_data.get("x", 0)),
                        pixel_y=label_data.get("pixel_y", label_data.get("y", 0)),
                        lon=label_data["lon"],
                        lat=label_data["lat"]
                    )
                    project.images[image_path].labels.append(label)
            
            # Also check for image_paths from v1 format
            for path in data.get("image_paths", []):
                if path not in project.images:
                    name = Path(path).stem
                    project.images[path] = ImageData(path=path, name=name, group="")
        
        return project
    
    def clear(self):
        """Clear all labels but keep images and classes."""
        for image in self.images.values():
            image.labels.clear()
        self._next_id = 1
    
    def clear_all(self):
        """Clear everything."""
        self.classes.clear()
        self.images.clear()
        self._next_id = 1
