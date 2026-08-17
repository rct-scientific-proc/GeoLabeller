"""The keyboard-shortcut reference (F1), laid out to fit on the screen.

The reference has grown a section per feature, and as a single stacked column
it ran taller than a laptop screen - with the last few sections simply
unreachable, since a QMessageBox neither scrolls nor resizes.

Here the sections are data rather than one HTML blob, so they can be dealt
into as many columns as the screen is wide enough for, balanced by height. The
result is wide and short instead of narrow and tall, and it sits in a scroll
area besides, so it stays usable on a small screen or at a large font size.
"""
from html import escape

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QScrollArea, QVBoxLayout, QWidget,
)

# (heading, [(keys, what it does), ...]) - rendered as a small table each.
SHORTCUT_SECTIONS = [
    ("File Operations", [
        ("Ctrl+N", "New Project"),
        ("Ctrl+Shift+P", "Open Project"),
        ("Ctrl+S", "Save Project"),
        ("Ctrl+Shift+S", "Save Project As"),
        ("Ctrl+O", "Add Image (GeoTIFF + custom)"),
        ("Ctrl+Shift+O", "Add Directory"),
        ("Ctrl+Q", "Exit"),
    ]),
    ("Navigation", [
        ("Mouse Wheel", "Zoom in/out"),
        ("Click + Drag", "Pan (in Pan mode)"),
        ("Right-click", "Context menu"),
        ("Ctrl+G", "Go to a latitude/longitude"),
        ("Ctrl+Shift+W", "Add waypoint by coordinates"),
    ]),
    ("Mode Switching", [
        ("P", "Pan mode"),
        ("L", "Label mode"),
        ("C", "Cycle mode (group-based)"),
        ("V", "View Cycle (layers in view)"),
        ("W", "Waterfall mode (stack group vertically)"),
        ("R", "Ruler mode"),
    ]),
    ("Labeling (Label & Cycle modes)", [
        ("Left-click", "Place label"),
        ("Right-click label", "Label options (remove, link, measure)"),
        ("Right-click + drag", "Pan the view"),
        ("Ctrl+Left-click label", "Label options (Cycle modes)"),
        ("1-9", "Quick-switch to class 1-9"),
        ("Escape", "Cancel link mode"),
    ]),
    ("Chain Linking", [
        ("K", "Toggle chain-link (any labeling mode)"),
        ("Left-click label", "Add to chain (first click anchors)"),
        ("N", "Finish this chain, start a new one"),
        ("Escape", "Exit chain-link (links are kept)"),
    ]),
    ("Cycle Modes (Cycle / View Cycle)", [
        ("Space", "Next layer (unchecks current)"),
        ("Ctrl+Space", "Go back to previous layer"),
        ("Left-click", "Place label"),
        ("Right-click label", "Label options (remove, link, measure)"),
        ("Right-click + drag", "Pan around"),
        ("Mouse wheel", "Zoom in/out"),
    ]),
    ("Waterfall Mode", [
        ("Hold Space", "Glide up through the image stack"),
        ("Hold Ctrl+Space", "Glide down (starts at the bottom)"),
        ("Left-click", "Place label (group labels stay visible)"),
        ("Right-click label", "Label options (remove, link, measure)"),
        ("Right-click + drag", "Pan within the stack"),
        ("Mouse wheel", "Zoom in/out"),
    ]),
    ("Measuring", [
        ("Shift + Drag", "Measure distance, without changing mode"),
        ("M", "Measure label under cursor (length, width)"),
        ("Escape", "Clear the measurement or the go-to marker"),
    ]),
    ("Waypoints", [
        ("Right-click map", "Add waypoint here (Pan mode, geo)"),
        ("Ctrl+Shift+W", "Add waypoint by coordinate"),
        ("Right-click marker", "Go to / Rename / Remove"),
        ("Double-click in panel", "Fly to that waypoint"),
        ("Show on map", "Hide or show every waypoint marker"),
    ]),
    ("Layer Panel", [
        ("Checkbox", "Toggle layer/group visibility"),
        ("Right-click group", "Select/Unselect, Expand/Collapse all"),
        ("Right-click layer", "Zoom to layer, Remove"),
        ("Drag & Drop", "Reorder layers/groups"),
    ]),
    ("Labeled Images Panel", [
        ("Checkbox", "Toggle image visibility (synced)"),
        ("Right-click label", "Zoom to label or layer"),
        ("Right-click group", "Select/Unselect all in group"),
    ]),
    ("Help", [
        ("F1", "Show this help"),
    ]),
]

