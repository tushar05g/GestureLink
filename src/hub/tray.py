import multiprocessing
import sys
import os

_hub_mutex = None

if __name__ == "__main__":
    multiprocessing.freeze_support()
    
    if sys.platform == "win32":
        try:
            from src.core.utils import kill_process_on_port, kill_processes_by_name, get_lock
            
            # Use the robust cleanup logic
            kill_process_on_port(8000)
            kill_processes_by_name(["cloudflared", "GestureLink_Hub"])
            
            # Use HTTPS if certificates are found, otherwise fallback to HTTP
            from src.core.utils import resource_path
            local_proto = "https" if resource_path("cert.pem").exists() else "http"
            
            # Double-check lock acquisition
            err = get_lock()
            if err == 183:
                print("!!! Warning: Another instance is still holding the lock. Please check Task Manager.")
                sys.exit(1)
            else:
                if _hub_mutex: print("DEBUG: Hub lock acquired.")
        except Exception:
            import traceback
            traceback.print_exc()

# Fix for PyInstaller with console=False
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import threading
import webbrowser
import socket
import signal
from pathlib import Path
from PIL import Image, ImageDraw
import pystray
from pystray import MenuItem as item

# Heavy imports deferred to avoid module double-load errors in subprocesses
# from src.hub.server import build_app
# from src.hub.managers import detect_lan_ip
import uvicorn

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
    
    # Define argument and return types correctly for 64-bit Windows
    # LPARAM and WPARAM are pointer-sized (64-bit on x64)
    user32.DefWindowProcW.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
    user32.DefWindowProcW.restype = ctypes.wintypes.LPARAM # LRESULT is pointer-sized

    WNDPROCTYPE = ctypes.WINFUNCTYPE(
        ctypes.wintypes.LPARAM,
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
        class_name = "GL_HubShutdownWatcher"
        wc = WNDCLASSEX()
        wc.cbSize        = ctypes.sizeof(WNDCLASSEX)
        wc.lpfnWndProc   = _wnd_proc_ref
        wc.hInstance     = hinstance
        wc.lpszClassName = class_name
        user32.RegisterClassExW(ctypes.byref(wc))
        # Top-level hidden window (NOT message-only) so it receives the broadcast
        user32.CreateWindowExW(0, class_name, "GL_HubShutdownWatcher", 0,
                               0, 0, 0, 0, None, None, hinstance, None)
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    threading.Thread(target=_run, daemon=True).start()


class GestureLinkTray:
    def __init__(self):
        self.host = "0.0.0.0"
        self.port = 8000
        self.server_thread = None
        self.icon = None
        from src.hub.server import build_app
        self.app = build_app(host=self.host, port=self.port)

    def create_icon_image(self):
        # Premium V3 'Digital Hand' Icon logic
        width = 64
        height = 64
        image = Image.new('RGBA', (width, height), color=(0, 0, 0, 0))
        dc = ImageDraw.Draw(image)
        main_color = (0, 255, 149, 255) # Neon Mint
        
        # Palm
        dc.rounded_rectangle([15, 40, 49, 55], radius=5, fill=main_color)
        # Fingers
        dc.rounded_rectangle([15, 20, 21, 37], radius=2, fill=main_color) # Index
        dc.rounded_rectangle([24, 12, 30, 37], radius=2, fill=main_color) # Middle
        dc.rounded_rectangle([33, 15, 39, 37], radius=2, fill=main_color) # Ring
        dc.rounded_rectangle([42, 25, 48, 37], radius=2, fill=main_color) # Pinky
        # Thumb
        dc.rounded_rectangle([5, 37, 12, 45], radius=2, fill=main_color)
        
        # Signal Arcs
        dc.arc([2, 2, 61, 61], start=210, end=330, fill=main_color, width=2)
        dc.arc([10, 10, 54, 54], start=210, end=330, fill=main_color, width=1)
        
        # Background Circle for contrast in tray
        bg = Image.new('RGBA', (width, height), color=(3, 7, 12, 255))
        final = Image.alpha_composite(bg, image)
        return final

    def on_open_hub(self):
        from src.core.utils import resource_path
        proto = "https" if resource_path("cert.pem").exists() else "http"
        webbrowser.open(f"{proto}://localhost:{self.port}/hub")

    def on_copy_ip(self):
        from src.hub.managers import detect_lan_ip
        ip = detect_lan_ip()
        try:
            import pyperclip
            pyperclip.copy(ip)
            print(f"Copied to clipboard: {ip}")
        except ImportError:
            print(f"Hub IP: {ip}")

    def on_quit(self):
        print("Shutting down GestureLink...")
        if self.icon:
            self.icon.stop()

    def run_server(self):
        # We now default to HTTP for the local server to avoid 'Self-Signed' SSL trust issues on mobile hotspots.
        # Cloudflare Tunnel will still provide a valid HTTPS URL for the remote web app.
        from src.core.utils import resource_path
        cert = resource_path("cert.pem")
        key = resource_path("key.pem")
        if cert.exists() and key.exists():
            ssl_params = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
            print("[*] Local SSL active.")
        else:
            ssl_params = {}
            print("[*] Local SSL disabled (HTTP mode).")
        
        uvicorn.run(
            "src.hub.server:build_app",
            factory=True,
            host="0.0.0.0",
            port=self.port,
            **ssl_params
        )

    def run(self):
        # --- Graceful shutdown: SIGTERM + Windows Restart Manager ---
        signal.signal(signal.SIGTERM, lambda s, f: self.on_quit())
        signal.signal(signal.SIGINT,  lambda s, f: self.on_quit())
        if sys.platform == "win32":
            _start_shutdown_listener(self.on_quit)

        # Start FastAPI in a background thread
        self.server_thread = threading.Thread(target=self.run_server, daemon=True)
        self.server_thread.start()

        # Create System Tray Icon
        menu = (
            item('Open Hub UI', self.on_open_hub),
            item('Copy Hub IP', self.on_copy_ip),
            item('Exit', self.on_quit),
        )
        
        self.icon = pystray.Icon(
            "GestureLink",
            self.create_icon_image(),
            "GestureLink Hub",
            menu
        )
        
        from src.hub.managers import detect_lan_ip
        print(f"GestureLink Hub running on http://{detect_lan_ip()}:{self.port}")
        print("Tray icon active. Access the dashboard via the taskbar.")
        self.icon.run()

if __name__ == "__main__":
    tray = GestureLinkTray()
    tray.run()
