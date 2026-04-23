import threading
import webbrowser
import socket
import os
import signal
import sys
from pathlib import Path
from PIL import Image, ImageDraw
import pystray
from pystray import MenuItem as item

from src.hub.server import build_app
from src.hub.managers import detect_lan_ip
import uvicorn

class GestureLinkTray:
    def __init__(self):
        self.host = "0.0.0.0"
        self.port = 8000
        self.server_thread = None
        self.icon = None
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
        webbrowser.open(f"http://localhost:{self.port}/hub")

    def on_copy_ip(self):
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
        uvicorn.run(self.app, host=self.host, port=self.port, log_level="info")

    def run(self):
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
        
        print(f"GestureLink Hub running on http://{detect_lan_ip()}:{self.port}")
        print("Tray icon active. Access the dashboard via the taskbar.")
        self.icon.run()

if __name__ == "__main__":
    tray = GestureLinkTray()
    tray.run()
