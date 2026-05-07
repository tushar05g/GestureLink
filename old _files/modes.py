"""
modes.py — AppMode enum + BuilderController.
"""
from __future__ import annotations
import logging
from enum import Enum, auto
from typing import Optional
from world import Cube, CubeWorld
from renderer import CubeRenderer

logger = logging.getLogger(__name__)


class AppMode(Enum):
    CURSOR  = auto()
    BUILDER = auto()


class BuilderController:
    def __init__(self, cfg) -> None:
        self.cfg      = cfg
        self.cc       = cfg.cube
        self.world    = CubeWorld(max_undo=cfg.cube.max_undo)
        self.renderer = CubeRenderer(cfg)

        self.current_layer: int  = 3
        self.move_mode:     str  = "group"

        self._ghost:       Optional[tuple] = None
        self._erase_ghost: Optional[tuple] = None

        self._painting:     bool           = False
        self._paint_hold:   int            = 0
        self._last_painted: Optional[tuple]= None

        self._dragging:          bool            = False
        self._drag_group:        list            = []
        self._drag_origin_grid:  Optional[tuple] = None

        self._toggle_cooldown:        int = 0
        self._TOGGLE_COOLDOWN_FRAMES: int = 20

    # ------------------------------------------------------------------
    def update(self, gesture: str, nx: float, ny: float,
               frame_w: int, frame_h: int, frame) -> str:
        cc = self.cc
        gs, ox, oy = cc.grid_size, cc.iso_offset_x, cc.iso_offset_y

        # Depth layer from wrist height
        self.current_layer = max(0, min(cc.num_layers-1, int(ny*(cc.num_layers-1))))
        gz = self.current_layer

        px = int(nx * frame_w)
        py = int(ny * frame_h)
        gx = max(0, (px - gz*ox) // gs)
        gy = max(0, (py + gz*oy) // gs)

        self._ghost = self._erase_ghost = None
        status = "BUILDER"

        if self._toggle_cooldown > 0:
            self._toggle_cooldown -= 1

        # Toggle button hit
        if gesture == "POINTING" and self._hit_toggle(px, py) and self._toggle_cooldown == 0:
            self._toggle_move_mode()
            self._toggle_cooldown = self._TOGGLE_COOLDOWN_FRAMES
            self._render(frame)
            return f"TOGGLE -> {self.move_mode.upper()}"

        if gesture == "POINTING":
            self._ghost   = (int(gx), int(gy), gz)
            self._painting = False
            self._paint_hold = 0
            self._stop_drag()
            status = "PREVIEW"

        elif gesture == "PINCH":
            self._paint_hold += 1
            self._ghost = (int(gx), int(gy), gz)
            if self._paint_hold >= cc.paint_hold_frames:
                self._painting = True
            if self._painting:
                pos = (int(gx), int(gy), gz)
                if pos != self._last_painted:
                    self.world.place(*pos)
                    self._last_painted = pos
            status = "PAINTING"

        elif gesture == "SCROLL":
            self._painting = False
            self._paint_hold = 0
            self._stop_drag()
            self._erase_ghost = (int(gx), int(gy), gz)
            cube = self.world.nearest_at_xy(int(gx), int(gy))
            if cube:
                self.world.erase(cube.gx, cube.gy, cube.gz)
            status = "ERASING"

        elif gesture == "RIGHT_CLICK":
            self._painting = False
            self._paint_hold = 0
            if self.world.undo():
                status = "UNDO"

        else:
            self._painting = False
            self._paint_hold = 0
            self._stop_drag()

        self._render(frame)
        return status

    # ------------------------------------------------------------------
    def handle_thumb_pinch_drag(self, nx, ny, frame_w, frame_h, frame,
                                drag_start_norm, is_dragging) -> str:
        cc = self.cc
        gs, ox, oy = cc.grid_size, cc.iso_offset_x, cc.iso_offset_y
        gz = self.current_layer
        px, py = int(nx*frame_w), int(ny*frame_h)
        gx  = max(0, (px - gz*ox) // gs)
        gy  = max(0, (py + gz*oy) // gs)

        if drag_start_norm and not self._dragging:
            spx = int(drag_start_norm[0]*frame_w)
            spy = int(drag_start_norm[1]*frame_h)
            sgx = max(0, (spx - gz*ox) // gs)
            sgy = max(0, (spy + gz*oy) // gs)
            sc  = self.world.nearest_at_xy(int(sgx), int(sgy))
            if sc:
                self._drag_group = (self.world.connected_group(sc)
                                    if self.move_mode == "group" else [sc])
                self.world.selected_group = self._drag_group
                self._drag_origin_grid    = (int(sgx), int(sgy), sc.gz)
                self._dragging            = True

        if self._dragging and self._drag_origin_grid:
            ogx, ogy, ogz = self._drag_origin_grid
            dgx, dgy = int(gx)-ogx, int(gy)-ogy
            if dgx or dgy:
                if self.world.move_group(self._drag_group, dgx, dgy, 0):
                    self._drag_origin_grid = (int(gx), int(gy), ogz)
                    if self._drag_group:
                        ref = self._drag_group[0]
                        new_ref = Cube(ref.gx+dgx, ref.gy+dgy, ref.gz)
                        self._drag_group = self.world.connected_group(new_ref)
                        self.world.selected_group = self._drag_group

        if not is_dragging:
            self._stop_drag()
            self._render(frame)
            return "DROP"

        self._render(frame)
        return "MOVING"

    # ------------------------------------------------------------------
    def _stop_drag(self):
        self._dragging = False
        self._drag_group = []
        self._drag_origin_grid = None
        self.world.selected_group = []

    def _hit_toggle(self, px, py) -> bool:
        cc = self.cc
        return (cc.toggle_btn_x <= px <= cc.toggle_btn_x+cc.toggle_btn_w and
                cc.toggle_btn_y <= py <= cc.toggle_btn_y+cc.toggle_btn_h)

    def _toggle_move_mode(self):
        self.move_mode = "single" if self.move_mode == "group" else "group"
        logger.info("Move mode -> %s", self.move_mode)

    def _render(self, frame):
        self.renderer.render(frame, self.world,
                             self._ghost, self._erase_ghost,
                             self.current_layer, self.move_mode)

    @property
    def ghost(self):        return self._ghost
    @property
    def erase_ghost(self):  return self._erase_ghost
