"""Dialog for managing label classes."""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QMessageBox
)
from PyQt5.QtCore import Qt


class ClassEditorDialog(QDialog):
    """Dialog for editing label classes."""
    
    def __init__(self, current_classes: list[str], parent=None):
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
        ok_btn.clicked.connect(self.accept)
        ok_btn.setDefault(True)
        button_layout.addWidget(ok_btn)
        
        layout.addLayout(button_layout)
    
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
