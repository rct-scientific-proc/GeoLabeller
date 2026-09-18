"""The Snippet Editor: one window for everything recorded per snippet.

Planned for 2.0.0 to bring the orientation and mask editors into a single
window, with room for the per-snippet values still to come (the ML team's
"confidence" first). Everything for it lives in this package, apart from
the main window and apart from the non-GUI logic the exporters share
(masks.py, orientation_math.py, snippets.py):

    strip.py      the snippet list: class and Show filters, counts, stepping

It is being moved in piece by piece, each piece shipping on its own with
the existing windows still working; see the 2.0.0 plan for the order.
"""
from .strip import SnippetStrip, Worklist

__all__ = ["SnippetStrip", "Worklist"]
