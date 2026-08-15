"""Jump the view to a typed latitude/longitude.

Coordinates get copied out of all sorts of places - a spreadsheet, a mapping
site, a field notebook - so ``parse_lat_lon`` accepts the usual spellings
rather than demanding one: decimal degrees, hemisphere suffixes or prefixes,
and degrees/minutes/seconds.

Contents:
- ``parse_lat_lon`` - text to (lat, lon), or None (no Qt).
- ``format_lat_lon`` - the reverse, for confirming what was understood.
- ``GoToLocationDialog`` - the entry dialog.
- ``WaypointDialog`` - the same coordinate entry, plus a name, for waypoints.
"""
import re

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QSpinBox,
    QDialogButtonBox,
)

# Degree/minute/second marks, including the typographic ones a copy-paste
# tends to bring along, are just separators once the numbers are tokenised.
_DEGREE_MARKS = re.compile(r"[°º'′’\"″”]")
_TOKENS = re.compile(r"[-+]?\d+(?:\.\d+)?|[NSEW]|,")

MAX_LATITUDE = 85.05112878  # Web Mercator is undefined beyond this


def _tokenise(text: str) -> list[list[str]] | None:
    """Split text into one token group per coordinate, or None if unusable."""
    cleaned = _DEGREE_MARKS.sub(" ", text.upper())
    groups: list[list[str]] = []
    current: list[str] = []
    saw_hemisphere = False
    for token in _TOKENS.findall(cleaned):
        if token == ",":
            if current:
                groups.append(current)
                current = []
        elif token in "NSEW":
            saw_hemisphere = True
            has_letter = any(t in "NSEW" for t in current)
            has_number = any(t not in "NSEW" for t in current)
            if has_letter:
                # Already labelled, so this letter opens the next coordinate:
                # "N40 W73".
                groups.append(current)
                current = [token]
            elif has_number:
                # Trailing letter closes the coordinate it follows: "40N 73W".
                current.append(token)
                groups.append(current)
                current = []
            else:
                current = [token]
        else:
            current.append(token)
    if current:
        groups.append(current)

    if len(groups) == 1 and not saw_hemisphere:
        # "40.75 -73.98" or "40 45 12 73 58 59": no separator at all, so split
        # the numbers down the middle.
        numbers = groups[0]
        if len(numbers) % 2 or not numbers:
            return None
        half = len(numbers) // 2
        groups = [numbers[:half], numbers[half:]]
    return groups if len(groups) == 2 else None


def _to_degrees(group: list[str]) -> tuple[float, str | None] | None:
    """Turn one token group into (degrees, hemisphere letter or None)."""
    hemisphere = None
    numbers = []
    for token in group:
        if token in "NSEW":
            hemisphere = token
        else:
            numbers.append(float(token))
    if not numbers or len(numbers) > 3:
        return None
    if any(n < 0 for n in numbers[1:]):
        return None  # only the degrees term carries the sign
    negative = numbers[0] < 0
    value = abs(numbers[0])
    if len(numbers) > 1:
        value += numbers[1] / 60.0
    if len(numbers) > 2:
        value += numbers[2] / 3600.0
    if negative or hemisphere in ("S", "W"):
        value = -value
    return value, hemisphere


def parse_lat_lon(text: str) -> tuple[float, float] | None:
    """Read a coordinate pair, or return None if it can't be understood.

    Accepts decimal degrees ("40.7536, -73.9832"), hemisphere letters on
    either side of the number ("40.7536N 73.9832W", "N40 W73") and
    degrees/minutes/seconds ("40 45 12.9 N, 73 58 59.5 W"). Latitude comes
    first unless the hemisphere letters say otherwise.
    """
    if not text or not text.strip():
        return None
    groups = _tokenise(text)
    if groups is None:
        return None
    parsed = [_to_degrees(group) for group in groups]
    if any(p is None for p in parsed):
        return None
    (first, first_hemisphere), (second, second_hemisphere) = parsed

    # Hemisphere letters name the axis, so "W73.98, N40.75" works too.
    if first_hemisphere in ("E", "W") or second_hemisphere in ("N", "S"):
        lat, lon = second, first
    else:
        lat, lon = first, second

    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        return None
    return lat, lon


def format_lat_lon(lat: float, lon: float) -> str:
    """Render a coordinate pair the way the dialog echoes it back."""
    return (f"{abs(lat):.6f}° {'S' if lat < 0 else 'N'}, "
            f"{abs(lon):.6f}° {'W' if lon < 0 else 'E'}")


