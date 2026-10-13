"""GeoLabel application package."""
import sys

from . import gdal_config, settings_scope

# Before anything opens a raster: every way in - the window, the frozen
# build, a script, a test - imports this package first, so none of them can
# forget. (gdal_config imports nothing but os, so this costs nothing.)
gdal_config.apply()

# And before any window reads or writes a setting: a test run is sent to
# a throwaway folder, so it cannot record itself as the user's own habits
# (see settings_scope). For a real user this does nothing.
settings_scope.apply()

# Windows, source runs: the Microsoft C++ runtime the system has, loaded
# before PyQt5 brings its own older copy (14.26 in the 5.15 wheels). A
# DLL loaded first by name is the one every later load gets, and
# PyTorch's c10.dll refuses to initialise under Qt's (WinError 1114,
# 2026-10-13); the system's newer runtime serves Qt just as well. Only
# for Model Review's optional training (requirements-ml.txt): the
# installed build has no PyTorch and is left exactly as it was.
if sys.platform == "win32" and not getattr(sys, "frozen", False):
    import ctypes
    for _runtime in ("vcruntime140.dll", "vcruntime140_1.dll",
                     "msvcp140.dll"):
        try:
            ctypes.WinDLL(_runtime)
        except OSError:
            pass                    # no redistributable: Qt's own, then
