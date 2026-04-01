"""Entry point for the GeoLabel application."""
import os
import sys

# When running as a frozen executable, set PROJ_DATA so pyproj/rasterio can find proj.db
if getattr(sys, 'frozen', False):
    os.environ['PROJ_DATA'] = os.path.join(os.path.dirname(sys.executable), 'proj_data')

from PyQt5.QtWidgets import QApplication
from app.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("GeoLabel")
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
