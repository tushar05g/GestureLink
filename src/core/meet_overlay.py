"""
meet_overlay.py — Transparent, click-through, always-on-top annotation overlay.

Used by MEET PAINT mode (mode index 1) to draw ink strokes directly on the
screen — visible over Google Meet screen-shares and all other windows.

Architecture
------------
- Runs a Tkinter window in a background daemon thread.
- The window background is a chroma-key colour (#010101) which Win32 makes
  fully transparent via SetLayeredWindowAttributes / LWA_COLORKEY.
- WS_EX_TRANSPARENT makes the window click-through so Meet (and everything
  else underneath) still receives all mouse events normally.
- All draw calls from the gesture loop are marshalled onto the Tkinter event
  loop with root.after(0, fn) — fully thread-safe.

Usage
-----
    overlay = MeetOverlay()
    overlay.start()                             # opens window in bg thread

    overlay.draw_stroke(sx, sy)                 # call every frame while drawing
    overlay.lift_pen()                          # end current stroke
    overlay.draw_laser(sx, sy)                  # laser pointer (no stroke)
    overlay.erase_nearest(sx, sy)               # remove closest path
    overlay.clear_all()                         # wipe everything
    overlay.set_color("#FF4757")                # change ink colour

    overlay.stop()                              # close and cleanup
"""
from __future__ import annotations

import ctypes
import logging
import math
import threading
import tkinter as tk
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Win32 constants (ctypes — no pywin32 dependency required)
# ---------------------------------------------------------------------------
GWL_EXSTYLE       = -20
WS_EX_LAYERED     = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
LWA_COLORKEY      = 0x00000001
HWND_TOPMOST      = -1
SWP_NOMOVE        = 0x0002
SWP_NOSIZE        = 0x0001

# Chroma-key colour: background pixels with this value become 100% transparent.
# Using #010101 (near-black) instead of pure black so black ink still shows.
_CHROMA = "#010101"
_CHROMA_WIN32 = 0x010101   # BGR in Win32 → R=0x01, G=0x01, B=0x01

# ---------------------------------------------------------------------------
# MeetOverlay
# ---------------------------------------------------------------------------

