"""
renderer.py — 2D isometric cube renderer on OpenCV frame.
"""
from __future__ import annotations
import logging
from typing import Optional
import cv2
import numpy as np
from world import Cube, CubeWorld

logger = logging.getLogger(__name__)


def _cube_origin(gx, gy, gz, gs, ox, oy):
    return int(gx*gs + gz*ox), int(gy*gs - gz*oy)


def _draw_iso_cube(frame, px, py, gs, color_bgr, alpha=1.0, outline=False,
                   outline_color=(0,255,255)):
    h = gs // 2
    b, g, r = color_bgr

    def shade(f):
        return (int(min(255,b*f)), int(min(255,g*f)), int(min(255,r*f)))

    top   = np.array([[px+gs//2,py],[px+gs,py+h],[px+gs//2,py+gs//2+h//2],[px,py+h]], np.int32)
    left  = np.array([[px,py+h],[px+gs//2,py+gs//2+h//2],[px+gs//2,py+gs],[px,py+gs-h//2]], np.int32)
    right = np.array([[px+gs//2,py+gs//2+h//2],[px+gs,py+h],[px+gs,py+gs-h//2],[px+gs//2,py+gs]], np.int32)

    if alpha < 1.0:
        ov = frame.copy()
        cv2.fillPoly(ov, [top],   shade(1.4))
        cv2.fillPoly(ov, [left],  shade(1.0))
        cv2.fillPoly(ov, [right], shade(0.6))
        cv2.addWeighted(ov, alpha, frame, 1-alpha, 0, frame)
    else:
        cv2.fillPoly(frame, [top],   shade(1.4))
        cv2.fillPoly(frame, [left],  shade(1.0))
        cv2.fillPoly(frame, [right], shade(0.6))

    edge = outline_color if outline else shade(0.3)
    lw   = 2 if outline else 1
    for poly in [top, left, right]:
        cv2.polylines(frame, [poly], True, edge, lw, cv2.LINE_AA)


class CubeRenderer:
    def __init__(self, cfg) -> None:
        self.cc = cfg.cube

    def render(self, frame, world: CubeWorld,
               ghost: Optional[tuple],
               erase_ghost: Optional[tuple],
               current_layer: int,
               move_mode: str) -> np.ndarray:
        cc = self.cc
        gs, ox, oy = cc.grid_size, cc.iso_offset_x, cc.iso_offset_y
        selected_set = set(world.selected_group)

        # Draw back→front
        for gz in range(cc.num_layers-1, -1, -1):
            color = cc.layer_colors[gz]
            layer_cubes = sorted(
                [c for c in world.cubes if c.gz == gz],
                key=lambda c: (-c.gy, -c.gx)
            )
            for cube in layer_cubes:
                px, py = _cube_origin(cube.gx, cube.gy, cube.gz, gs, ox, oy)
                is_sel = cube in selected_set
                _draw_iso_cube(frame, px, py, gs, color, 1.0,
                               outline=is_sel, outline_color=cc.selected_color)

        if ghost:
            px, py = _cube_origin(*ghost, gs, ox, oy)
            _draw_iso_cube(frame, px, py, gs, cc.layer_colors[ghost[2]],
                           cc.ghost_alpha, True, (200,200,200))

        if erase_ghost:
            px, py = _cube_origin(*erase_ghost, gs, ox, oy)
            _draw_iso_cube(frame, px, py, gs, cc.erase_color, 0.4, True, (0,0,255))

        self._draw_toggle(frame, move_mode)
        self._draw_layer_bar(frame, current_layer)
        return frame

    def _draw_toggle(self, frame, move_mode):
        cc = self.cc
        x, y, w, h = cc.toggle_btn_x, cc.toggle_btn_y, cc.toggle_btn_w, cc.toggle_btn_h
        cv2.rectangle(frame, (x,y), (x+w,y+h), (30,30,30), -1)
        cv2.rectangle(frame, (x,y), (x+w,y+h), (0,200,200), 1)
        cv2.putText(frame, "MOVE MODE", (x+8,y+18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180,180,180), 1, cv2.LINE_AA)
        for i, (label, mode) in enumerate([("Single cube","single"),("Group","group")]):
            col = (0,255,200) if move_mode == mode else (100,100,100)
            cy  = y + 34 + i*18
            cv2.circle(frame, (x+14, cy), 6, col, -1 if move_mode==mode else 1)
            cv2.putText(frame, label, (x+24, cy+4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, col, 1, cv2.LINE_AA)

    def _draw_layer_bar(self, frame, current_layer):
        cc  = self.cc
        x, y = 10, 200
        bar_h, gap = 16, 4
        cv2.putText(frame, "DEPTH", (x, y-6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (180,180,180), 1, cv2.LINE_AA)
        for i in range(cc.num_layers):
            by     = y + i*(bar_h+gap)
            color  = cc.layer_colors[i]
            active = (i == current_layer)
            cv2.rectangle(frame, (x,by), (x+80,by+bar_h),
                          color if active else (40,40,40), -1)
            cv2.rectangle(frame, (x,by), (x+80,by+bar_h),
                          (0,200,200) if active else (60,60,60), 1)
            cv2.putText(frame, f"L{i}" + (" <" if active else ""),
                        (x+4, by+12), cv2.FONT_HERSHEY_SIMPLEX, 0.35,
                        (255,255,255) if active else (120,120,120), 1, cv2.LINE_AA)