TIPS = [
    "Layers default to hidden when loading - expand groups and check to display",
    "Turning on a layer automatically checks its parent groups",
    "Add Directory creates a root group named after the selected folder",
    "Visibility syncs between Layer Panel and Labeled Images Panel",
    "Custom file readers are auto-detected - registered formats appear in "
    "file dialogs",
]

# Roughly how wide one column of shortcuts wants to be, in pixels.
_COLUMN_WIDTH = 460
# A heading costs about this many rows of height, for balancing purposes.
_HEADING_WEIGHT = 2
_MAX_COLUMNS = 3
# Header, tips spacing and the button row, outside the scrolling content.
_CHROME_HEIGHT = 120


def _section_html(title: str, rows) -> str:
    """One section as a heading plus a two-column table."""
    cells = "".join(
        f"<tr><td valign='top'><b>{escape(keys)}</b>&nbsp;&nbsp;</td>"
        f"<td valign='top'>{escape(text)}</td></tr>"
        for keys, text in rows)
    return (f"<h3 style='margin-bottom:2px'>{escape(title)}</h3>"
            f"<table cellspacing='0' cellpadding='1'>{cells}</table>")


def _balance(sections, columns: int):
    """Deal sections into ``columns`` lists of roughly equal height.

    Keeps the original order within each column - the reference reads top to
    bottom, then across - and starts a new column once the current one has had
    its share, so no column ends up wildly longer than the rest.
    """
    if columns <= 1:
        return [list(sections)]
    weights = [len(rows) + _HEADING_WEIGHT for _title, rows in sections]
    target = sum(weights) / columns

    result, current, used = [], [], 0
    for section, weight in zip(sections, weights):
        # Start a new column when this one has met its share, unless doing so
        # would leave too few sections for the columns still to come.
        remaining_columns = columns - len(result)
        if (current and used + weight / 2 > target
                and len(sections) - len(result) > remaining_columns
                and remaining_columns > 1):
            result.append(current)
            current, used = [], 0
        current.append(section)
        used += weight
    if current:
        result.append(current)
    return result


class ShortcutsDialog(QDialog):
    """The F1 reference: multi-column, scrollable, and capped to the screen."""

    def __init__(self, parent=None):
        """Build the dialog, choosing a column count that suits the screen."""
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts & Tips")
        available = self._available_size()
        columns = max(1, min(_MAX_COLUMNS,
                             int(available.width() * 0.9) // _COLUMN_WIDTH))
        self._build_ui(columns)
        self._fit_to_screen(available, columns)

    def _available_size(self):
        """Usable area of the screen this dialog is opening on."""
        screen = None
        if self.parent() is not None and hasattr(self.parent(), "screen"):
            screen = self.parent().screen()
        if screen is None:
            screen = QApplication.primaryScreen()
        return screen.availableGeometry()

    def _build_ui(self, columns: int):
        """Assemble the header, the columns of sections, and the tips."""
        layout = QVBoxLayout(self)

        heading = QLabel("<h2 style='margin:0'>Keyboard Shortcuts</h2>")
        heading.setTextFormat(Qt.RichText)
        layout.addWidget(heading)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(24)
        for column in _balance(SHORTCUT_SECTIONS, columns):
            label = QLabel("".join(_section_html(title, rows)
                                   for title, rows in column))
            label.setTextFormat(Qt.RichText)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            body_layout.addWidget(label, 1)
        body_layout.addStretch(0)

        tips = QLabel("<h3 style='margin-bottom:2px'>Tips</h3><ul>"
                      + "".join(f"<li>{escape(tip)}</li>" for tip in TIPS)
                      + "</ul>")
        tips.setTextFormat(Qt.RichText)
        tips.setWordWrap(True)
        body_layout_wrapper = QVBoxLayout()
        body_layout_wrapper.setContentsMargins(0, 0, 0, 0)
        body_layout_wrapper.addWidget(body)
        body_layout_wrapper.addWidget(tips)
        body_layout_wrapper.addStretch(1)
        holder = QWidget()
        holder.setLayout(body_layout_wrapper)

        # A scroll area as well as the columns: the columns make it fit on a
        # normal screen, this guarantees every row is reachable on a small one
        # or at a large font size.
        scroll = QScrollArea()
        self._scroll = scroll
        scroll.setWidget(holder)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _fit_to_screen(self, available, columns: int):
        """Size to the laid-out content, never beyond what the screen shows.

        Measured from the scroll area's contents rather than the dialog's own
        size hint, which over-estimates badly for stacked rich text - the
        window would open far taller than it needed to be.
        """
        width = min(int(available.width() * 0.92),
                    columns * _COLUMN_WIDTH + 60)
        content = self._scroll.widget().sizeHint().height()
        height = min(int(available.height() * 0.9), content + _CHROME_HEIGHT)
        self.resize(width, max(420, height))
