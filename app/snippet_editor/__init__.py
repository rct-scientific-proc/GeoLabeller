"""The Snippet Editor: one window for everything recorded per snippet.

Since 2.0.0, the orientation and mask editors in a single window, with
room for more per-snippet values (the ML team's "confidence" was the
first). Everything for it lives in this package, apart from
the main window and apart from the non-GUI logic the exporters share
(masks.py, orientation_math.py, snippets.py). Nothing here imports the
main GUI; tests/test_snippet_editor_embed.py holds that.

    window.py                the Snippet Editor window: one list, the
                             Single and Grid views, the column of
                             sections, the save bar
    section.py               the Section interface every section speaks
    strip.py                 the snippet list: class and Show filters,
                             counts, stepping - shared by every section
    single_view.py           one snippet, zoomable, with paintable layers,
                             an arrow overlay and the Paint/Orient tools
    orientation_section.py   the Grid view (OrientationEditor), and
                             OrientationSection
    mask_section.py          the Single view's mask painting
                             (MaskEditor), and MaskSection
    confidence_section.py    ConfidenceSection - the 1-5 rating; the
                             smallest complete section, to copy for the
                             next per-snippet value

Through 1.x the two editors were windows of their own (Labels > Mask
Editor, Labels > Orientation Editor). Now they are panels built around
the window's one shared strip, and exist nowhere else.
"""
from .mask_section import MaskEditor
from .orientation_section import OrientationEditor
from .strip import SnippetStrip, Worklist
from .window import SnippetEditor

__all__ = ["MaskEditor", "OrientationEditor", "SnippetEditor",
           "SnippetStrip", "Worklist"]
