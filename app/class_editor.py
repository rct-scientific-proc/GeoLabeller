"""Dialog for managing label classes."""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton
)

# The H5 export appends its own class of this name for the sliding-window
# negatives. A user class with the same name duplicates the exported class
# list: labels land under a second same-named class as gt=True while the
# export's negative bookkeeping points at the first - two "hard_negative"
# columns a consumer cannot tell apart. Reserved rather than worked around;
# the way to feed confusers to the model is the image flag (right click an
# image on the canvas).
RESERVED_CLASS = "hard_negative"


class ClassEditorDialog(QDialog):
    """Dialog for editing label classes."""

    def __init__(self, current_classes: list[str], parent=None):
        """Initialize the class editor dialog with the current classes."""
        super().__init__(parent)
        self.setWindowTitle("Edit Classes")
        self.setMinimumSize(300, 400)

        self._setup_ui(current_classes)

    def _setup_ui(self, current_classes: list[str]):
        """Set up the dialog UI."""
        layout = QVBoxLayout(self)

        # Instructions
        instructions = QLabel(
            "Enter class names, one per line.\n"
            "Removing a class will delete all its labels."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        # Text editor for classes
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlainText("\n".join(current_classes))
        self.text_edit.setPlaceholderText("Enter class names here...")
        layout.addWidget(self.text_edit)

        # Buttons
        button_layout = QHBoxLayout()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self._accept_if_valid)
        ok_btn.setDefault(True)
        button_layout.addWidget(ok_btn)

        layout.addLayout(button_layout)

    def _accept_if_valid(self):
        """Close only when no class uses the reserved export name."""
        if RESERVED_CLASS in self.get_classes():
            QMessageBox.warning(
                self, "Reserved class name",
                f"'{RESERVED_CLASS}' is reserved: the HDF5 export writes its "
                "sliding-window negatives under that name, and a label class "
                "with the same name would appear as a second, ambiguous "
                "'hard_negative' in every exported dataset.\n\n"
                "To give the model confusers, flag whole images instead: "
                "right click an image on the canvas and choose "
                "\"Hard negative source\".")
            return
        self.accept()

    def get_classes(self) -> list[str]:
        """Get the list of classes from the text editor."""
        text = self.text_edit.toPlainText()
        # Split by newlines, strip whitespace, remove empty lines
        classes = [line.strip() for line in text.split("\n")]
        classes = [c for c in classes if c]
        # Remove duplicates while preserving order
        seen = set()
        unique_classes = []
        for c in classes:
            if c not in seen:
                seen.add(c)
                unique_classes.append(c)
        return unique_classes
