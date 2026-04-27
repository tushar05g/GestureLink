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
from renderer_2d import IsometricRenderer2D

logger = logging.getLogger(__name__)


class AppMode(Enum):
    PRODUCTIVITY = auto()
    BUILDER      = auto()


class BuilderController:
    """
    All Builder Mode logic (2D Isometric Overlay version):
      - Ghost cube / paint / erase / undo
      - Pinch → add cube
    """

    def __init__(self, cfg) -> None:
        self.cfg   = cfg
        self.cc    = cfg.cube
        self.world = CubeWorld()
        self.renderer = IsometricRenderer2D(size=cfg.cube.grid_size)

        self.current_layer: int = 0
        
        self._ghost:        Optional[tuple] = None
        self._erase_ghost:  Optional[tuple] = None

        # Paint state
        self._painting:      bool  = False
        self._paint_hold:    int   = 0
        self._last_painted:  Optional[tuple] = None

    def update(
        self,
        gesture:  str,
        nx:       float,
        ny:       float,
        frame_w:  int,
        frame_h:  int,
        gesture_state,
    ) -> str:
        """Process one frame. Returns status string."""
        
        # Determine depth layer (z) from y position
        self.current_layer = int(ny * 5)
        gz = self.current_layer

        # Grid mapping (10x10 grid on screen)
        gx = int(nx * 10)
        gy = int(ny * 10)

        self._ghost       = None
        self._erase_ghost = None
        status = "BUILDER"

        # ---- Gestures ----------------------------------------
        if gesture == "POINTING":
            self._ghost = (gx, gy, gz)
            self._painting    = False
            self._paint_hold  = 0
            status = "PREVIEW"

        elif gesture == "PINCH":
            self._paint_hold += 1
            self._ghost = (gx, gy, gz)
            if self._paint_hold >= 5:
                self._painting = True
            if self._painting:
                pos = (gx, gy, gz)
                if pos != self._last_painted:
                    self.world.place(*pos)
                    self._last_painted = pos
            status = "PAINTING"

        elif gesture == "SCROLL":
            self._painting    = False
            self._paint_hold  = 0
            self.world.erase(gx, gy, gz)
            status = "ERASING"

        elif gesture == "RIGHT_CLICK":
            self._painting   = False
            self._paint_hold = 0
            self.world.undo()
            status = "UNDO"

        return status

    def render(self, frame):
        self.renderer.render(frame, self.world, ghost=self._ghost)

    @property
    def ghost(self):       return self._ghost
    @property
    def erase_ghost(self): return self._erase_ghost
