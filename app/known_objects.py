"""Objects other images' labels put on an image (asked for 2026-11-20).

In a survey flown in passes, an object labelled on five images is almost
always on a sixth that covers the same ground. With View > Cycling > Stop
at Objects Labelled on Other Images, a cycle step stops at each such object
not labelled on the image yet - zoomed to where the other passes put it -
before the next image; and Object Cycle visits, as well as the images the
object is labelled on, the ones that cover it without its label.

Ryan's answers: one stop per object; objects labelled on the image already
are skipped; a label placed at a stop is linked to its object (a second
switch); Object Cycle takes in the images that cover the object.

Users' feedback on 2.10.8 (2.10.9): the rings 2.10.8 drew round each other
view's place were overwhelming - gone; and an object whose views had
spread widely was framed to hold every one of them, so one stray view
zoomed the stop out until the object itself was lost. An object's place
is now its CLUMP: the tightest group holding at least half its views,
with any others near that group (_clump) - stray views count for nothing,
neither where the stop is nor how wide.

This module is the bookkeeping, free of Qt: every object's place on the
ground, the views that agree on it, and which objects lie on a layer.
Places are Web Mercator metres, the map's own units.
"""
import math
import os
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from .labels import canonical_path

EARTH_RADIUS = 6378137.0
WORLD_WIDTH = 2.0 * math.pi * EARTH_RADIUS


@lru_cache(maxsize=65536)
def path_key(path: str) -> str:
    """One spelling of an image's path to compare by (cached: an index
    of 20,000 labels asks it of each label's image)."""
    return os.path.normcase(canonical_path(path or ""))


def to_mercator(lon: float, lat: float) -> tuple[float, float]:
    """WGS84 degrees to Web Mercator metres (the canvas's formula)."""
    x = math.radians(lon) * EARTH_RADIUS
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * EARTH_RADIUS
    return x, y


def to_lonlat(x: float, y: float) -> tuple[float, float]:
    """Web Mercator metres to WGS84 degrees, longitude within -180..180."""
    lon = math.degrees(x / EARTH_RADIUS)
    lon = (lon + 180.0) % 360.0 - 180.0
    lat = math.degrees(2 * math.atan(math.exp(y / EARTH_RADIUS)) - math.pi / 2)
    return lon, lat


def x_on(bounds, wraps: bool, x: float) -> float:
    """``x`` as drawn on a layer: on one across the 180 degree meridian,
    ground on the far side is drawn one world width east (hunt M5)."""
    if wraps and bounds is not None and x < bounds[0]:
        return x + WORLD_WIDTH
    return x


@dataclass(frozen=True)
class KnownObject:
    """One object with a place on the ground: a linked set of labels, or
    a label linked to none."""
    object_id: str
    x: float                # the median of its clump
    y: float
    places: tuple           # ((label_id, x, y), ...) of all its labels
    clump: tuple            # ((x, y), ...) of the views that agree
    paths: frozenset        # path_key of every image it is labelled on
    classes: frozenset
    name: str               # "vessel 'convoy lead'"

    def place_of(self, label_id: int) -> "tuple[float, float] | None":
        for member, x, y in self.places:
            if member == label_id:
                return x, y
        return None


@dataclass(frozen=True)
class Stop:
    """Where a cycle stops on an image: the object, and the place to look
    - its label there when it has one (Object Cycle), else where the
    other images put it."""
    obj: KnownObject
    x: float
    y: float
    labelled_here: bool = False


def _median_x(xs: list) -> list:
    """Longitudes either side of 180 degrees on one side of it, so the
    median of a place straddling it is not half a world away."""
    if xs and max(xs) - min(xs) > WORLD_WIDTH / 2:
        return [x + WORLD_WIDTH if x < 0 else x for x in xs]
    return xs


def _median(values: list) -> float:
    """The median of a few values (numpy's costs more than the sort for
    the handful of views an object has)."""
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


# Views further from the clump's core than this many times the core's own
# reach are strays; nearer, they join it.
CLUMP_REACH = 3.0
# From this many views the distances are worked out by numpy at once (for
# fewer, its overhead costs more than the loop).
NUMPY_FROM = 16


