"""The labels as KML: Export > Labels (KML)...

KML 2.2 is what Google Earth, QGIS, ArcGIS and GDAL read, and the
standard library writes it. One Placemark per label with ground: a
Folder per class, a Style per class in the project's marker colour, the
class as the placemark's name, the label's own description as its
description, and everything else known of it - image, group, pixel
position, size, heading, confidence, review, who made it and when, the
ids - as ExtendedData, so nothing of the label is lost on the way out.

What is left out, and counted: a label on imagery without ground (no
georeferencing and no position recorded), and a label rejected in the
Snippet Editor, which the HDF5 and sub-image exports leave out too.

Pure functions, no Qt. MainWindow._export_kml puts the file dialog and
the status line around write_kml.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePath

from .labels import REVIEW_REJECTED
from .version import app_version

KML_NS = "http://www.opengis.net/kml/2.2"
EXTENSION = ".kml"
ICON = "http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png"
DEFAULT_COLOUR = "#ffffff"


@dataclass
class KmlReport:
    """What an export came to."""
    written: int = 0
    no_ground: int = 0          # labels with no position on the ground
    rejected: int = 0           # labels rejected in review, left out
    classes: Counter = field(default_factory=Counter)   # class -> written

    def summary(self) -> str:
        parts = [f"{self.written} label{'' if self.written == 1 else 's'}"]
        if self.classes:
            parts[0] += " (" + ", ".join(
                f"{n} {name}" for name, n in sorted(
                    self.classes.items(), key=lambda item: (-item[1], item[0])
                )) + ")"
        if self.no_ground:
            parts.append(f"{self.no_ground} without ground left out")
        if self.rejected:
            parts.append(f"{self.rejected} rejected left out")
        return "; ".join(parts)


def write_kml(path, project, colours: dict | None = None,
              name: str | None = None) -> KmlReport:
    """Write the project's labels as a KML 2.2 file; returns the counts.

    ``colours`` maps a class name to its marker colour as "#rrggbb" (the
    canvas's), white for a class not given. ``name`` is the Document's,
    the project file's by default. Raises OSError when the file cannot
    be written.
    """
    colours = colours or {}
    report = KmlReport()
    ET.register_namespace("", KML_NS)
    root = ET.Element(_tag("kml"))
    document = ET.SubElement(root, _tag("Document"))
    ET.SubElement(document, _tag("name")).text = _clean(
        name or "GeoLabeller labels")
    ET.SubElement(document, _tag("description")).text = (
        f"Labels written by GeoLabeller {app_version()} on "
        + datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    classes = list(project.classes)
    for image in project.images.values():
        for label in image.labels:
            if label.class_name not in classes:
                classes.append(label.class_name)
    for index, class_name in enumerate(classes):
        _style(document, _style_id(index), colours.get(class_name))
    folders: dict = {}
    for image in project.images.values():
        grounded = image.get_affine() is not None and image.get_crs() is not None
        for label in sorted(image.labels, key=lambda lab: lab.id):
            if label.review == REVIEW_REJECTED:
                report.rejected += 1
                continue
            if not grounded and (label.lat, label.lon) == (0.0, 0.0):
                report.no_ground += 1
                continue
            folder = folders.get(label.class_name)
            if folder is None:
                folder = ET.SubElement(document, _tag("Folder"))
                ET.SubElement(folder, _tag("name")).text = _clean(
                    label.class_name)
                folders[label.class_name] = folder
            _placemark(folder, image, label,
                       _style_id(classes.index(label.class_name)))
            report.written += 1
            report.classes[label.class_name] += 1
    tree = ET.ElementTree(root)
    ET.indent(tree)
    tree.write(path, encoding="UTF-8", xml_declaration=True)
    return report


def kml_colour(rgb: str | None) -> str:
    """A "#rrggbb" colour as KML writes it: aabbggrr, opaque."""
    text = (rgb or DEFAULT_COLOUR).lstrip("#")
    if len(text) != 6:
        text = DEFAULT_COLOUR.lstrip("#")
    try:
        int(text, 16)
    except ValueError:
        text = DEFAULT_COLOUR.lstrip("#")
    r, g, b = text[0:2], text[2:4], text[4:6]
    return f"ff{b}{g}{r}".lower()


# -- the pieces ---------------------------------------------------------------

def _style_id(index: int) -> str:
    return f"class-{index}"


def _style(document, style_id: str, rgb: str | None):
    style = ET.SubElement(document, _tag("Style"), {"id": style_id})
    icon_style = ET.SubElement(style, _tag("IconStyle"))
    ET.SubElement(icon_style, _tag("color")).text = kml_colour(rgb)
    ET.SubElement(icon_style, _tag("scale")).text = "1.0"
    icon = ET.SubElement(icon_style, _tag("Icon"))
    ET.SubElement(icon, _tag("href")).text = ICON
    label_style = ET.SubElement(style, _tag("LabelStyle"))
    ET.SubElement(label_style, _tag("scale")).text = "0.8"


def _placemark(folder, image, label, style_id: str):
    mark = ET.SubElement(folder, _tag("Placemark"),
                         {"id": f"label-{int(label.id)}"})
    ET.SubElement(mark, _tag("name")).text = _clean(label.class_name)
    if label.description:
        ET.SubElement(mark, _tag("description")).text = _clean(
            label.description)
    created = (label.attribution or {}).get("created") or {}
    when = str(created.get("at") or "")
    if when:
        stamp = ET.SubElement(mark, _tag("TimeStamp"))
        ET.SubElement(stamp, _tag("when")).text = _clean(when)
    ET.SubElement(mark, _tag("styleUrl")).text = "#" + style_id
    data = ET.SubElement(mark, _tag("ExtendedData"))
    rows = [
        ("class", label.class_name),
        ("image", PurePath(str(image.path or "").replace("\\", "/")).name),
        ("group", image.group or ""),
        ("pixel_x", f"{float(label.pixel_x):.2f}"),
        ("pixel_y", f"{float(label.pixel_y):.2f}"),
        ("label_id", str(int(label.id))),
        ("unique_id", label.unique_id or ""),
        ("object_id", label.object_id or ""),
    ]
    if label.length_m is not None:
        rows.append(("length_m", f"{float(label.length_m):.2f}"))
    if label.width_m is not None:
        rows.append(("width_m", f"{float(label.width_m):.2f}"))
    if label.orientation_deg is not None:
        rows.append(("heading_deg", f"{float(label.orientation_deg):.1f}"))
    if label.confidence:
        rows.append(("confidence", str(int(label.confidence))))
    if label.review:
        rows.append(("review", label.review))
    if created.get("by"):
        rows.append(("created_by", str(created["by"])))
    if when:
        rows.append(("created_at", when))
    if label.description:
        rows.append(("description", label.description))
    for key, value in rows:
        entry = ET.SubElement(data, _tag("Data"), {"name": key})
        ET.SubElement(entry, _tag("value")).text = _clean(value)
    point = ET.SubElement(mark, _tag("Point"))
    ET.SubElement(point, _tag("coordinates")).text = (
        f"{float(label.lon):.8f},{float(label.lat):.8f},0")


def _tag(name: str) -> str:
    return f"{{{KML_NS}}}{name}"


def _clean(text) -> str:
    """Text as XML can carry it: no control character (one made a whole
    GPX file unreadable, waypoint_io)."""
    return "".join(ch for ch in str(text) if ch >= " " or ch in "\t\n")
