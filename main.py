"""Entry point for the GeoLabel application."""
import os
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

# When running as a frozen executable, set PROJ_DATA so pyproj/rasterio can find proj.db
if getattr(sys, 'frozen', False):
    os.environ['PROJ_DATA'] = os.path.join(os.path.dirname(sys.executable), 'proj_data')


def _get_log_dir() -> Path:
    """Return a writable location for startup crash logs."""
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "GeoLabeller" / "logs"
    return Path(tempfile.gettempdir()) / "GeoLabeller" / "logs"


def _log_startup_exception(exc_type, exc_value, exc_traceback) -> Optional[Path]:
    """Write the full exception traceback to a persistent log file."""
    try:
        log_dir = _get_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "startup_errors.log"

        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] Unhandled startup exception\n")
            handle.write(f"Python: {sys.version}\n")
            handle.write(f"Executable: {sys.executable}\n")
            handle.write(f"Working directory: {os.getcwd()}\n")
            traceback.print_exception(exc_type, exc_value, exc_traceback, file=handle)
            handle.write("\n")
        return log_path
    except Exception:
        # Logging must never crash the app.
        return None


def _show_startup_error_notice(log_path: Optional[Path]) -> None:
    """Show a visual error message and include the crash log path."""
    log_path_str = str(log_path) if log_path else "(unable to determine log path)"
    message = (
        "GeoLabeller failed to start due to an unexpected error.\n\n"
        f"A traceback was written to:\n{log_path_str}"
    )

    try:
        from PyQt5.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance()
        owns_app = False
        if app is None:
            app = QApplication([])
            owns_app = True

        QMessageBox.critical(None, "GeoLabeller Startup Error", message)

        if owns_app:
            app.quit()
        return
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "GeoLabeller Startup Error", 0x10)
            return
        except Exception:
            pass

    print(message, file=sys.stderr)


def _handle_unhandled_exception(exc_type, exc_value, exc_traceback) -> None:
    """Log unhandled exceptions and display where the traceback was written."""
    log_path = _log_startup_exception(exc_type, exc_value, exc_traceback)
    _show_startup_error_notice(log_path)


sys.excepthook = _handle_unhandled_exception


def main():
    try:
        from PyQt5.QtWidgets import QApplication
        from app.main_window import MainWindow

        app = QApplication(sys.argv)
        app.setApplicationName("GeoLabel")

        window = MainWindow()
        window.show()

        return app.exec_()
    except Exception:
        _handle_unhandled_exception(*sys.exc_info())
        return 1


if __name__ == "__main__":
    sys.exit(main())
