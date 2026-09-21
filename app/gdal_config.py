"""How GeoLabeller asks GDAL to work. One place, so the tuning can be found.

Measured 2026-09-20 (tests/bench_load_path.py), after users wondered
whether cycling reverse-alphabetically cost them disk locality. It does
not: order made no difference, and the disk is about a tenth of a load.
What a level load spends its time on is the reprojection into the scene's
projection - 80-85% of it at every level - and that ran on one thread.

  * WARP_THREADS: threads for one reprojection (rasterio's num_threads).
    One load is 2x faster at the level a fitted image uses, 2.4x at full
    resolution, and a zoomed-in detail tile 2.5x, for identical pixels.
  * apply(): GDAL's own decode threads (GDAL_NUM_THREADS), set once for
    the process as the app package is imported. A full-resolution
    three-band read of a tiled, compressed GeoTIFF goes 180 -> 28 ms. It
    does nothing for the small overview reads a fitted image makes, so
    this is for zoomed-in loads and detail tiles; exports that write
    compressed tiles get it too.
  * opened(): open a raster, skipping GDAL's listing of its directory
    where that directory holds no sidecar files. GDAL lists the whole
    folder on every open, looking for them - in a folder of ten thousand
    images, ten thousand names per open: more than half of an open on a
    local disk (3.5 -> 1.5 ms), and the expensive part of one on a
    network share. Only the directory import used to skip it.
"""
import os
import threading
from contextlib import contextmanager

# Files GDAL looks for beside a raster. A folder holding any of them keeps
# its listing: skip it there and GDAL never finds the file - an external
# pyramid (.ovr) would silently stop being used.
SIDECAR_SUFFIXES = (
    ".ovr", ".aux.xml", ".aux", ".rrd", ".msk", ".tab", ".prj",
    ".tfw", ".tifw", ".tiffw", ".wld", ".jgw", ".pgw", ".j2w")

_SKIP_LISTING = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR"}

# folder -> True when it is known to hold no sidecar file. Learnt from the
# import's own walk where there was one (note_directory), otherwise by
# listing the folder ONCE, the first time something in it is opened. It is
# not refreshed: a sidecar added while the app is running is found at the
# next start, the same as it was for the import.
_sidecar_free: dict = {}
_sidecar_free_lock = threading.Lock()


def threads_for(cores: "int | None") -> int:
    """Threads one warp (or one decode) may use on a machine of ``cores``.

    About a quarter of them, at most four. The canvas's load pool already
    runs up to four loads at once, so a warp that took every core would
    only fight its neighbours - measured, four loads at a time finish no
    sooner with threaded warps than without. What the threads buy is the
    ONE load the user is waiting on, which is what a cycle step feels
    like. On four cores this is 1: exactly the old behaviour.
    """
    return max(1, min(4, (cores or 1) // 4))


# Read where it is used (gdal_config.WARP_THREADS), not imported by value,
# so a test can vary it.
WARP_THREADS = threads_for(os.cpu_count())


def apply(environ=None, cores: "int | None" = None) -> None:
    """Make the process-wide GDAL settings. Called as ``app`` is imported.

    The environment is where GDAL looks when nothing more local is set, so
    a value put there reaches every open in every thread without each call
    site having to remember it - and it is read when a file is opened, not
    when GDAL loads, so setting it here is early enough.

    setdefault, never an assignment: a value already in the environment is
    somebody's decision - an admin's, or a user chasing a problem - and it
    wins. With too few cores for threads to pay, nothing is set at all and
    GDAL keeps its own default.
    """
    environ = os.environ if environ is None else environ
    threads = threads_for(os.cpu_count() if cores is None else cores)
    if threads > 1:
        environ.setdefault("GDAL_NUM_THREADS", str(threads))


def _folder_of(path: str) -> str:
    return os.path.normcase(os.path.dirname(os.path.abspath(path)))


def note_directory(directory, has_sidecars: bool) -> None:
    """Record what a walk of ``directory`` found, sparing a listing later."""
    key = os.path.normcase(os.path.abspath(str(directory)))
    with _sidecar_free_lock:
        _sidecar_free[key] = not has_sidecars


def forget_directories() -> None:
    with _sidecar_free_lock:
        _sidecar_free.clear()


def _holds_no_sidecars(directory: str) -> bool:
    try:
        with os.scandir(directory) as entries:
            return not any(entry.name.lower().endswith(SIDECAR_SUFFIXES)
                           for entry in entries)
    except OSError:
        # Not knowing is not the same as knowing there are none.
        return False


def open_env(path: str) -> dict:
    """The GDAL options to open ``path`` under (for rasterio.Env)."""
    folder = _folder_of(path)
    with _sidecar_free_lock:
        known = _sidecar_free.get(folder)
    if known is None:
        # Outside the lock: a slow share must not hold up other folders.
        # Two threads may both list a new folder once; both get the same
        # answer.
        known = _holds_no_sidecars(folder)
        with _sidecar_free_lock:
            _sidecar_free[folder] = known
    return dict(_SKIP_LISTING) if known else {}


@contextmanager
def opened(path, *args, **kwargs):
    """``rasterio.open(path, ...)``, under :func:`open_env`.

    rasterio is imported here, not at the top: the app package imports
    this module before anything else, and that must stay free.
    """
    import rasterio
    with rasterio.Env(**open_env(str(path))):
        with rasterio.open(path, *args, **kwargs) as src:
            yield src
