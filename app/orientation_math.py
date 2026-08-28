"""Turning a drawn line into a label's two orientations.

The user drags start -> end across a snippet; that vector yields:

- principal_angle_rad: the standard unit-circle angle of the vector in
  UN-WARPED source-pixel space, radians in (-pi, pi]. Axes: x is +column
  (rightward along a row), y is UP the columns (-row). So a drag from top
  right to bottom left is about -3*pi/4 (-135 degrees), rightward along a
  row is 0, straight up the image is +pi/2. atan2 with the row axis negated
  gives exactly this; the one edge case is the negative-zero row delta that
  would return -pi, normalised to +pi so the range stays (-pi, pi].

- true_heading_deg: the geographic heading of the same vector, degrees
  clockwise from TRUE north in [0, 360). Both endpoints are pushed through
  the image's affine transform into its CRS, then to WGS84, and the forward
  azimuth between them comes from a geodesic solve - which is what makes the
  heading TRUE north rather than grid north, projection convergence and all.
  None when the image has no georeferencing.

Snippet coordinates are source-pixel coordinates (snippets are un-warped and
axis-aligned crops), so callers only add the crop's top-left offset.
"""
import math

from pyproj import Geod, Transformer

from .labels import WGS84

# WGS84 ellipsoid geodesics - the same datum the labels store lon/lat in.
_GEOD = Geod(ellps="WGS84")


def principal_angle_rad(col_start: float, row_start: float,
                        col_end: float, row_end: float) -> float | None:
    """Unit-circle angle of the start->end vector in image-pixel space.

    Returns radians in (-pi, pi], or None for a zero-length vector - a click
    without a drag carries no direction and must not be stored as one.
    """
    dcol = col_end - col_start
    drow = row_end - row_start
    if dcol == 0.0 and drow == 0.0:
        return None
    angle = math.atan2(-drow, dcol)
    if angle <= -math.pi:      # atan2(-0.0, negative) -> -pi; keep (-pi, pi]
        angle = math.pi
    return angle


def true_heading_deg(col_start: float, row_start: float,
                     col_end: float, row_end: float,
                     affine, crs) -> float | None:
    """True-north heading of the start->end vector, degrees CW in [0, 360).

    ``affine`` maps (col, row) -> CRS coordinates; ``crs`` is the image's
    native CRS. Returns None when either is missing (non-georeferenced
    imagery) or the vector has no length on the ground.
    """
    if affine is None or crs is None:
        return None
    if col_start == col_end and row_start == row_end:
        return None
    # Affine * (col, row): pixel centres, matching how labels store pixels.
    x1, y1 = affine * (col_start, row_start)
    x2, y2 = affine * (col_end, row_end)
    transformer = Transformer.from_crs(crs, WGS84, always_xy=True)
    lon1, lat1 = transformer.transform(x1, y1)
    lon2, lat2 = transformer.transform(x2, y2)
    azimuth, _back, dist = _GEOD.inv(lon1, lat1, lon2, lat2)
    if dist == 0.0:
        return None      # sub-pixel vector collapsed to one ground point
    return azimuth % 360.0
