"""
gl_window.py — GLFW window for 3D Builder Mode.
Text overlay is handled by GLRenderer directly (cached texture).
"""
from __future__ import annotations

import logging
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)

try:
    import glfw
    from OpenGL.GL import *
    _GLFW_AVAILABLE = True
except ImportError:
    _GLFW_AVAILABLE = False
    logger.warning("GLFW not available.")


class GLWindow:
    def __init__(self, cfg, world) -> None:
        self.cfg        = cfg
        self._w         = cfg.screen_w
        self._h         = cfg.screen_h
        self._win       = None
        self._renderer  = None
        self._world     = world
        self._open      = False

    def open(self) -> bool:
        if not _GLFW_AVAILABLE:
            logger.error("GLFW not installed — run: pip install glfw PyOpenGL PyOpenGL_accelerate")
            return False
        if not glfw.init():
            logger.error("GLFW init failed.")
            return False

        glfw.window_hint(glfw.DECORATED, False)
        self._win = glfw.create_window(self._w, self._h, "Aether Builder", None, None)
        if not self._win:
            logger.error("GLFW window creation failed.")
            glfw.terminate()
            return False

        glfw.make_context_current(self._win)
        glfw.swap_interval(1)

        from gl_renderer import GLRenderer
        self._renderer = GLRenderer(self.cfg, self._world)
        self._renderer.setup(self._w, self._h)
        self._open = True
        logger.info("GL window opened (%dx%d)", self._w, self._h)
        return True

    def is_open(self) -> bool:
        if not self._open or not self._win:
            return False
        return not glfw.window_should_close(self._win)

    def render_frame(
        self,
        ghost:          Optional[tuple],
        erase_ghost:    Optional[tuple],
        selected_set:   set,
        webcam_frame:   Optional[np.ndarray],
        status:         str,
        pinky_progress: float,
        mode_str:       Optional[str] = None,
    ) -> None:
        if not self._open or not self._renderer:
            return
        glfw.make_context_current(self._win)
        glfw.poll_events()
        self._renderer.render(
            ghost        = ghost,
            erase_ghost  = erase_ghost,
            selected_set = selected_set,
            webcam_frame = webcam_frame,
            status       = status,
            pinky_progress = pinky_progress,
        )
        glfw.swap_buffers(self._win)

    def close(self) -> None:
        if self._win:
            glfw.destroy_window(self._win)
            glfw.terminate()
            self._win  = None
            self._open = False
            logger.info("GL window closed.")
