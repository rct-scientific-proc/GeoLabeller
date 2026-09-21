"""GeoLabel application package."""
from . import gdal_config, settings_scope

# Before anything opens a raster: every way in - the window, the frozen
# build, a script, a test - imports this package first, so none of them can
# forget. (gdal_config imports nothing but os, so this costs nothing.)
gdal_config.apply()

# And before any window reads or writes a setting: a test run is sent to
# a throwaway folder, so it cannot record itself as the user's own habits
# (see settings_scope). For a real user this does nothing.
settings_scope.apply()
