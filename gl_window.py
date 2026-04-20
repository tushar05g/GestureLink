"""
gl_window.py — GLFW window management for the 3D Builder Mode.

Runs in the main thread alongside the gesture pipeline.
Handles:
  - Window creation / destruction
  - Render loop
  - Text overlay (status, mode badge) via OpenCV blitted to GL
"""
from __future__ import annotations

import logging
import threading
from typing import Optional, Callable

import numpy as np

logger = logging.getLogger(__name__)

try:
    import glfw
    from OpenGL.GL import *
    import cv2
    _GLFW_AVAILABLE = True
except ImportError:
    _GLFW_AVAILABLE = False
    logger.warning("GLFW/OpenGL not available.")


class GLWindow:
    """
    Full-screen GLFW window for 3D Builder rendering.
    Call open() to create, close() to destroy, render_frame() each loop.
    """

    def __init__(self, cfg, world) -> None:
        self.cfg   = cfg
        self._w    = cfg.screen_w
        self._h    = cfg.screen_h
        self._win  = None
        self._renderer = None
        self._world = world
        self._open  = False

    # ------------------------------------------------------------------
    def open(self) -> bool:
        """Create the GLFW window. Returns True on success."""
        if not _GLFW_AVAILABLE:
            logger.error("GLFW not available — install glfw + PyOpenGL.")
            return False

        if not glfw.init():
            logger.error("GLFW init failed.")
            return False

        glfw.window_hint(glfw.DECORATED, False)   # borderless
        self._win = glfw.create_window(
            self._w, self._h, "Aether Builder", None, None
        )
        if not self._win:
            logger.error("GLFW window creation failed.")
            glfw.terminate()
            return False

        glfw.make_context_current(self._win)
        glfw.swap_interval(1)   # vsync

        from gl_renderer import GLRenderer
        self._renderer = GLRenderer(self.cfg, self._world)
        self._renderer.setup(self._w, self._h)

        self._open = True
        logger.info("GL window opened (%dx%d)", self._w, self._h)
        return True

    # ------------------------------------------------------------------
    def is_open(self) -> bool:
        if not self._open or self._win is None:
            return False
        return not glfw.window_should_close(self._win)

    # ------------------------------------------------------------------
    def render_frame(
        self,
        ghost:        Optional[tuple],
        erase_ghost:  Optional[tuple],
        selected_set: set,
        webcam_frame: Optional[np.ndarray],
        status:       str,
        mode_str:     str,
        pinky_progress: float,
    ) -> None:
        """Render one frame. Call every loop iteration."""
        if not self._open or self._renderer is None:
            return

        glfw.make_context_current(self._win)
        glfw.poll_events()

        # 3D scene
        self._renderer.render(ghost, erase_ghost, selected_set, webcam_frame)

        # 2D text overlay
        self._draw_text_overlay(status, mode_str, pinky_progress)

        glfw.swap_buffers(self._win)

    # ------------------------------------------------------------------
    def _draw_text_overlay(self, status: str, mode_str: str, pinky_progress: float) -> None:
        """Render status text as 2D OpenCV texture overlay."""
        # Create a transparent RGBA overlay image
        overlay = np.zeros((self._h, self._w, 4), dtype=np.uint8)

        # Mode badge
        color = (0, 220, 255, 255)
        cv2.rectangle(overlay, (self._w//2 - 140, 10),
                      (self._w//2 + 140, 55), (20, 20, 20, 200), -1)
        cv2.rectangle(overlay, (self._w//2 - 140, 10),
                      (self._w//2 + 140, 55), color[:3], 1)
        cv2.putText(overlay, mode_str,
                    (self._w//2 - 128, 42),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, 2, cv2.LINE_AA)

        # Status badge
        cv2.rectangle(overlay, (10, 10), (280, 55), (20, 20, 20, 200), -1)
        cv2.rectangle(overlay, (10, 10), (280, 55), (0, 220, 255, 255), 1)
        cv2.putText(overlay, f"Builder: {status}",
                    (18, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 220, 255, 255), 2, cv2.LINE_AA)

        # Pinky progress bar
        if pinky_progress > 0:
            bar_w = int((self._w - 20) * pinky_progress)
            cv2.rectangle(overlay, (10, 62), (self._w - 10, 74),
                          (40, 40, 40, 200), -1)
            cv2.rectangle(overlay, (10, 62), (10 + bar_w, 74),
                          (0, 200, 255, 255), -1)

        # Legend bottom
        legend = [
            "[1] Index only              ->  Ghost preview",
            "[2] Index+Middle + drag     ->  Paint cubes",
            "[3] Thumb+Index touch       ->  Move cube/group",
            "[4] 3 fingertips            ->  Erase",
            "[5] Rock sign               ->  Undo",
            "[6] Left Fist + Right sweep ->  Rotate",
            "[7] Left Fist + Thumb+Index ->  Zoom in/out",
            "[8] Pinky hold              ->  Switch mode",
        ]
        for i, line in enumerate(legend):
            cv2.putText(overlay, line,
                        (12, self._h - 15 - (len(legend) - 1 - i) * 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                        (160, 160, 160, 200), 1, cv2.LINE_AA)

        # Blit overlay as GL texture
        self._blit_overlay(overlay)

    # ------------------------------------------------------------------
    def _blit_overlay(self, overlay_rgba: np.ndarray) -> None:
        """Blit an RGBA numpy image as a full-screen 2D texture."""
        h, w = overlay_rgba.shape[:2]
        img  = np.flipud(overlay_rgba).copy()

        glMatrixMode(GL_PROJECTION)
        glPushMatrix()
        glLoadIdentity()
        glOrtho(0, w, 0, h, -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glPushMatrix()
        glLoadIdentity()

        glDisable(GL_LIGHTING)
        glDisable(GL_DEPTH_TEST)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        tex = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, tex)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0,
                     GL_RGBA, GL_UNSIGNED_BYTE, img)

        glEnable(GL_TEXTURE_2D)
        glColor4f(1, 1, 1, 1)
        glBegin(GL_QUADS)
        glTexCoord2f(0,0); glVertex2f(0, 0)
        glTexCoord2f(1,0); glVertex2f(w, 0)
        glTexCoord2f(1,1); glVertex2f(w, h)
        glTexCoord2f(0,1); glVertex2f(0, h)
        glEnd()

        glDisable(GL_TEXTURE_2D)
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)

        glDeleteTextures([tex])

        glMatrixMode(GL_PROJECTION)
        glPopMatrix()
        glMatrixMode(GL_MODELVIEW)
        glPopMatrix()

    # ------------------------------------------------------------------
    def close(self) -> None:
        if self._win:
            glfw.destroy_window(self._win)
            glfw.terminate()
            self._win  = None
            self._open = False
            logger.info("GL window closed.")