class MeetOverlay:
    """
    Thread-safe transparent annotation overlay window.
    All public methods are safe to call from any thread.
    """

    def __init__(self) -> None:
        self._root: Optional[tk.Tk] = None
        self._canvas: Optional[tk.Canvas] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        # Stroke state
        self._current_stroke_ids: list[int] = []   # canvas item IDs for active stroke
        self._all_stroke_ids: list[list[int]] = []  # list of committed strokes
        self._prev_x: Optional[int] = None
        self._prev_y: Optional[int] = None

        # Laser pointer
        self._laser_id: Optional[int] = None
        self._laser_ring_id: Optional[int] = None

        # Settings
        self.color   = "#FF4757"   # default: red
        self.size    = 4           # stroke width (px)
        self._laser_r = 10         # laser dot radius

    # ------------------------------------------------------------------
    # Public API — callable from any thread
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open the overlay window in a background daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="MeetOverlay")
        self._thread.start()
        logger.info("MeetOverlay: started in background thread.")

    def stop(self) -> None:
        """Close the overlay window and stop the background thread."""
        self._running = False
        if self._root:
            try:
                self._root.after(0, self._root.destroy)
            except Exception:
                pass
        logger.info("MeetOverlay: stopped.")

    def draw_stroke(self, sx: int, sy: int, color: Optional[str] = None) -> None:
        """Add a point to the current in-progress stroke."""
        c = color or self.color
        if self._canvas and self._root:
            self._root.after(0, lambda: self._do_draw_stroke(sx, sy, c))

    def lift_pen(self) -> None:
        """Commit the current stroke and start a fresh one."""
        if self._root:
            self._root.after(0, self._do_lift_pen)

    def draw_laser(self, sx: int, sy: int) -> None:
        """Show an animated laser-pointer dot (no stroke recorded)."""
        if self._canvas and self._root:
            self._root.after(0, lambda: self._do_draw_laser(sx, sy))

    def clear_laser(self) -> None:
        """Remove the laser dot from canvas."""
        if self._canvas and self._root:
            self._root.after(0, self._do_clear_laser)

    def erase_nearest(self, sx: int, sy: int, radius: int = 60) -> None:
        """Remove the stroke whose centroid is nearest to (sx, sy)."""
        if self._root:
            self._root.after(0, lambda: self._do_erase_nearest(sx, sy, radius))

    def clear_all(self) -> None:
        """Remove every stroke and the laser dot from the canvas."""
        if self._canvas and self._root:
            self._root.after(0, self._do_clear_all)

    def set_color(self, hex_color: str) -> None:
        """Change the active ink colour (e.g. '#00FF95')."""
        self.color = hex_color

    def set_size(self, px: int) -> None:
        """Change the stroke width in pixels."""
        self.size = max(1, px)

    # ------------------------------------------------------------------
    # Background thread — Tkinter event loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._root = tk.Tk()
            sw = self._root.winfo_screenwidth()
            sh = self._root.winfo_screenheight()

            # Fullscreen, no border, always on top
            self._root.geometry(f"{sw}x{sh}+0+0")
            self._root.overrideredirect(True)          # no title bar / frame
            self._root.attributes("-topmost", True)    # always on top
            self._root.configure(bg=_CHROMA)

            # Canvas fills the whole screen with the chroma-key background
            self._canvas = tk.Canvas(
                self._root,
                width=sw, height=sh,
                bg=_CHROMA,
                highlightthickness=0,
                cursor="none",
            )
            self._canvas.pack()

            # Apply Win32 transparency + click-through
            self._root.update()          # ensure window has an HWND
            self._apply_win32_transparency()

            # Run Tkinter event loop
            self._root.mainloop()

        except Exception as e:
            logger.error("MeetOverlay thread crashed: %s", e)
        finally:
            self._running = False
            self._root = None
            self._canvas = None

    def _apply_win32_transparency(self) -> None:
        """Make window transparent (chroma-key) and click-through via Win32."""
        try:
            import sys
            if sys.platform != "win32":
                # On non-Windows: just set tk alpha (partial transparency fallback)
                self._root.attributes("-alpha", 0.85)
                return

            # Get HWND
            hwnd = ctypes.windll.user32.GetParent(self._root.winfo_id())
            if not hwnd:
                hwnd = self._root.winfo_id()

            # Force always-on-top via SetWindowPos
            ctypes.windll.user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE
            )

            # Add WS_EX_LAYERED + WS_EX_TRANSPARENT
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                style | WS_EX_LAYERED | WS_EX_TRANSPARENT
            )

            # Make chroma-key colour fully transparent
            ctypes.windll.user32.SetLayeredWindowAttributes(
                hwnd, _CHROMA_WIN32, 0, LWA_COLORKEY
            )
            logger.info("MeetOverlay: Win32 transparency applied (click-through active).")
        except Exception as e:
            logger.warning("MeetOverlay: Win32 transparency failed: %s", e)

    # ------------------------------------------------------------------
    # Internal draw helpers — called on the Tkinter event thread via after()
    # ------------------------------------------------------------------

    def _do_draw_stroke(self, sx: int, sy: int, color: str) -> None:
        if not self._canvas:
            return
            
        if not hasattr(self, '_current_stroke_coords'):
            self._current_stroke_coords = []
            
        if not self._current_stroke_ids:
            self._current_stroke_coords = [sx, sy]
            self._current_stroke_ids.append(-1)
            return

        self._current_stroke_coords.extend([sx, sy])

        if self._current_stroke_ids[0] == -1:
            line_id = self._canvas.create_line(
                *self._current_stroke_coords,
                fill=color,
                width=self.size,
                capstyle=tk.ROUND,
                joinstyle=tk.ROUND,
                smooth=True,
            )
            self._current_stroke_ids[0] = line_id
        else:
            self._canvas.coords(self._current_stroke_ids[0], *self._current_stroke_coords)

    def _do_lift_pen(self) -> None:
        if self._current_stroke_ids and self._current_stroke_ids[0] != -1:
            self._all_stroke_ids.append(list(self._current_stroke_ids))
        self._current_stroke_ids = []
        if hasattr(self, '_current_stroke_coords'):
            self._current_stroke_coords = []
        self._prev_x = None
        self._prev_y = None

    def _do_draw_laser(self, sx: int, sy: int) -> None:
        if not self._canvas:
            return
        # Commit any in-progress stroke first
        self._do_lift_pen()

        # Delete previous laser items
        self._do_clear_laser()

        r  = self._laser_r
        r2 = r + 6   # outer ring

        # Inner filled dot
        self._laser_id = self._canvas.create_oval(
            sx - r, sy - r, sx + r, sy + r,
            fill="#FFFFFF", outline=self.color, width=2
        )
        # Outer pulsing ring
        self._laser_ring_id = self._canvas.create_oval(
            sx - r2, sy - r2, sx + r2, sy + r2,
            fill="", outline=self.color, width=2
        )

    def _do_clear_laser(self) -> None:
        if not self._canvas:
            return
        for item_id in (self._laser_id, self._laser_ring_id):
            if item_id is not None:
                try:
                    self._canvas.delete(item_id)
                except Exception:
                    pass
        self._laser_id = None
        self._laser_ring_id = None

    def _do_erase_nearest(self, sx: int, sy: int, radius: int) -> None:
        """Find and delete the stroke whose midpoint is nearest to (sx, sy)."""
        if not self._canvas or not self._all_stroke_ids:
            return

        best_idx    = -1
        best_dist   = float("inf")

        for i, stroke in enumerate(self._all_stroke_ids):
            if not stroke:
                continue
            # Use the midpoint item's bounding box to approximate centroid
            mid_item = stroke[len(stroke) // 2]
            try:
                x1, y1, x2, y2 = self._canvas.bbox(mid_item)
                cx = (x1 + x2) / 2
                cy = (y1 + y2) / 2
            except Exception:
                continue
            dist = math.hypot(cx - sx, cy - sy)
            if dist < best_dist:
                best_dist = dist
                best_idx  = i

        if best_idx >= 0 and best_dist <= radius:
            for item_id in self._all_stroke_ids[best_idx]:
                try:
                    self._canvas.delete(item_id)
                except Exception:
                    pass
            self._all_stroke_ids.pop(best_idx)
            logger.debug("MeetOverlay: erased stroke %d (dist=%.1f)", best_idx, best_dist)

    def _do_clear_all(self) -> None:
        if not self._canvas:
            return
        self._canvas.delete("all")
        self._all_stroke_ids    = []
        self._current_stroke_ids = []
        self._laser_id          = None
        self._laser_ring_id     = None
        self._prev_x            = None
        self._prev_y            = None
        logger.info("MeetOverlay: canvas cleared.")
