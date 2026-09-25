"""How a group's imagery is drawn: which bands, and per-channel adjustments.

Asked for 2026-09-24: new imagery whose RGB composite is unviewable - only
one channel holds a picture a person can read - so a group needs to show a
chosen band as grayscale, or a chosen band in each of red, green and blue,
with brightness, contrast and gamma per channel.

Display only. The canvas, the Snippet Panel and the Snippet Editor draw
through these settings; nothing measured, stored or exported does - the
HDF5 dataset and the sub-image export read the raw bands exactly as before.

Settings belong to a project group and are inherited by its sub-groups
unless they set their own (``resolve``). They are saved in the project
(format 4.6) under "display_settings", keyed by group path.

The adjustments work on the 8-bit display values: after the per-file
2-98 percentile stretch that 16-bit and float imagery already gets, before
anything is drawn. So they are a 256-entry lookup table per channel, cheap
enough to redraw the canvas while a slider moves. Choosing different bands
means reading the file again, so that reloads the group's images.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field, replace

import numpy as np

MODE_RGB = "rgb"
MODE_SINGLE = "single"

BRIGHTNESS_RANGE = (-100, 100)
CONTRAST_RANGE = (-100, 100)
GAMMA_RANGE = (0.2, 5.0)
# Contrast is a gain around mid-grey: 8 ** (contrast / 100), so -100 is an
# eighth of the spread, 0 unchanged, +100 eight times it - enough for a
# picture that fills a sixth of the byte range. Brightness moves
# the level by 1.27 grey levels per step, +-100 reaching the ends.
CONTRAST_BASE = 8.0
BRIGHTNESS_STEP = 1.27
# Auto contrast maps this percentile window of a band onto 0..255.
AUTO_PERCENTILES = (2.0, 98.0)


def _clamp(value, low, high):
    return max(low, min(high, value))


@dataclass(frozen=True)
class ChannelAdjust:
    """Brightness, contrast and gamma for one displayed channel, or the
    channel hidden outright.

    ``hidden`` draws the channel as exactly zero whatever the sliders say
    (they are kept, so unhiding brings them back). The three sliders at
    their darkest only get within a few grey levels of that - asked for
    2026-09-24 as a clean way to drop a channel.
    """
    brightness: int = 0
    contrast: int = 0
    gamma: float = 1.0
    hidden: bool = False

    def is_identity(self) -> bool:
        return (not self.hidden and self.brightness == 0
                and self.contrast == 0 and abs(self.gamma - 1.0) < 1e-9)

    def clamped(self) -> "ChannelAdjust":
        return ChannelAdjust(
            int(round(_clamp(self.brightness, *BRIGHTNESS_RANGE))),
            int(round(_clamp(self.contrast, *CONTRAST_RANGE))),
            round(float(_clamp(self.gamma, *GAMMA_RANGE)), 2),
            bool(self.hidden))

    def table(self) -> np.ndarray:
        """This channel's 256-entry lookup table (uint8)."""
        if self.hidden:
            return np.zeros(256, dtype=np.uint8)
        values = np.arange(256, dtype=np.float64)
        # Brightness first, then contrast around mid-grey: a dim picture
        # is lifted into the middle and then spread, which is what Auto
        # does. Contrast first would push a dark picture below zero where
        # no brightness could reach it.
        values += self.brightness * BRIGHTNESS_STEP
        gain = CONTRAST_BASE ** (self.contrast / 100.0)
        values = (values - 128.0) * gain + 128.0
        values = np.clip(values, 0.0, 255.0) / 255.0
        if abs(self.gamma - 1.0) > 1e-9:
            # Gamma above 1 lifts the mid-tones, as in most image viewers.
            values = values ** (1.0 / self.gamma)
        return np.round(values * 255.0).astype(np.uint8)

    def to_dict(self) -> dict:
        data = {"brightness": self.brightness, "contrast": self.contrast,
                "gamma": self.gamma}
        if self.hidden:
            data["hidden"] = True
        return data

    @classmethod
    def from_dict(cls, data) -> "ChannelAdjust":
        if not isinstance(data, dict):
            return cls()
        try:
            return cls(float(data.get("brightness", 0)),
                       float(data.get("contrast", 0)),
                       float(data.get("gamma", 1.0)),
                       data.get("hidden") is True).clamped()
        except (TypeError, ValueError):
            return cls()


def _identity3():
    return (ChannelAdjust(), ChannelAdjust(), ChannelAdjust())


