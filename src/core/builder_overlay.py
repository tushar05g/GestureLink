"""
builder_overlay.py — Fullscreen isometric 3D cube builder overlay.

Architecture
------------
- Mirrors MeetOverlay's pattern: runs a Tkinter window in a background thread.
- The window is fullscreen, always-on-top, opaque (dark background).
- Unlike MeetOverlay it is NOT click-through — it receives no user input from
  the mouse; all interaction is driven by gesture data pushed via update().
- All draw calls are marshalled onto the Tkinter event loop with root.after(0, fn).

Usage
-----
    overlay = BuilderOverlay(cfg)
    overlay.start()

    # Every frame from the gesture loop:
    overlay.update(world, ghost, erase_ghost, status, pinky_progress)

    overlay.stop()
"""
from __future__ import annotations

import logging
import threading
import tkinter as tk
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Win32 constants (same as MeetOverlay, for always-on-top)
# ---------------------------------------------------------------------------
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001


# ---------------------------------------------------------------------------
# Isometric geometry helpers
# ---------------------------------------------------------------------------

def _iso_cube_polys(px: int, py: int, gs: int):
    """Return (top, left, right) polygon point lists for an isometric cube
    whose top-left corner is at pixel (px, py) with grid size gs."""
    h = gs // 2
    top = [(px+gs//2, py),        (px+gs, py+h),
           (px+gs//2, py+gs//2+h//2), (px, py+h)]
    left = [(px,       py+h),      (px+gs//2, py+gs//2+h//2),
            (px+gs//2, py+gs),     (px, py+gs-h//2)]
    right = [(px+gs//2, py+gs//2+h//2), (px+gs, py+h),
             (px+gs, py+gs-h//2),   (px+gs//2, py+gs)]
    return top, left, right


def _shade(color_hex: str, factor: float) -> str:
    """Lighten or darken a hex color by factor (>1 = lighter, <1 = darker)."""
    color_hex = color_hex.lstrip("#")
    r, g, b = int(color_hex[0:2], 16), int(color_hex[2:4], 16), int(color_hex[4:6], 16)
    r = min(255, max(0, int(r * factor)))
    g = min(255, max(0, int(g * factor)))
    b = min(255, max(0, int(b * factor)))
    return f"#{r:02x}{g:02x}{b:02x}"


# ---------------------------------------------------------------------------
# BuilderOverlay
# ---------------------------------------------------------------------------

class BuilderOverlay:
    """
    Thread-safe fullscreen 3D Builder overlay.
    All public methods are safe to call from any thread.
    """

    # Isometric layer colours (front → back = brighter → darker)
    LAYER_COLORS = [
        "#4ecdc4", "#45b7aa", "#3ca28f", "#338e75",
        "#2a7a5a", "#216640", "#185226",
    ]

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        cc = cfg.cube
        self.grid_size = cc.grid_size
        self.iso_x = cc.iso_offset_x
        self.iso_y = cc.iso_offset_y
        self.num_layers = cc.num_layers

        self._root:   Optional[tk.Tk] = None
        self._canvas: Optional[tk.Canvas] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        # Pending render payload
        self._pending: Optional[dict] = None
        self._pending_lock = threading.Lock()

        # Canvas item IDs for current render (cleared each frame)
        self._item_ids: list[int] = []

        # Laser / cursor dot
        self._cursor_id:      Optional[int] = None
        self._cursor_ring_id: Optional[int] = None

        # HUD items
        self._hud_ids: list[int] = []

    # ------------------------------------------------------------------
    # Public API — callable from any thread
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Open the builder overlay window in a background daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="BuilderOverlay"
        )
        self._thread.start()
        logger.info("BuilderOverlay: started in background thread.")

    def stop(self) -> None:
        """Close the overlay window and stop the background thread."""
        self._running = False
        if self._root:
            try:
                self._root.after(0, self._root.destroy)
            except Exception:
                pass
        logger.info("BuilderOverlay: stopped.")

    def update(
        self,
        world,
        ghost:          Optional[tuple],
        erase_ghost:    Optional[tuple],
        status:         str,
        pinky_progress: float = 0.0,
        cursor_norm:    Optional[tuple] = None,
    ) -> None:
        """Push a new frame payload. Safe to call from any thread."""
        payload = {
            "world":          world,
            "ghost":          ghost,
            "erase_ghost":    erase_ghost,
            "status":         status,
            "pinky_progress": pinky_progress,
            "cursor_norm":    cursor_norm,
        }
        with self._pending_lock:
            self._pending = payload
        if self._root:
            self._root.after(0, self._draw_frame)

    # ------------------------------------------------------------------
    # Background thread — Tkinter event loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._root = tk.Tk()
            sw = self._root.winfo_screenwidth()
            sh = self._root.winfo_screenheight()
            self._sw = sw
            self._sh = sh

            # Fullscreen, no border, always on top, dark background
            self._root.geometry(f"{sw}x{sh}+0+0")
            self._root.overrideredirect(True)
            self._root.attributes("-topmost", True)
            self._root.configure(bg="#0a0f1a")

            self._canvas = tk.Canvas(
                self._root,
                width=sw, height=sh,
                bg="#0a0f1a",
                highlightthickness=0,
            )
            self._canvas.pack()

            self._root.update()
            self._apply_win32_topmost()

            self._root.mainloop()

        except Exception as e:
            logger.error("BuilderOverlay thread crashed: %s", e)
        finally:
            self._running = False
            self._root = None
            self._canvas = None

    def _apply_win32_topmost(self) -> None:
        """Force always-on-top via Win32 SetWindowPos."""
        try:
            import sys
            import ctypes
            if sys.platform != "win32":
                return
            hwnd = ctypes.windll.user32.GetParent(self._root.winfo_id())
            if not hwnd:
                hwnd = self._root.winfo_id()
            ctypes.windll.user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE
            )
            logger.info("BuilderOverlay: Win32 always-on-top applied.")
        except Exception as e:
            logger.warning("BuilderOverlay: Win32 topmost failed: %s", e)

    # ------------------------------------------------------------------
    # Internal drawing — runs on the Tkinter event thread via after()
    # ------------------------------------------------------------------

    def _draw_frame(self) -> None:
        if not self._canvas:
            return

        with self._pending_lock:
            payload = self._pending
        if payload is None:
            return

        canvas = self._canvas
        sw, sh = self._sw, self._sh
        gs = self.grid_size
        ox = self.iso_x
        oy = self.iso_y
        world = payload["world"]
        ghost = payload["ghost"]
        erase_ghost = payload["erase_ghost"]
        status = payload["status"]
        pinky_progress = payload["pinky_progress"]
        cursor_norm = payload["cursor_norm"]

        # --- Clear previous frame ---
        canvas.delete("all")

        # --- Background gradient hint (subtle) ---
        canvas.create_rectangle(0, 0, sw, sh, fill="#0a0f1a", outline="")

        # --- Grid lines (faint) ---
        cols = sw // gs + 2
        rows = sh // gs + 2
        for c in range(cols):
            x = c * gs
            canvas.create_line(x, 0, x, sh, fill="#1a2535", width=1)
        for r in range(rows):
            y = r * gs
            canvas.create_line(0, y, sw, y, fill="#1a2535", width=1)

        # --- Draw cubes (back → front layers) ---
        if world:
            selected_set = set(world.selected_group) if hasattr(world, "selected_group") else set()
            cubes = list(world.cubes)

            for gz in range(self.num_layers - 1, -1, -1):
                color = self.LAYER_COLORS[min(gz, len(self.LAYER_COLORS) - 1)]
                layer_cubes = sorted(
                    [c for c in cubes if c.gz == gz],
                    key=lambda c: (-c.gy, -c.gx),
                )
                for cube in layer_cubes:
                    px = int(cube.gx * gs + cube.gz * ox)
                    py = int(cube.gy * gs - cube.gz * oy)
                    is_sel = cube in selected_set
                    self._draw_iso_cube(canvas, px, py, gs, color, selected=is_sel)

        # --- Ghost preview cube ---
        if ghost:
            gx, gy, gz = ghost
            px = int(gx * gs + gz * ox)
            py = int(gy * gs - gz * oy)
            color = self.LAYER_COLORS[min(gz, len(self.LAYER_COLORS) - 1)]
            self._draw_iso_cube(canvas, px, py, gs, color, alpha=0.4, ghost=True)

        # --- Erase ghost ---
        if erase_ghost:
            gx, gy, gz = erase_ghost
            px = int(gx * gs + gz * ox)
            py = int(gy * gs - gz * oy)
            self._draw_iso_cube(canvas, px, py, gs, "#ff4757", alpha=0.5, ghost=True)

        # --- Cursor dot (from normalized hand position) ---
        if cursor_norm:
            cx = int(cursor_norm[0] * sw)
            cy = int(cursor_norm[1] * sh)
            r = 10
            canvas.create_oval(cx-r, cy-r, cx+r, cy+r,
                               fill="#00ff95", outline="#ffffff", width=2)
            canvas.create_oval(cx-(r+6), cy-(r+6), cx+(r+6), cy+(r+6),
                               fill="", outline="#00ff95", width=2)

        # --- HUD overlay ---
        self._draw_hud(canvas, sw, sh, status, pinky_progress)

    def _draw_iso_cube(
        self,
        canvas: tk.Canvas,
        px: int, py: int, gs: int,
        color: str,
        alpha: float = 1.0,
        selected: bool = False,
        ghost: bool = False,
    ) -> None:
        """Draw one isometric cube at pixel position (px, py)."""
        top, left, right = _iso_cube_polys(px, py, gs)

        top_color = _shade(color, 1.4)
        left_color = color
        right_color = _shade(color, 0.6)

        outline_color = "#00ffcc" if selected else _shade(color, 0.35)
        outline_w = 2 if selected else 1

        if ghost:
            # For ghost cubes draw as semi-transparent via stipple
            stipple = "gray50"
            canvas.create_polygon(top,   fill=top_color,   outline=outline_color,
                                  width=outline_w, stipple=stipple)
            canvas.create_polygon(left,  fill=left_color,  outline=outline_color,
                                  width=outline_w, stipple=stipple)
            canvas.create_polygon(right, fill=right_color, outline=outline_color,
                                  width=outline_w, stipple=stipple)
        else:
            canvas.create_polygon(top,   fill=top_color,   outline=outline_color, width=outline_w)
            canvas.create_polygon(left,  fill=left_color,  outline=outline_color, width=outline_w)
            canvas.create_polygon(right, fill=right_color, outline=outline_color, width=outline_w)

    def _draw_hud(
        self,
        canvas: tk.Canvas,
        sw: int, sh: int,
        status: str,
        pinky_progress: float,
    ) -> None:
        """Draw the status HUD at the top-left corner."""
        # Semi-transparent background panel
        canvas.create_rectangle(10, 10, 360, 68, fill="#000000", stipple="gray50", outline="")
        canvas.create_rectangle(10, 10, 360, 68, fill="", outline="#00ff95", width=1)

        # Status indicator dot
        dot_color = "#00ff00" if status not in ("BUILDER", "IDLE") else "#004400"
        canvas.create_oval(22, 30, 32, 40, fill=dot_color, outline="")

        # Mode & status text
        canvas.create_text(42, 25, text="[BUILDER MODE]",
                           font=("Courier New", 10, "bold"),
                           fill="#00ff95", anchor="w")
        canvas.create_text(42, 50, text=status,
                           font=("Courier New", 14, "bold"),
                           fill="#ffffff", anchor="w")

        # Gesture legend (bottom-left)
        legend = [
            "☝  Point     → Preview cube",
            "✌  V-sign    → Paint cube",
            "🤙  Scroll    → Erase",
            "🤘  Rock sign → Undo",
            "🤙  Pinky hold → Exit Builder",
        ]
        for i, line in enumerate(legend):
            canvas.create_text(16, sh - 20 - (len(legend) - 1 - i) * 22,
                               text=line,
                               font=("Courier New", 10),
                               fill="#6b7a8d", anchor="w")

        # Pinky-hold progress bar
        if pinky_progress > 0.01:
            bar_w = int((sw - 20) * pinky_progress)
            canvas.create_rectangle(10, sh - 8, sw - 10, sh - 4,
                                    fill="#1a2535", outline="")
            canvas.create_rectangle(10, sh - 8, 10 + bar_w, sh - 4,
                                    fill="#00c8ff", outline="")
            canvas.create_text(sw // 2, sh - 16,
                               text="Hold pinky to exit Builder…",
                               font=("Courier New", 10),
                               fill="#00c8ff", anchor="center")