def _clump(points: list) -> list:
    """The views of ``points`` (x, y) that agree on where the object is.

    The core is the tightest group of at least half of them (two at
    least): the view whose k-th nearest view (itself the first) is
    nearest, with those k. Of two groups as large, the tighter wins. The
    views within CLUMP_REACH times the core's reach of its median join
    it; the rest are strays. One view is its own clump; two are one
    clump, however far apart - neither is "most"."""
    n = len(points)
    if n < 2:
        return list(points)
    k = max(2, (n + 1) // 2)
    if n < NUMPY_FROM:
        best, best_radius = 0, math.inf
        for i, (px, py) in enumerate(points):
            radius = sorted(math.hypot(qx - px, qy - py)
                            for qx, qy in points)[k - 1]
            if radius < best_radius:
                best, best_radius = i, radius
        px, py = points[best]
        core = sorted(points,
                      key=lambda q: math.hypot(q[0] - px, q[1] - py))[:k]
    else:
        at = np.asarray(points, dtype=float)
        apart = np.hypot(at[:, None, 0] - at[None, :, 0],
                         at[:, None, 1] - at[None, :, 1])
        radii = np.partition(apart, k - 1, axis=1)[:, k - 1]
        best = int(np.argmin(radii))
        core = [points[i] for i in np.argsort(apart[best], kind="stable")[:k]]
    cx = _median([x for x, _y in core])
    cy = _median([y for _x, y in core])
    reach = CLUMP_REACH * max(math.hypot(x - cx, y - cy) for x, y in core)
    return [(x, y) for x, y in points if math.hypot(x - cx, y - cy) <= reach]


def object_from(object_id: str, members: list) -> "KnownObject | None":
    """``members``: (label, image) pairs of one object. None when none of
    them has a place on the ground."""
    places, paths, classes, name = [], set(), set(), ""
    for label, image in members:
        paths.add(path_key(image.path))
        classes.add(label.class_name)
        if not name:
            name = label.class_name + (
                f" '{label.group_id}'" if label.group_id else "")
        ground = image.ground_of(label)
        if ground is None:
            continue
        x, y = to_mercator(ground[1], ground[0])
        places.append((label.id, x, y))
    if not places:
        return None
    xs = _median_x([x for _id, x, _y in places])
    ys = [y for _id, _x, y in places]
    clump = _clump(list(zip(xs, ys)))
    cx = float(_median([x for x, _y in clump]))
    cy = float(_median([y for _x, y in clump]))
    if cx > WORLD_WIDTH / 2:
        cx -= WORLD_WIDTH
    clump = tuple((x - WORLD_WIDTH if x > WORLD_WIDTH / 2 else x, y)
                  for x, y in clump)
    return KnownObject(object_id, cx, cy, tuple(places), clump,
                       frozenset(paths), frozenset(classes), name)


class ObjectIndex:
    """Every object of a project with a place on the ground, found by
    area at once (numpy) - at 20,000 labels a step must not walk them."""

    def __init__(self, objects: list):
        self.objects = list(objects)
        self.by_id = {obj.object_id: obj for obj in self.objects}
        self._xs = np.array([obj.x for obj in self.objects], dtype=float)
        self._ys = np.array([obj.y for obj in self.objects], dtype=float)

    @classmethod
    def of(cls, project) -> "ObjectIndex":
        groups: dict = {}
        for image in project.images.values():
            for label in image.labels:
                groups.setdefault(label.object_id, []).append((label, image))
        objects = []
        for object_id, members in groups.items():
            obj = object_from(object_id, members)
            if obj is not None:
                objects.append(obj)
        return cls(objects)

    def within(self, bounds, wraps: bool = False) -> list:
        """The objects whose place lies inside ``bounds`` (west, south,
        east, north)."""
        if not self.objects or bounds is None:
            return []
        west, south, east, north = bounds
        xs, ys = self._xs, self._ys
        inside_x = (xs >= west) & (xs <= east)
        if wraps:
            inside_x |= (xs + WORLD_WIDTH >= west) & (xs + WORLD_WIDTH <= east)
        hit = inside_x & (ys >= south) & (ys <= north)
        return [self.objects[i] for i in np.flatnonzero(hit)]


def stops_on(index: ObjectIndex, bounds, wraps: bool, image_path: str,
             has_data) -> list:
    """The stops on an image: the objects whose place lies on it - on its
    data, ``has_data(obj, x, y)`` says, with x as drawn on the layer -
    and that are not labelled on it yet. North to south, then west to
    east, so a run of stops reads down the image."""
    here = path_key(image_path)
    stops = []
    for obj in index.within(bounds, wraps):
        if here in obj.paths:
            continue
        x = x_on(bounds, wraps, obj.x)
        if has_data(obj, x, obj.y):
            stops.append(Stop(obj, x, obj.y))
    stops.sort(key=lambda stop: (-stop.y, stop.x))
    return stops


def window(stop: Stop, floor_scale: float, view_w: int, view_h: int,
           margin: float = 1.5) -> tuple:
    """(west, south, east, north) to show a stop in: the view at
    ``floor_scale`` metres a screen pixel, centred on the stop - wider
    when that does not hold the object's clump, with ``margin`` round it.
    Strays are not held (2.10.9), nor anything round a stop at the
    object's own label on the image (Object Cycle): that label is what is
    looked at there."""
    view_w, view_h = max(1, view_w), max(1, view_h)
    reach = 0.0
    for x, y in (() if stop.labelled_here else stop.obj.clump):
        dx = x - stop.x
        if abs(dx) > WORLD_WIDTH / 2:       # the far side of 180 degrees
            dx -= math.copysign(WORLD_WIDTH, dx)
        reach = max(reach, abs(dx), abs(y - stop.y))
    scale = max(floor_scale, 2.0 * reach * margin / min(view_w, view_h))
    half_w, half_h = view_w * scale / 2.0, view_h * scale / 2.0
    return (stop.x - half_w, stop.y - half_h,
            stop.x + half_w, stop.y + half_h)
