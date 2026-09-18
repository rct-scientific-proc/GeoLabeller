"""The Snippet Editor: one window for everything recorded per snippet.

Planned for 2.0.0 to bring the orientation and mask editors into a single
window, with room for the per-snippet values still to come (the ML team's
"confidence" first). Everything for it lives in this package, apart from
the main window and apart from the non-GUI logic the exporters share
(masks.py, orientation_math.py, snippets.py). Nothing here imports the
main GUI; tests/test_snippet_editor_embed.py holds that.

    window.py                the Snippet Editor window: one list, the
                             Single and Grid views, the save bar
    strip.py                 the snippet list: class and Show filters,
                             counts, stepping - shared by every section
    single_view.py           one snippet, zoomable, with paintable layers
    mask_section.py          the mask editor (MaskEditor)
    orientation_section.py   the orientation editor and its grid
                             (OrientationEditor)

Both editors are still their own windows today (Labels > Mask Editor,
Labels > Orientation Editor) and can also be built as plain panels around
a shared strip, which is how the Snippet Editor holds them. The package
is being filled in piece by piece, each piece shipping on its own with
the existing windows still working; see the 2.0.0 plan for the order.
"""
from .mask_section import MaskEditor
from .orientation_section import OrientationEditor
from .strip import SnippetStrip, Worklist
from .window import SnippetEditor

__all__ = ["MaskEditor", "OrientationEditor", "SnippetEditor",
           "SnippetStrip", "Worklist"]
