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
"""
import os


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
