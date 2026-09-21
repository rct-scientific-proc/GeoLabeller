"""How GeoLabeller asks GDAL to work. One place, so the tuning can be found.

Measured 2026-09-20 (tests/bench_load_path.py), after users wondered
whether cycling reverse-alphabetically cost them disk locality. It does
not: order made no difference, and the disk is about a tenth of a load.
What a level load spends its time on is the reprojection into the scene's
projection - 80-85% of it at every level - and that ran on one thread.

  * WARP_THREADS: threads for one reprojection (rasterio's num_threads).
    One load is 2x faster at the level a fitted image uses, 2.4x at full
    resolution, and a zoomed-in detail tile 2.5x, for identical pixels.
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
