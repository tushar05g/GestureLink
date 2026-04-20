"""
modes.py — Mode state machine.

PRODUCTIVITY : cursor/click/scroll via controller.py
BUILDER      : 3D cube builder via OpenGL
"""
from __future__ import annotations

import logging
from enum import Enum, auto
from typing import Optional

import numpy as np

from world import Cube, CubeWorld

logger = logging.getLogger(__name__)


class AppMode(Enum):
    PRODUCTIVITY = auto()
    BUILDER      = auto()


class BuilderController:
    """
    All Builder Mode logic:
      - Ghost cube / paint / erase / undo
      - Pinch → move single cube or group
      - Two-hand rotate + zoom via GL camera
      - Toggle button (single/group move)
    """

    def __init__(self, cfg) -> None:
        self.cfg   = cfg
        self.cc    = cfg.cube
        self.world = CubeWorld(max_undo=cfg.cube.max_undo)

        self.current_layer: int = 3
        self.move_mode: str = "group"

        self._ghost:        Optional[tuple] = None
        self._erase_ghost:  Optional[tuple] = None

        # Paint state
        self._painting:      bool  = False
        self._paint_hold:    int   = 0
        self._last_painted:  Optional[tuple] = None

        # Pinch drag (move)
        self._dragging:           bool  = False
        self._drag_group:         list  = []
        self._drag_origin_grid:   Optional[tuple] = None

        # Toggle debounce
        self._toggle_cooldown:         int = 0
        self._TOGGLE_COOLDOWN_FRAMES:  int = 20

        # Two-hand rotate/zoom state
        self._prev_right_index: Optional[tuple] = None
        self._prev_thumb_index_dist: Optional[float] = None

        # GL camera reference — set by app.py after GL window opens
        self.camera = None

    # ------------------------------------------------------------------
    # Called every frame from app.py
    # ------------------------------------------------------------------

    def update(
        self,
        gesture:  str,
        nx:       float,
        ny:       float,
        frame_w:  int,
        frame_h:  int,
        gesture_state,     # full GestureState for two-hand data
    ) -> str:
        """Process one frame. Returns status string."""

        gs = self.cc.grid_size
        ox = self.cc.iso_offset_x
        oy = self.cc.iso_offset_y

        # Depth layer from wrist height
        layer_f = ny * (self.cc.num_layers - 1)
        self.current_layer = max(0, min(self.cc.num_layers - 1, int(layer_f)))
        gz = self.current_layer

        # Screen px → grid coords
        px = int(nx * frame_w)
        py = int(ny * frame_h)
        gx = max(0, (px - gz * ox) // gs)
        gy = max(0, (py + gz * oy) // gs)

        self._ghost       = None
        self._erase_ghost = None
        status = "BUILDER"

        # ---- Two-hand: left fist active → rotate / zoom -----------------
        if gesture_state.left_fist and self.camera is not None:
            status = self._handle_two_hand(gesture_state)
            # Reset single-hand state
            self._prev_right_index = None
            return status
        else:
            self._prev_right_index      = None
            self._prev_thumb_index_dist = None

        # ---- Toggle cooldown tick ----------------------------------------
        if self._toggle_cooldown > 0:
            self._toggle_cooldown -= 1

        # ---- Toggle button check -----------------------------------------
        if gesture == "POINTING":
            if self._hit_toggle(px, py) and self._toggle_cooldown == 0:
                self._toggle_move_mode()
                self._toggle_cooldown = self._TOGGLE_COOLDOWN_FRAMES
                return f"TOGGLE -> {self.move_mode.upper()}"

        # ---- Single-hand gestures ----------------------------------------
        if gesture == "POINTING":
            self._ghost = (int(gx), int(gy), gz)
            self._painting    = False
            self._paint_hold  = 0
            self._stop_drag()
            status = "PREVIEW"

        elif gesture == "PINCH":
            self._paint_hold += 1
            self._ghost = (int(gx), int(gy), gz)
            if self._paint_hold >= self.cc.paint_hold_frames:
                self._painting = True
            if self._painting:
                pos = (int(gx), int(gy), gz)
                if pos != self._last_painted:
                    self.world.place(*pos)
                    self._last_painted = pos
            status = "PAINTING"

        elif gesture == "SCROLL":
            self._painting    = False
            self._paint_hold  = 0
            self._stop_drag()
            eg = (int(gx), int(gy), gz)
            self._erase_ghost = eg
            cube = self.world.nearest_at_xy(int(gx), int(gy))
            if cube:
                self.world.erase(cube.gx, cube.gy, cube.gz)
            status = "ERASING"

        elif gesture == "RIGHT_CLICK":
            self._painting   = False
            self._paint_hold = 0
            if self.world.undo():
                status = "UNDO"

        else:
            self._painting   = False
            self._paint_hold = 0
            self._stop_drag()

        return status

    # ------------------------------------------------------------------
    def handle_thumb_pinch_drag(
        self,
        nx: float, ny: float,
        frame_w: int, frame_h: int,
        drag_start_norm: Optional[tuple],
        is_dragging: bool,
    ) -> str:
        """Handle thumb+index pinch drag for moving cubes."""
        gs = self.cc.grid_size
        ox = self.cc.iso_offset_x
        oy = self.cc.iso_offset_y
        gz = self.current_layer

        px  = int(nx * frame_w)
        py  = int(ny * frame_h)
        gx  = max(0, (px - gz * ox) // gs)
        gy  = max(0, (py + gz * oy) // gs)

        if drag_start_norm and not self._dragging:
            spx = int(drag_start_norm[0] * frame_w)
            spy = int(drag_start_norm[1] * frame_h)
            sgx = max(0, (spx - gz * ox) // gs)
            sgy = max(0, (spy + gz * oy) // gs)
            start_cube = self.world.nearest_at_xy(int(sgx), int(sgy))
            if start_cube:
                if self.move_mode == "group":
                    self._drag_group = self.world.connected_group(start_cube)
                else:
                    self._drag_group = [start_cube]
                self.world.selected_group = self._drag_group
                self._drag_origin_grid = (int(sgx), int(sgy), start_cube.gz)
                self._dragging = True

        if self._dragging and self._drag_origin_grid:
            ogx, ogy, ogz = self._drag_origin_grid
            dgx = int(gx) - ogx
            dgy = int(gy) - ogy
            if dgx != 0 or dgy != 0:
                moved = self.world.move_group(self._drag_group, dgx, dgy, 0)
                if moved:
                    self._drag_origin_grid = (int(gx), int(gy), ogz)
                    if self._drag_group:
                        ref     = self._drag_group[0]
                        new_ref = Cube(ref.gx + dgx, ref.gy + dgy, ref.gz)
                        self._drag_group = self.world.connected_group(new_ref)
                        self.world.selected_group = self._drag_group

        if not is_dragging:
            self._stop_drag()
            return "DROP"

        return "MOVING"

    # ------------------------------------------------------------------
    # Two-hand rotate / zoom
    # ------------------------------------------------------------------

    def _handle_two_hand(self, gs) -> str:
        """
        Left fist is active.
        Right hand controls:
          - Index fingertip sweep → rotate camera
          - Thumb+index dist change → zoom
        """
        cfg = self.cfg.opengl
        ri  = gs.right_index_pos          # (nx, ny) normalised
        tid = gs.right_thumb_index_dist   # normalised dist

        status = "LOCKED"

        # --- Rotate via index sweep ---
        if self._prev_right_index is not None:
            dx = ri[0] - self._prev_right_index[0]
            dy = ri[1] - self._prev_right_index[1]
            if abs(dx) > 0.002 or abs(dy) > 0.002:
                dyaw   =  dx * cfg.rotate_sensitivity * 180.0
                dpitch =  dy * cfg.rotate_sensitivity * 180.0
                if self.camera:
                    self.camera.rotate(dyaw, dpitch)
                status = "ROTATING"

        # --- Zoom via thumb+index dist ---
        if self._prev_thumb_index_dist is not None:
            dd = tid - self._prev_thumb_index_dist
            if abs(dd) > 0.005:
                # closing = zoom out (positive delta = fingers spreading = zoom in)
                zoom_delta = -dd * cfg.zoom_sensitivity
                if self.camera:
                    self.camera.zoom(zoom_delta)
                status = "ZOOMING"

        self._prev_right_index      = ri
        self._prev_thumb_index_dist = tid
        return status

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _stop_drag(self) -> None:
        self._dragging         = False
        self._drag_group       = []
        self._drag_origin_grid = None
        self.world.selected_group = []

    def _hit_toggle(self, px: int, py: int) -> bool:
        cc = self.cc
        return (cc.toggle_btn_x <= px <= cc.toggle_btn_x + cc.toggle_btn_w and
                cc.toggle_btn_y <= py <= cc.toggle_btn_y + cc.toggle_btn_h)

    def _toggle_move_mode(self) -> None:
        self.move_mode = "single" if self.move_mode == "group" else "group"
        logger.info("Move mode -> %s", self.move_mode)

    @property
    def ghost(self):       return self._ghost
    @property
    def erase_ghost(self): return self._erase_ghost