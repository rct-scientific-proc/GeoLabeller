"""GeoLabel application package."""
from . import gdal_config

# Before anything opens a raster: every way in - the window, the frozen
# build, a script, a test - imports this package first, so none of them can
# forget. (gdal_config imports nothing but os, so this costs nothing.)
gdal_config.apply()
