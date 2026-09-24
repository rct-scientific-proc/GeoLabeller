"""Waypoint exchange: GPX out, GPX or a GeoLabeller project in.

GPX 1.1 is the waypoint interchange format everything else reads - GPS
receivers, Google Earth, QGIS, GDAL (which ships with the app) - and the
standard library writes and parses it, so nothing new to install. On
import a GeoLabeller project file (.geolabel / .json) is accepted too: a
colleague's waypoints can be taken from their project directly, since
Import Ground Truth deliberately leaves waypoints alone.

Pure functions, no Qt. MainWindow._export_waypoints and _import_waypoints
put the file dialogs and the confirmation around them.
"""
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

from .version import app_version

GPX_NS = "http://www.topografix.com/GPX/1/1"
EXTENSION = ".gpx"
# Two positions closer than this are the same place: 1e-7 degrees is about
# a centimetre, far inside how precisely anyone places a waypoint.
SAME_PLACE_DEG = 1e-7


class WaypointFileError(ValueError):
    """The file could not be read as waypoints; the message says why."""


@dataclass(frozen=True)
class Found:
    """One waypoint read from a file. A blank name is auto-named on apply."""
    name: str
    lat: float
    lon: float


@dataclass
class WaypointsRead:
    """What a file held: the usable entries, how many were not, and the
    kind of file it was ("gpx" or "project")."""
    found: list
    skipped: int
    source: str


# -- writing ----------------------------------------------------------------

def write_gpx(path, waypoints, creator: str | None = None) -> int:
    """Write ``waypoints`` (anything with name, lat, lon) as a GPX 1.1 file.

    Returns how many were written. Raises OSError when the file cannot be
    written. A name loses any control character, which XML cannot carry
    (one such character made the whole file unreadable).
    """
    ET.register_namespace("", GPX_NS)
    root = ET.Element(_tag("gpx"), {
        "version": "1.1",
        "creator": creator or f"GeoLabeller {app_version()}"})
    metadata = ET.SubElement(root, _tag("metadata"))
    ET.SubElement(metadata, _tag("time")).text = (
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    count = 0
    for wp in waypoints:
        wpt = ET.SubElement(root, _tag("wpt"), {
            "lat": f"{wp.lat:.8f}", "lon": f"{wp.lon:.8f}"})
        ET.SubElement(wpt, _tag("name")).text = _clean(wp.name)
        count += 1
    tree = ET.ElementTree(root)
    ET.indent(tree)
    tree.write(path, encoding="UTF-8", xml_declaration=True)
    return count


def _tag(name: str) -> str:
    return f"{{{GPX_NS}}}{name}"


def _clean(name) -> str:
    return "".join(ch for ch in str(name) if ch >= " " or ch in "\t\n")


# -- reading ----------------------------------------------------------------

def read_waypoints(path) -> WaypointsRead:
    """Read the waypoints in a GPX file or a GeoLabeller project file.

    The kind is told from the content, not the extension. Raises
    WaypointFileError for anything that is neither.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError as e:
        raise WaypointFileError(
            f"Could not read {path}: {e.strerror or e}") from None
    start = head.lstrip(b"\xef\xbb\xbf \t\r\n")     # a BOM, whitespace
    if start.startswith(b"<"):
        return _read_gpx(path)
    if start.startswith((b"{", b"[")):
        return _read_project(path)
    raise WaypointFileError(
        f"{path} is neither a GPX file nor a GeoLabeller project.")


def _local(tag) -> str:
    """An element's tag without its namespace."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _read_gpx(path) -> WaypointsRead:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        raise WaypointFileError(
            f"{path} is not well-formed XML: {e}") from None
    if _local(root.tag) != "gpx":
        raise WaypointFileError(
            f"{path} is XML but not GPX (its root element is "
            f"<{_local(root.tag)}>).")
    # Any namespace (GPX 1.0, 1.1, none) and any depth: only wpt elements
    # are waypoints - route and track points are not.
    found, skipped = [], 0
    for elem in root.iter():
        if _local(elem.tag) != "wpt":
            continue
        position = _position(elem.get("lat"), elem.get("lon"))
        if position is None:
            skipped += 1
            continue
        name = ""
        for child in elem:
            if _local(child.tag) == "name":
                name = (child.text or "").strip()
                break
        found.append(Found(name, *position))
    return WaypointsRead(found, skipped, "gpx")


def _read_project(path) -> WaypointsRead:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise WaypointFileError(f"{path} is not readable JSON: {e}") from None
    if not isinstance(data, dict) or not any(
            key in data for key in ("waypoints", "images", "format_version")):
        raise WaypointFileError(
            f"{path} is JSON but not a GeoLabeller project.")
    entries = data.get("waypoints", [])
    if not isinstance(entries, list):
        raise WaypointFileError(
            f"{path}: the project's waypoints entry is not a list.")
    found, skipped = [], 0
    for entry in entries:
        position = (_position(entry.get("lat"), entry.get("lon"))
                    if isinstance(entry, dict) else None)
        if position is None:
            skipped += 1
            continue
        found.append(Found(str(entry.get("name") or "").strip(), *position))
    return WaypointsRead(found, skipped, "project")


def _position(lat, lon):
    """(lat, lon) as floats when both are numbers on the globe, else None."""
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon


# -- into the project -------------------------------------------------------

def same_place(lat_a, lon_a, lat_b, lon_b) -> bool:
    return (abs(lat_a - lat_b) <= SAME_PLACE_DEG
            and abs(lon_a - lon_b) <= SAME_PLACE_DEG)


def plan_import(project, found) -> tuple[list, int]:
    """(new, already): the entries to add, and how many are here already.

    Already here: at the same place as a project waypoint and either
    unnamed in the file or named the same (case and padding aside). A
    renamed waypoint at the same spot is new - the name is information.
    So importing a file twice adds nothing the second time.
    """
    have = [(wp.name.strip().casefold(), wp.lat, wp.lon)
            for wp in project.waypoints]
    new, already = [], 0
    for entry in found:
        key = entry.name.strip().casefold()
        if any(same_place(entry.lat, entry.lon, lat, lon)
               and (not key or key == name) for name, lat, lon in have):
            already += 1
            continue
        new.append(entry)
        have.append((key, entry.lat, entry.lon))   # the file's own repeats
    return new, already


def apply_import(project, new) -> list:
    """Add ``new`` to the project; returns the Waypoints made (fresh ids,
    blank names auto-named "WP n")."""
    return [project.add_waypoint(entry.lat, entry.lon, name=entry.name)
            for entry in new]
