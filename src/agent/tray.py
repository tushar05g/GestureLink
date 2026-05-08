import sys
import os
import multiprocessing

# --- Single-Instance Mutex (Windows) ---
# This named mutex lets the Inno Setup installer detect that the Agent is running
# and close it gracefully before overwriting files during an update.
_agent_mutex = None
if sys.platform == "win32":
    try:
        import ctypes
        _agent_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "GestureLinkAgent")
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk(); root.withdraw()
            messagebox.showerror("GestureLink Agent", "GestureLink Agent is already running.\nCheck the system tray.")
            root.destroy()
            sys.exit(1)
    except Exception:
        pass  # Non-critical — silently skip on non-Windows or import failure

# CRITICAL: Fix for PyInstaller + Multiprocessing (prevent infinite loop / fork bomb)
if __name__ == "__main__":
    multiprocessing.freeze_support()

import threading
import socket
import pystray
from pystray import MenuItem as item
from PIL import Image, ImageDraw
import uvicorn
from src.agent.main import app

# Fix for PyInstaller with console=False
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

def _start_shutdown_listener(on_shutdown):
    """
    Spawns a hidden Win32 window to receive WM_QUERYENDSESSION.
    The Windows Restart Manager (used by Inno Setup CloseApplications=yes)
    broadcasts this message to all top-level windows when it needs apps to
    close before overwriting files. Returning 1 signals 'ready to close';
    WM_ENDSESSION then fires on_shutdown() for a clean exit.
    """
    import ctypes, ctypes.wintypes, threading

    user32   = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    WM_QUERYENDSESSION = 0x0011
    WM_ENDSESSION      = 0x0016

    WNDPROCTYPE = ctypes.WINFUNCTYPE(
        ctypes.c_long,
        ctypes.wintypes.HWND, ctypes.c_uint,
        ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM
    )

    def _wnd_proc(hwnd, msg, wparam, lparam):
        if msg == WM_QUERYENDSESSION:
            return 1          # Tell Restart Manager: "yes, we can close"
        if msg == WM_ENDSESSION and wparam:
            on_shutdown()     # Actually shut down now
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    _wnd_proc_ref = WNDPROCTYPE(_wnd_proc)  # Keep reference — prevents GC

    class WNDCLASSEX(ctypes.Structure):
        _fields_ = [
            ("cbSize",        ctypes.c_uint),
            ("style",         ctypes.c_uint),
            ("lpfnWndProc",   WNDPROCTYPE),
            ("cbClsExtra",    ctypes.c_int),
            ("cbWndExtra",    ctypes.c_int),
            ("hInstance",     ctypes.wintypes.HANDLE),
            ("hIcon",         ctypes.wintypes.HANDLE),
            ("hCursor",       ctypes.wintypes.HANDLE),
            ("hbrBackground", ctypes.wintypes.HANDLE),
            ("lpszMenuName",  ctypes.wintypes.LPCWSTR),
            ("lpszClassName", ctypes.wintypes.LPCWSTR),
            ("hIconSm",       ctypes.wintypes.HANDLE),
        ]

    def _run():
        hinstance  = kernel32.GetModuleHandleW(None)
        class_name = "GL_AgentShutdownWatcher"
        wc = WNDCLASSEX()
        wc.cbSize        = ctypes.sizeof(WNDCLASSEX)
        wc.lpfnWndProc   = _wnd_proc_ref
        wc.hInstance     = hinstance
        wc.lpszClassName = class_name
        user32.RegisterClassExW(ctypes.byref(wc))
        # Top-level hidden window (NOT message-only) so it receives the broadcast
        user32.CreateWindowExW(0, class_name, "GL_AgentShutdownWatcher", 0,
                               0, 0, 0, 0, None, None, hinstance, None)
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    threading.Thread(target=_run, daemon=True).start()


class AgentTray:
    def __init__(self, port=8001):
        self.port = port
        self.icon = None

    def create_icon_image(self):
        width = 64
        height = 64
        image = Image.new('RGBA', (width, height), color=(0, 0, 0, 0))
        dc = ImageDraw.Draw(image)
        # Agent color: Cyber Blue
        main_color = (0, 162, 255, 255) 
        
        # Micro-Agent Icon (smaller palm + dots)
        dc.rounded_rectangle([20, 35, 44, 50], radius=4, fill=main_color)
        dc.ellipse([20, 15, 28, 23], fill=main_color) # Dot 1
        dc.ellipse([36, 15, 44, 23], fill=main_color) # Dot 2
        
        # Signal Ring
        dc.ellipse([5, 5, 59, 59], outline=main_color, width=2)
        
        bg = Image.new('RGBA', (width, height), color=(3, 7, 12, 255))
        return Image.alpha_composite(bg, image)

    def on_quit(self):
        if self.icon:
            self.icon.stop()
        os._exit(0)

    def run_server(self):
        # The agent's app is imported from src.agent.main
        from src.core.utils import resource_path
        cert = str(resource_path("cert.pem"))
        key = str(resource_path("key.pem"))
        
        # Launch with SSL to allow HTTPS-based mobile control
        uvicorn.run(
            app, 
            host="0.0.0.0", 
            port=self.port, 
            log_level="info",
            ssl_keyfile=key,
            ssl_certfile=cert
        )

    def run(self):
        # --- Graceful shutdown: SIGTERM + Windows Restart Manager ---
        import signal
        signal.signal(signal.SIGTERM, lambda s, f: self.on_quit())
        signal.signal(signal.SIGINT,  lambda s, f: self.on_quit())
        if sys.platform == "win32":
            _start_shutdown_listener(self.on_quit)

        threading.Thread(target=self.run_server, daemon=True).start()

        menu = (
            item('GestureLink Agent (Online)', lambda: None, enabled=False),
            item('Exit Agent', self.on_quit),
        )
        
        self.icon = pystray.Icon(
            "GestureLinkAgent",
            self.create_icon_image(),
            "GestureLink Agent",
            menu
        )
        self.icon.run()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    
    tray = AgentTray(port=args.port)
    tray.run()
