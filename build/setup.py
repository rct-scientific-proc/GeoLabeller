"""
cx_Freeze setup script for GeoLabeller.

Usage:
    python setup.py build       # Build executable
    python setup.py bdist_msi   # Build MSI installer (Windows only)
"""

import sys
from pathlib import Path
from cx_Freeze import setup, Executable

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Dependencies to include
build_exe_options = {
    # Optimization level: 0=none, 1=basic (-O), 2=full (-OO removes docstrings)
    "optimize": 2,
    
    # Compress Python modules into a zip file for smaller distribution
    "zip_include_packages": ["*"],
    "zip_exclude_packages": [],  # Keep empty unless specific packages have issues
    
    "packages": [
        "PyQt5",
        "PyQt5.QtCore",
        "PyQt5.QtGui",
        "PyQt5.QtWidgets",
        "rasterio",
        "numpy",
        "PIL",
        "affine",
        "json",
        "math",
        "pathlib",
        "traceback",
        "uuid",
        "enum",
        "xml",
    ],
    "excludes": [
        "tkinter",
        "unittest",
        "email",
        "html",
        "http",
        "pydoc",
        "doctest",
        "asyncio",
        "concurrent",
        "multiprocessing",
        "test",
        # Exclude Qt modules we don't use (avoid QML path issues)
        "PyQt5.QtQml",
        "PyQt5.QtQuick",
        "PyQt5.QtQuickWidgets",
        "PyQt5.QtNetwork",
        "PyQt5.QtSql",
        "PyQt5.QtMultimedia",
        "PyQt5.QtMultimediaWidgets",
        "PyQt5.QtBluetooth",
        "PyQt5.QtDesigner",
        "PyQt5.QtHelp",
        "PyQt5.QtLocation",
        "PyQt5.QtPositioning",
        "PyQt5.QtSensors",
        "PyQt5.QtSerialPort",
        "PyQt5.QtSvg",
        "PyQt5.QtTest",
        "PyQt5.QtWebChannel",
        "PyQt5.QtWebEngine",
        "PyQt5.QtWebEngineCore",
        "PyQt5.QtWebEngineWidgets",
        "PyQt5.QtWebSockets",
        "PyQt5.QtXml",
        "PyQt5.QtXmlPatterns",
    ],
    "include_files": [
        # Include any additional data files here
        # ("source_path", "dest_path"),
    ],
    "include_msvcr": True,  # Include Microsoft Visual C++ runtime (Windows)
}

# MSI installer options (Windows)
bdist_msi_options = {
    "upgrade_code": "{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}",
    "add_to_path": False,
    "initial_target_dir": r"[ProgramFilesFolder]\GeoLabeller",
}

# Base for GUI application (hides console on Windows)
base = None
if sys.platform == "win32":
    base = "gui"

# Target executable
target = Executable(
    script="../main.py",
    base=base,
    target_name="GeoLabeller",
    icon=None,  # Add icon path here if available: "icon.ico"
)

setup(
    name="GeoLabeller",
    version="1.0.0",
    description="A geospatial image labeling tool for creating ground truth datasets",
    author="Ryan",
    options={
        "build_exe": build_exe_options,
        "bdist_msi": bdist_msi_options,
    },
    executables=[target],
)
