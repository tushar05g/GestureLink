import math
import os
import sys
import time
import psutil
import socket
import ctypes
from pathlib import Path

_hub_mutex = None

def get_lock(name="GestureLinkHub"):
    """Acquires a global named mutex to ensure only one instance of an app runs."""
    global _hub_mutex
    if sys.platform == "win32":
        _hub_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, name)
        return ctypes.windll.kernel32.GetLastError()
    return 0


def resource_path(relative_path: str) -> Path:
    """
    Get the absolute path to a resource.
    Works for both normal Python execution and PyInstaller frozen executables.

    PyInstaller extracts bundled files to a temporary folder at runtime
    stored in sys._MEIPASS. This function resolves relative paths against
    that folder when frozen, or against the project root otherwise.

    Usage:
        html_file = resource_path("src/web/hub/hub.html")
        model     = resource_path("src/core/models/hand_landmarker.task")
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # Running inside a PyInstaller bundle
        base = Path(sys._MEIPASS)
    else:
        # Running normally — resolve from project root (two levels above utils.py)
        base = Path(__file__).resolve().parent.parent.parent
    
    res = base / relative_path
    return res

def kill_process_on_port(port: int):
    """Kills any process currently using the specified TCP port."""
    try:
        import psutil
        for conn in psutil.net_connections():
            if conn.laddr.port == port and conn.status == 'LISTEN':
                if conn.pid is None or conn.pid < 10: continue
                try:
                    p = psutil.Process(conn.pid)
                    print(f"[*] Port {port} is occupied by {p.name()} (PID: {conn.pid}). Cleaning up...")
                    p.terminate()
                    try:
                        p.wait(timeout=2)
                    except psutil.TimeoutExpired:
                        p.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
    except ImportError:
        print("[!] psutil not found, skipping port cleanup.")
    except Exception as e:
        print(f"[!] Error cleaning up port {port}: {e}")

def kill_processes_by_name(name_list: list[str]):
    """Kills all processes whose names contain any of the strings in name_list."""
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                # Never kill system processes
                if proc.info['pid'] < 10: continue
                
                for target in name_list:
                    if target.lower() in proc.info['name'].lower():
                        # Don't kill ourselves
                        if proc.info['pid'] == os.getpid(): continue
                        print(f"[*] Found conflicting process: {proc.info['name']} (PID: {proc.info['pid']}). Cleaning up...")
                        proc.terminate()
                        try:
                            proc.wait(timeout=2)
                        except psutil.TimeoutExpired:
                            proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except ImportError:
        print("[!] psutil not found, skipping process cleanup.")
    except Exception as e:
        print(f"[!] Error cleaning up processes: {e}")


class OneEuroFilter:
    """
    The One Euro Filter is a first-order low-pass filter with an adaptive cutoff frequency.
    It is specifically designed for low-latency signal filtering like cursor movement.
    """
    def __init__(self, freq, mincutoff=1.0, beta=0.007, dcutoff=1.0):
        self.freq = freq
        self.mincutoff = mincutoff
        self.beta = beta
        self.dcutoff = dcutoff
        self.x_prev = None
        self.dx_prev = None

    def __call__(self, x, timestamp=None):
        if self.x_prev is None:
            self.x_prev = x
            self.dx_prev = 0.0
            return x

        # Calculate velocity
        dx = (x - self.x_prev) * self.freq
        
        # Filter velocity
        edx = self._low_pass_filter(dx, self.dx_prev, self._alpha(self.freq, self.dcutoff))
        self.dx_prev = edx

        # Filter signal with adaptive cutoff based on velocity
        cutoff = self.mincutoff + self.beta * abs(edx)
        alpha = self._alpha(self.freq, cutoff)
        
        filtered_x = self._low_pass_filter(x, self.x_prev, alpha)
        self.x_prev = filtered_x
        
        return filtered_x

    def _alpha(self, freq, cutoff):
        te = 1.0 / freq
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / te)

    def _low_pass_filter(self, x, prev_x, alpha):
        return alpha * x + (1.0 - alpha) * prev_x