class GoToLocationDialog(QDialog):
    """Ask for a coordinate and how wide a view to land in."""

    def __init__(self, parent=None, defaults=None):
        """Build the dialog. ``defaults`` re-fills the last entry and width."""
        super().__init__(parent)
        self._defaults = dict(defaults or {})
        self._parsed: tuple[float, float] | None = None
        self.setWindowTitle("Go to Coordinates")
        self.setMinimumWidth(420)
        self._build_ui()

    def _build_ui(self):
        """Assemble the dialog widgets."""
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Latitude / longitude:"))

        self.coord_edit = QLineEdit()
        self.coord_edit.setPlaceholderText("40.7536, -73.9832")
        self.coord_edit.setToolTip(
            "Decimal degrees, hemisphere letters or degrees/minutes/seconds:\n"
            "  40.7536, -73.9832\n"
            "  40.7536N 73.9832W\n"
            "  40 45 12.9 N, 73 58 59.5 W")
        self.coord_edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.coord_edit)

        self._echo = QLabel("")
        self._echo.setWordWrap(True)
        layout.addWidget(self._echo)

        width_row = QHBoxLayout()
        width_row.addWidget(QLabel("View width:"))
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 1_000_000)
        self.width_spin.setValue(int(self._defaults.get("width", 200)))
        self.width_spin.setSuffix(" m")
        self.width_spin.setToolTip(
            "How much ground the view covers on arrival.")
        width_row.addWidget(self.width_spin)
        width_row.addStretch(1)
        layout.addLayout(width_row)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Go")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.coord_edit.setText(self._defaults.get("text", ""))
        self.coord_edit.selectAll()
        self._on_text_changed()

    def _on_text_changed(self):
        """Re-parse as the user types and echo back what was understood."""
        text = self.coord_edit.text().strip()
        self._parsed = parse_lat_lon(text)
        if self._parsed is not None:
            lat, lon = self._parsed
            if abs(lat) > MAX_LATITUDE:
                self._echo.setStyleSheet("color: #cc0000;")
                self._echo.setText(
                    f"Latitude must be within {MAX_LATITUDE:g}° of the "
                    "equator - the map projection has no pixels beyond it.")
                self._parsed = None
            else:
                self._echo.setStyleSheet("color: #007700;")
                self._echo.setText(format_lat_lon(lat, lon))
        elif text:
            self._echo.setStyleSheet("color: #cc0000;")
            self._echo.setText("Could not read a latitude and longitude here.")
        else:
            self._echo.setStyleSheet("")
            self._echo.setText("")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(
            self._parsed is not None)

    def coordinates(self) -> tuple[float, float] | None:
        """The parsed (latitude, longitude), or None if nothing valid."""
        return self._parsed

    def view_width_m(self) -> int:
        """Chosen view width, in ground metres."""
        return self.width_spin.value()

    def entered_text(self) -> str:
        """What the user actually typed, for re-filling next time."""
        return self.coord_edit.text().strip()


class WaypointDialog(QDialog):
    """Ask for a waypoint's name and coordinate.

    Uses the same coordinate parsing and live echo as ``GoToLocationDialog``,
    so anything that works in "Go to Coordinates" works here too. The name is
    optional - blank means the project auto-names it "WP n".
    """

    def __init__(self, parent=None, name: str = "", coord_text: str = ""):
        """Build the dialog, optionally pre-filled for editing."""
        super().__init__(parent)
        self._parsed: tuple[float, float] | None = None
        self.setWindowTitle("Add Waypoint")
        self.setMinimumWidth(420)
        self._build_ui(name, coord_text)

    def _build_ui(self, name: str, coord_text: str):
        """Assemble the dialog widgets."""
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Name (optional):"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Left blank, this becomes \"WP 1\"")
        self.name_edit.setText(name)
        layout.addWidget(self.name_edit)

        layout.addWidget(QLabel("Latitude / longitude:"))
        self.coord_edit = QLineEdit()
        self.coord_edit.setPlaceholderText("40.7536, -73.9832")
        self.coord_edit.setToolTip(
            "Decimal degrees, hemisphere letters or degrees/minutes/seconds:\n"
            "  40.7536, -73.9832\n"
            "  40.7536N 73.9832W\n"
            "  40 45 12.9 N, 73 58 59.5 W")
        self.coord_edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.coord_edit)

        self._echo = QLabel("")
        self._echo.setWordWrap(True)
        layout.addWidget(self._echo)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("Add")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.coord_edit.setText(coord_text)
        self._on_text_changed()

    def _on_text_changed(self):
        """Re-parse as the user types and echo back what was understood."""
        text = self.coord_edit.text().strip()
        self._parsed = parse_lat_lon(text)
        if self._parsed is not None:
            lat, lon = self._parsed
            if abs(lat) > MAX_LATITUDE:
                self._echo.setStyleSheet("color: #cc0000;")
                self._echo.setText(
                    f"Latitude must be within {MAX_LATITUDE:g}° of the "
                    "equator - the map projection has no pixels beyond it.")
                self._parsed = None
            else:
                self._echo.setStyleSheet("color: #007700;")
                self._echo.setText(format_lat_lon(lat, lon))
        elif text:
            self._echo.setStyleSheet("color: #cc0000;")
            self._echo.setText("Could not read a latitude and longitude here.")
        else:
            self._echo.setStyleSheet("")
            self._echo.setText("")
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(
            self._parsed is not None)

    def coordinates(self) -> tuple[float, float] | None:
        """The parsed (latitude, longitude), or None if nothing valid."""
        return self._parsed

    def waypoint_name(self) -> str:
        """The typed name, blank if the user left it empty."""
        return self.name_edit.text().strip()
