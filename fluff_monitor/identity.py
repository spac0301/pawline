"""Public application identity; compatibility data paths stay independent."""
from pathlib import Path
import ctypes
import sys

APP_NAME = "Pawline"
DESKTOP_ID = "pawline"
WINDOWS_APP_ID = "spac0301.Pawline"
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
ICON = ROOT / "assets/app/pawline.png"


def set_process_name(name="pawline"):
    """Expose a distinguishable process name without changing its arguments."""
    if sys.platform == "linux":
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            return libc.prctl(15, ctypes.c_char_p(name.encode("ascii")[:15]), 0, 0, 0) == 0
        except (AttributeError, OSError):
            return False
    return True


def identify_windows_app():
    if sys.platform == "win32":
        function = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        function.argtypes = [ctypes.c_wchar_p]
        function.restype = ctypes.c_long
        function(WINDOWS_APP_ID)
