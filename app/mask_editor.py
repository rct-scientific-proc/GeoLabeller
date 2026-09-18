"""Moved: the mask editor now lives in the Snippet Editor package.

    app/snippet_editor/mask_section.py   the editor (MaskEditor)
    app/snippet_editor/single_view.py    its paint surface (MaskPaintCanvas)

These names are re-exported so existing imports keep working while the
Snippet Editor is built (2.0.0). Nothing is defined here - edit the files
above. This signpost goes when the Mask Editor window is retired.
"""
from .snippet_editor.mask_section import MASK_WORKLIST, MaskEditor
from .snippet_editor.single_view import (DEFAULT_BRUSH_PX, MASK_COLORS,
                                         MASK_SNIPPET_SIZE, MAX_DISPLAY_PX,
                                         MAX_ZOOM, MIN_ZOOM, MaskPaintCanvas,
                                         apply_display_adjust, display_scale)

__all__ = ["DEFAULT_BRUSH_PX", "MASK_COLORS", "MASK_SNIPPET_SIZE",
           "MASK_WORKLIST", "MAX_DISPLAY_PX", "MAX_ZOOM", "MIN_ZOOM",
           "MaskEditor", "MaskPaintCanvas", "apply_display_adjust",
           "display_scale"]
