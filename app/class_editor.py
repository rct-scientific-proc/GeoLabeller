"""Dialog for managing label classes."""
from PyQt5.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout
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
            "Removing a class will delete all its labels. A changed line "
            "is a class removed and another added: to rename a class and "
            "keep its labels, use Labels > Rename Class..."
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


def reserved_name_warning(parent):
    """The one message for a class named after the export's negatives."""
    QMessageBox.warning(
        parent, "Reserved class name",
        f"'{RESERVED_CLASS}' is reserved: the HDF5 export writes its "
        "sliding-window negatives under that name, and a label class "
        "with the same name would appear as a second, ambiguous "
        "'hard_negative' in every exported dataset.\n\n"
        "To give the model confusers, flag whole images instead: "
        "right click an image on the canvas and choose "
        "\"Hard negative source\".")


class RenameClassDialog(QDialog):
    """Labels > Rename Class... (2.9.2): a class, and its new name.

    Edit Classes reads a changed line as one class removed and another
    added, which deletes the labels; this keeps them. The dialog only
    collects the two names and says when the new one is a class already
    (a merge); the window asks about the merge, with the counts, and
    makes the change.
    """

    def __init__(self, current_classes: list[str], parent=None,
                 current: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Rename Class")
        self._classes = list(current_classes)
        layout = QVBoxLayout(self)
        note = QLabel(
            "Every label of the class takes the new name, on every image, "
            "and so does what Model Review remembers a chip became. The "
            "class keeps its place in the list, and so its colour. A name "
            "the project already has merges the two classes under it.")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.class_combo = QComboBox()
        self.class_combo.addItems(self._classes)
        if current in self._classes:
            self.class_combo.setCurrentText(current)
        form.addRow("Class:", self.class_combo)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("the new name")
        form.addRow("New name:", self.name_edit)
        layout.addLayout(form)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok
                                        | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.name_edit.textChanged.connect(self._update_hint)
        self.class_combo.currentTextChanged.connect(self._update_hint)
        self._update_hint()
        self.name_edit.setFocus()

    def chosen(self) -> tuple[str, str]:
        """(the class, its new name), the name stripped."""
        return self.class_combo.currentText(), self.name_edit.text().strip()

    def _update_hint(self):
        old, new = self.chosen()
        ok = self.buttons.button(QDialogButtonBox.Ok)
        if not new or new == old:
            self.hint.setText("")
            ok.setEnabled(False)
            return
        ok.setEnabled(True)
        if new in self._classes:
            self.hint.setText(
                f"'{new}' is a class already: the {old} labels will join "
                f"it, and the project will have one class, {new}.")
        else:
            self.hint.setText("")

    def _accept_if_valid(self):
        old, new = self.chosen()
        if not new or new == old:
            return
        if new == RESERVED_CLASS:
            reserved_name_warning(self)
            return
        self.accept()


class DescriptionEditorDialog(QDialog):
    """Edit the project's description presets (the Shift+1-9 picker).

    Unlike classes, removing a preset touches nothing: labels keep whatever
    description they carry - the presets are only a faster way to fill the
    field on newly placed labels.
    """

    def __init__(self, current_descriptions: list[str], parent=None):
        """Initialize with the current preset list."""
        super().__init__(parent)
        self.setWindowTitle("Edit Descriptions")
        self.setMinimumSize(340, 400)

        layout = QVBoxLayout(self)
        instructions = QLabel(
            "Enter description presets, one per line. The active preset "
            "(toolbar dropdown, or Shift+1-9; Shift+0 for none) is applied "
            "to every newly placed label.\n\n"
            "The first nine lines map to Shift+1-9. Removing a preset "
            "never changes existing labels."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlainText("\n".join(current_descriptions))
        self.text_edit.setPlaceholderText(
            "e.g.\nspring foliage\nwinter, bare branches")
        layout.addWidget(self.text_edit)

        button_layout = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        ok_btn.setDefault(True)
        button_layout.addWidget(ok_btn)
        layout.addLayout(button_layout)

    def get_descriptions(self) -> list[str]:
        """The preset list: stripped, non-empty, first occurrence wins."""
        lines = [line.strip()
                 for line in self.text_edit.toPlainText().split("\n")]
        seen = set()
        unique = []
        for line in lines:
            if line and line not in seen:
                seen.add(line)
                unique.append(line)
        return unique


class MaskNameEditorDialog(QDialog):
    """Edit the project's mask-name presets (the Snippet Editor's picker).

    Like descriptions, editing this list touches no existing data: a mask
    already painted keeps the name it was painted with. It only decides
    what the picker offers next - which is the point, because the name is
    what the exported channel is keyed by, and "hull", "Hull" and "hul"
    become three different masks otherwise.
    """

    def __init__(self, current_names: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Mask Names")
        self.setMinimumSize(340, 400)

        layout = QVBoxLayout(self)
        instructions = QLabel(
            "Enter mask names, one per line. The Snippet Editor offers these "
            "when a mask is added, and a name typed there joins the list.\n\n"
            "Renaming or removing a line never changes a mask already "
            "painted - it only changes what is offered next."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlainText("\n".join(current_names))
        self.text_edit.setPlaceholderText("e.g.\nhull\ndeck\nshadow")
        layout.addWidget(self.text_edit)

        button_layout = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        ok_btn.setDefault(True)
        button_layout.addWidget(ok_btn)
        layout.addLayout(button_layout)

    def get_mask_names(self) -> list[str]:
        """The preset list: stripped, non-empty, first occurrence wins."""
        names: dict[str, None] = {}
        for line in self.text_edit.toPlainText().split("\n"):
            line = line.strip()
            if line:
                names.setdefault(line, None)
        return list(names)