@dataclass(frozen=True)
class DisplaySettings:
    """One group's display: the bands drawn and each channel's adjustment.

    ``mode`` is MODE_RGB (``bands`` go to red, green and blue; None means
    the file's own first three, as always) or MODE_SINGLE (``band`` drawn
    as grayscale, adjusted by ``channels[0]``). Band numbers are 1-based,
    as GDAL and every GIS number them.
    """
    mode: str = MODE_RGB
    bands: tuple | None = None
    band: int = 1
    channels: tuple = field(default_factory=_identity3)

    # -- what it means ------------------------------------------------------

    def is_default(self) -> bool:
        return self == DisplaySettings()

    def band_choice(self) -> tuple:
        """What decides which bands are read. Two settings with the same
        choice need no re-read to switch between them."""
        if self.mode == MODE_SINGLE:
            return (MODE_SINGLE, self.band)
        return (MODE_RGB, self.bands)

    def source_bands(self, count: int) -> tuple:
        """The three 1-based source bands for red, green and blue, given a
        file of ``count`` bands. A band the file does not have falls back to
        its last one rather than failing the read."""
        count = max(1, int(count))
        if self.mode == MODE_SINGLE:
            b = _clamp(int(self.band), 1, count)
            return (b, b, b)
        if self.bands is None:
            return (1, 2, 3) if count >= 3 else (1, 1, 1)
        return tuple(_clamp(int(b), 1, count) for b in self.bands)

    def lut(self) -> "np.ndarray | None":
        """(3, 256) uint8 lookup table for red, green, blue - or None when
        nothing is adjusted, so callers can skip the work entirely."""
        return _cached_lut(self.mode, self.channels)

    # -- editing ------------------------------------------------------------

    def with_channel(self, index: int, adjust: ChannelAdjust):
        channels = list(self.channels)
        channels[index] = adjust.clamped()
        return replace(self, channels=tuple(channels))

    # -- file -----------------------------------------------------------------

    def to_dict(self) -> dict:
        data = {"mode": self.mode}
        if self.mode == MODE_SINGLE:
            data["band"] = int(self.band)
            data["channels"] = [self.channels[0].to_dict()]
        else:
            if self.bands is not None:
                data["bands"] = [int(b) for b in self.bands]
            data["channels"] = [c.to_dict() for c in self.channels]
        return data

    @classmethod
    def from_dict(cls, data) -> "DisplaySettings":
        """Tolerant: anything unreadable falls back to the default."""
        if not isinstance(data, dict):
            return cls()
        mode = data.get("mode", MODE_RGB)
        if mode not in (MODE_RGB, MODE_SINGLE):
            mode = MODE_RGB
        raw = data.get("channels") or []
        channels = [ChannelAdjust.from_dict(c) for c in raw[:3]]
        channels += [ChannelAdjust()] * (3 - len(channels))
        try:
            band = max(1, int(data.get("band", 1)))
        except (TypeError, ValueError):
            band = 1
        bands = data.get("bands")
        try:
            bands = (tuple(max(1, int(b)) for b in bands)
                     if bands is not None and len(bands) == 3 else None)
        except (TypeError, ValueError):
            bands = None
        if mode == MODE_SINGLE:
            # One channel: hiding it would only blank the image.
            channels = [replace(channels[0], hidden=False), ChannelAdjust(),
                        ChannelAdjust()]
            bands = None
        return cls(mode=mode, bands=bands, band=band,
                   channels=tuple(channels))


DEFAULT = DisplaySettings()

_lut_cache: dict = {}
_lut_lock = threading.Lock()


def _cached_lut(mode, channels):
    key = (mode, channels)
    with _lut_lock:
        if key in _lut_cache:
            return _lut_cache[key]
    if mode == MODE_SINGLE:
        tables = [channels[0].table()] * 3
        identity = channels[0].is_identity()
    else:
        tables = [c.table() for c in channels]
        identity = all(c.is_identity() for c in channels)
    lut = None if identity else np.ascontiguousarray(np.stack(tables))
    with _lut_lock:
        if len(_lut_cache) > 256:
            _lut_cache.clear()
        _lut_cache[key] = lut
    return lut


def apply_lut(rgb: np.ndarray, lut: "np.ndarray | None") -> np.ndarray:
    """``rgb`` (H, W, 3 or 4) through a (3, 256) lookup table.

    Returns a new contiguous array; an alpha channel is carried unchanged.
    With no table the input is returned as it is.
    """
    if lut is None:
        return rgb
    out = np.empty(rgb.shape, dtype=np.uint8)
    for channel in range(3):
        np.take(lut[channel], rgb[..., channel], out=out[..., channel])
    if rgb.shape[-1] > 3:
        out[..., 3:] = rgb[..., 3:]
    return out


