"""Moved: the orientation editor now lives in the Snippet Editor package.

    app/snippet_editor/orientation_section.py   the editor and its grid

These names are re-exported so existing imports keep working while the
Snippet Editor is built (2.0.0). Nothing is defined here - edit the file
above. This signpost goes when the Orientation Editor window is retired.
(The angle maths was never here: it is app/orientation_math.py.)
"""
from .snippet_editor.orientation_section import (DERIVED_COLOR,
                                                 GRID_COLUMNS, MANUAL_COLOR,
                                                 MIN_DRAG_PX, PAGE_SIZE,
                                                 SNIPPET_SIZE,
                                                 OrientationCell,
                                                 OrientationEditor)

__all__ = ["DERIVED_COLOR", "GRID_COLUMNS", "MANUAL_COLOR", "MIN_DRAG_PX",
           "PAGE_SIZE", "SNIPPET_SIZE", "OrientationCell",
           "OrientationEditor"]
