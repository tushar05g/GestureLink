"""
renderer.py — Isometric cube renderer on top of OpenCV webcam frame.

Each cube is drawn as a 3-face isometric projection (top, left, right faces).
Depth layers are offset diagonally so farther cubes appear behind nearer ones.
"""
from __future__ import annotations

import logging
from typing import Optional

import cv2
import numpy as np

from world import Cube, CubeWorld

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Isometric projection helpers
# ---------------------------------------------------------------------------

def _cube_origin(gx: int, gy: int, gz: int, gs: int, ox: int, oy: int) -> tuple[int, int]:
    """
    Convert grid coords → screen pixel origin (top-left of cube front face).
    gz shifts the cube diagonally to simulate depth.
    ox, oy = isometric offset per layer (from CubeConfig).
    """
    px = gx * gs + gz * ox
    py = gy * gs - gz * oy
    return int(px), int(py)


def _draw_iso_cube(
    frame: np.ndarray,
    px: int,
    py: int,
    gs: int,
    color_bgr: tuple[int, int, int],
    alpha: float = 1.0,
    outline: bool = False,
    outline_color: tuple[int, int, int] = (0, 255, 255),
) -> None:
    """
    Draw a single isometric cube at pixel origin (px, py).

    Faces:
      Top   : lighter shade
      Left  : base color
      Right : darker shade
    """
    h = gs // 2   # half-step for isometric top face height

    b, g, r = color_bgr

    def _shade(factor: float) -> tuple[int, int, int]:
        return (
            int(min(255, b * factor)),
            int(min(255, g * factor)),
            int(min(255, r * factor)),
        )

    top_color   = _shade(1.4)
    left_color  = _shade(1.0)
    right_color = _shade(0.6)

    # Face polygons
    # Top face (diamond)
    top = np.array([
        [px + gs//2, py],
        [px + gs,    py + h],
        [px + gs//2, py + gs//2 + h//2],
        [px,         py + h],
    ], np.int32)

    # Left face
    left = np.array([
        [px,         py + h],
        [px + gs//2, py + gs//2 + h//2],
        [px + gs//2, py + gs],
        [px,         py + gs - h//2],
    ], np.int32)

    # Right face
    right = np.array([
        [px + gs//2, py + gs//2 + h//2],
        [px + gs,    py + h],
        [px + gs,    py + gs - h//2],
        [px + gs//2, py + gs],
    ], np.int32)

    if alpha < 1.0:
        overlay = frame.copy()
        cv2.fillPoly(overlay, [top],   top_color)
        cv2.fillPoly(overlay, [left],  left_color)
        cv2.fillPoly(overlay, [right], right_color)
        cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)
    else:
        cv2.fillPoly(frame, [top],   top_color)
        cv2.fillPoly(frame, [left],  left_color)
        cv2.fillPoly(frame, [right], right_color)

    # Outline
    if outline:
        for poly in [top, left, right]:
            cv2.polylines(frame, [poly], True, outline_color, 1, cv2.LINE_AA)
    else:
        # Subtle dark edge lines always
        dark = _shade(0.3)
        for poly in [top, left, right]:
            cv2.polylines(frame, [poly], True, dark, 1, cv2.LINE_AA)


# ---------------------------------------------------------------------------
# Main Renderer
# ---------------------------------------------------------------------------

class CubeRenderer:
    """Renders the CubeWorld onto an OpenCV BGR frame."""

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.cc  = cfg.cube

    # ------------------------------------------------------------------
    def render(
        self,
        frame: np.ndarray,
        world: CubeWorld,
        ghost: Optional[tuple[int,int,int]],      # (gx, gy, gz) or None
        erase_ghost: Optional[tuple[int,int,int]], # erase brush position
        current_layer: int,
        move_mode: str,   # "single" or "group"
    ) -> np.ndarray:
        """
        Draw all cubes + ghost onto frame (in-place).
        Cubes are drawn back-to-front (farthest layer first) so nearer
        cubes correctly occlude farther ones.
        """
        cc  = self.cc
        gs  = cc.grid_size
        ox  = cc.iso_offset_x
        oy  = cc.iso_offset_y
        h, w = frame.shape[:2]

        selected_set = set(world.selected_group)

        # Draw layers back → front (gz=6 first, gz=0 last)
        for gz in range(cc.num_layers - 1, -1, -1):
            color = cc.layer_colors[gz]
            layer_cubes = [c for c in world.cubes if c.gz == gz]

            # Sort back-to-front within layer (larger gy first)
            layer_cubes.sort(key=lambda c: (-c.gy, -c.gx))

            for cube in layer_cubes:
                px, py = _cube_origin(cube.gx, cube.gy, cube.gz, gs, ox, oy)
                is_selected = cube in selected_set
                _draw_iso_cube(
                    frame, px, py, gs, color,
                    alpha=1.0,
                    outline=is_selected,
                    outline_color=cc.selected_color,
                )

        # Draw ghost cube (placement preview)
        if ghost:
            gx, gy, gz = ghost
            px, py = _cube_origin(gx, gy, gz, gs, ox, oy)
            color  = cc.layer_colors[gz]
            _draw_iso_cube(frame, px, py, gs, color,
                           alpha=cc.ghost_alpha, outline=True,
                           outline_color=(200, 200, 200))

        # Draw erase brush ghost (red tint)
        if erase_ghost:
            gx, gy, gz = erase_ghost
            px, py = _cube_origin(gx, gy, gz, gs, ox, oy)
            _draw_iso_cube(frame, px, py, gs, cc.erase_color,
                           alpha=0.4, outline=True,
                           outline_color=(0, 0, 255))

        # Draw toggle button
        self._draw_toggle(frame, move_mode)

        # Draw layer indicator
        self._draw_layer_indicator(frame, current_layer)

        return frame

    # ------------------------------------------------------------------
    def _draw_toggle(self, frame: np.ndarray, move_mode: str) -> None:
        cc = self.cc
        x, y, w, h = cc.toggle_btn_x, cc.toggle_btn_y, cc.toggle_btn_w, cc.toggle_btn_h

        cv2.rectangle(frame, (x, y), (x+w, y+h), (30, 30, 30), -1)
        cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 200, 200), 1)
        cv2.putText(frame, "MOVE MODE", (x+8, y+18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 180, 180), 1, cv2.LINE_AA)

        # Single option
        s_col = (0, 255, 200) if move_mode == "single" else (100, 100, 100)
        cv2.circle(frame, (x+14, y+34), 6, s_col, -1 if move_mode == "single" else 1)
        cv2.putText(frame, "Single cube", (x+24, y+38),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, s_col, 1, cv2.LINE_AA)

        # Group option
        g_col = (0, 255, 200) if move_mode == "group" else (100, 100, 100)
        cv2.circle(frame, (x+14, y+52), 6, g_col, -1 if move_mode == "group" else 1)
        cv2.putText(frame, "Group", (x+24, y+56),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, g_col, 1, cv2.LINE_AA)

    # ------------------------------------------------------------------
    def _draw_layer_indicator(self, frame: np.ndarray, current_layer: int) -> None:
        cc  = self.cc
        x, y = 10, 200
        bar_h = 16
        gap   = 4

        cv2.putText(frame, "DEPTH", (x, y - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (180, 180, 180), 1, cv2.LINE_AA)

        for i in range(cc.num_layers):
            by = y + i * (bar_h + gap)
            color = cc.layer_colors[i]
            is_active = (i == current_layer)
            cv2.rectangle(frame, (x, by), (x + 80, by + bar_h),
                          color if is_active else (40, 40, 40), -1)
            cv2.rectangle(frame, (x, by), (x + 80, by + bar_h),
                          (0, 200, 200) if is_active else (60, 60, 60), 1)
            label = f"L{i}" + (" <" if is_active else "")
            cv2.putText(frame, label, (x + 4, by + 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35,
                        (255, 255, 255) if is_active else (120, 120, 120),
                        1, cv2.LINE_AA)