def auto_adjust(histogram: np.ndarray) -> ChannelAdjust:
    """Brightness and contrast that stretch a band's 2-98 percentile window
    of display values over 0..255 (gamma back to 1).

    ``histogram`` is 256 counts of display values. An empty or flat band
    gets the identity.
    """
    counts = np.asarray(histogram, dtype=np.float64)
    total = counts.sum()
    if total <= 0:
        return ChannelAdjust()
    cumulative = np.cumsum(counts) / total
    low = int(np.searchsorted(cumulative, AUTO_PERCENTILES[0] / 100.0))
    high = int(np.searchsorted(cumulative, AUTO_PERCENTILES[1] / 100.0))
    if high <= low:
        return ChannelAdjust()
    gain = 255.0 / (high - low)
    contrast = 100.0 * math.log(gain, CONTRAST_BASE)
    contrast = _clamp(contrast, *CONTRAST_RANGE)
    gain = CONTRAST_BASE ** (round(contrast) / 100.0)
    # Place the window's low end at 0:
    # (low + shift - 128) * gain + 128 = 0.
    shift = 128.0 - 128.0 / gain - low
    brightness = _clamp(shift / BRIGHTNESS_STEP, *BRIGHTNESS_RANGE)
    return ChannelAdjust(brightness, contrast, 1.0).clamped()


# -- groups ------------------------------------------------------------------

def resolve(by_group: dict, group_path: str) -> DisplaySettings:
    """The settings that apply to ``group_path``: its own, else the nearest
    ancestor's, else the default."""
    path = (group_path or "").strip("/")
    while True:
        found = by_group.get(path)
        if found is not None:
            return found
        if not path:
            return DEFAULT
        path = path.rsplit("/", 1)[0] if "/" in path else ""


def settings_from_project_data(data) -> dict:
    """The "display_settings" entry of a project file, parsed."""
    if not isinstance(data, dict):
        return {}
    return {str(group): DisplaySettings.from_dict(value)
            for group, value in data.items()}


# -- the image -> settings lookup for snippet reads ---------------------------
#
# Snippets are read on worker threads by path; the main window installs a
# function that answers "which settings for this image" from the project.

_resolver = None


def set_resolver(resolver) -> None:
    """Install ``resolver(image_path) -> DisplaySettings`` (None to clear)."""
    global _resolver
    _resolver = resolver


def for_image(image_path: str) -> DisplaySettings:
    resolver = _resolver
    if resolver is None:
        return DEFAULT
    try:
        found = resolver(image_path)
    except Exception:          # noqa: BLE001 - a lookup must not fail a read
        return DEFAULT
    return found if isinstance(found, DisplaySettings) else DEFAULT


# -- reading pixels through the settings --------------------------------------

def display_rgb(src, window, nodata, scaling, settings: DisplaySettings):
    """A window of ``src`` as (H, W, 3) uint8, drawn through ``settings``.

    None when the window is entirely nodata. The bands are the settings'
    source bands, each through the file's own display stretch, then the
    lookup table.
    """
    from .snippets import apply_band_stretch, nodata_mask

    chosen = settings.source_bands(src.count)
    unique = sorted(set(chosen))
    data = src.read(indexes=unique, window=window)
    empty = nodata_mask(data, nodata)
    if empty is not None and bool(empty.all()):
        return None
    planes = {}
    for position, band in enumerate(unique):
        plane = apply_band_stretch(data[position], scaling, band - 1)
        if np.issubdtype(plane.dtype, np.floating):
            plane = np.nan_to_num(plane, nan=0.0, posinf=255.0, neginf=0.0)
        planes[band] = np.clip(plane, 0, 255).astype(np.uint8)
    rgb = np.stack([planes[b] for b in chosen], axis=-1)
    return np.ascontiguousarray(apply_lut(rgb, settings.lut()))


def band_histogram(src, band: int, scaling, max_side: int = 512):
    """256 counts of ``band``'s display values, from a decimated read.

    What the Display Settings window draws behind each channel, and what
    Auto works from. Nodata is left out.
    """
    from .snippets import apply_band_stretch, nodata_mask

    band = _clamp(int(band), 1, src.count)
    scale = max(src.width, src.height) / max_side
    out_w = max(1, int(src.width / max(scale, 1.0)))
    out_h = max(1, int(src.height / max(scale, 1.0)))
    data = src.read(band, out_shape=(out_h, out_w))
    empty = nodata_mask(data, src.nodata)
    plane = apply_band_stretch(data, scaling, band - 1)
    if np.issubdtype(np.asarray(plane).dtype, np.floating):
        plane = np.nan_to_num(plane, nan=0.0, posinf=255.0, neginf=0.0)
    plane = np.clip(plane, 0, 255).astype(np.uint8)
    values = plane[~empty] if empty is not None else plane.ravel()
    return np.bincount(values.ravel(), minlength=256)[:256]
