"""
gl_renderer.py — PyOpenGL 3D cube renderer.

Renders:
  - 3D grid of cubes with lighting (ambient + directional)
  - Ghost cube (semi-transparent)
  - Webcam PIP in bottom-right corner
  - Coordinate axes (debug, optional)

Camera system:
  - Orbit camera (distance, yaw, pitch)
  - Controlled by gesture state from modes.py
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Deferred imports — only available when OpenGL is installed
try:
    from OpenGL.GL import *
    from OpenGL.GLU import *
    import cv2
    _GL_AVAILABLE = True
except ImportError:
    _GL_AVAILABLE = False
    logger.warning("PyOpenGL not available — Builder 3D mode disabled.")


# ---------------------------------------------------------------------------
# Cube geometry (unit cube, centred at origin)
# ---------------------------------------------------------------------------

# 6 faces × 4 vertices × 3 coords
_CUBE_VERTICES = np.array([
    # Front
    [-0.5,-0.5, 0.5], [ 0.5,-0.5, 0.5], [ 0.5, 0.5, 0.5], [-0.5, 0.5, 0.5],
    # Back
    [-0.5,-0.5,-0.5], [-0.5, 0.5,-0.5], [ 0.5, 0.5,-0.5], [ 0.5,-0.5,-0.5],
    # Top
    [-0.5, 0.5,-0.5], [-0.5, 0.5, 0.5], [ 0.5, 0.5, 0.5], [ 0.5, 0.5,-0.5],
    # Bottom
    [-0.5,-0.5,-0.5], [ 0.5,-0.5,-0.5], [ 0.5,-0.5, 0.5], [-0.5,-0.5, 0.5],
    # Right
    [ 0.5,-0.5,-0.5], [ 0.5, 0.5,-0.5], [ 0.5, 0.5, 0.5], [ 0.5,-0.5, 0.5],
    # Left
    [-0.5,-0.5,-0.5], [-0.5,-0.5, 0.5], [-0.5, 0.5, 0.5], [-0.5, 0.5,-0.5],
], dtype=np.float32)

_CUBE_NORMALS = np.array([
    [ 0, 0, 1],[ 0, 0, 1],[ 0, 0, 1],[ 0, 0, 1],  # Front
    [ 0, 0,-1],[ 0, 0,-1],[ 0, 0,-1],[ 0, 0,-1],  # Back
    [ 0, 1, 0],[ 0, 1, 0],[ 0, 1, 0],[ 0, 1, 0],  # Top
    [ 0,-1, 0],[ 0,-1, 0],[ 0,-1, 0],[ 0,-1, 0],  # Bottom
    [ 1, 0, 0],[ 1, 0, 0],[ 1, 0, 0],[ 1, 0, 0],  # Right
    [-1, 0, 0],[-1, 0, 0],[-1, 0, 0],[-1, 0, 0],  # Left
], dtype=np.float32)

# Face indices (quads as 2 triangles each)
_CUBE_INDICES = []
for face in range(6):
    base = face * 4
    _CUBE_INDICES += [base, base+1, base+2, base, base+2, base+3]
_CUBE_INDICES = np.array(_CUBE_INDICES, dtype=np.uint32)


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------

class OrbitCamera:
    """Orbit camera controlled by yaw/pitch/distance."""

    def __init__(self, cfg) -> None:
        self.distance: float = cfg.opengl.cam_distance
        self.yaw:      float = 30.0   # degrees
        self.pitch:    float = 25.0   # degrees
        self._min_dist = cfg.opengl.cam_distance_min
        self._max_dist = cfg.opengl.cam_distance_max

    def rotate(self, dyaw: float, dpitch: float) -> None:
        self.yaw   = (self.yaw + dyaw) % 360.0
        self.pitch = max(-89.0, min(89.0, self.pitch + dpitch))

    def zoom(self, delta: float) -> None:
        self.distance = max(self._min_dist,
                           min(self._max_dist, self.distance + delta))

    def apply(self) -> None:
        """Apply the camera transform (call inside GL context)."""
        glLoadIdentity()
        glTranslatef(0.0, 0.0, -self.distance)
        glRotatef(self.pitch, 1.0, 0.0, 0.0)
        glRotatef(self.yaw,   0.0, 1.0, 0.0)


# ---------------------------------------------------------------------------
# Main Renderer
# ---------------------------------------------------------------------------

class GLRenderer:
    """OpenGL renderer — call setup() once after GL context created."""

    # Neon cyan colour per depth layer (normalised RGB)
    _LAYER_COLORS = [
        (0.0, 1.0, 1.0),    # layer 0 — brightest
        (0.0, 0.86, 0.86),
        (0.0, 0.73, 0.73),
        (0.0, 0.59, 0.59),
        (0.0, 0.45, 0.45),
        (0.0, 0.31, 0.31),
        (0.0, 0.20, 0.20),  # layer 6 — darkest
    ]
    _SELECTED_COLOR = (1.0, 1.0, 0.0)   # yellow outline for selected
    _GHOST_COLOR    = (0.0, 0.8, 0.8)
    _ERASE_COLOR    = (1.0, 0.2, 0.2)

    def __init__(self, cfg, world) -> None:
        self.cfg    = cfg
        self.cc     = cfg.cube
        self.gl_cfg = cfg.opengl
        self.world  = world
        self.camera = OrbitCamera(cfg)
        self._pip_texture: Optional[int] = None
        self._pip_w = cfg.opengl.pip_w
        self._pip_h = cfg.opengl.pip_h

    # ------------------------------------------------------------------
    def setup(self, viewport_w: int, viewport_h: int) -> None:
        """Initialise GL state. Call once after window creation."""
        self._vp_w = viewport_w
        self._vp_h = viewport_h

        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)
        glEnable(GL_LIGHT0)
        glEnable(GL_COLOR_MATERIAL)
        glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
        glEnable(GL_NORMALIZE)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        # Lighting
        glLightfv(GL_LIGHT0, GL_POSITION,  [5.0, 10.0, 5.0, 1.0])
        glLightfv(GL_LIGHT0, GL_AMBIENT,   [0.3, 0.3, 0.3, 1.0])
        glLightfv(GL_LIGHT0, GL_DIFFUSE,   [0.9, 0.9, 0.9, 1.0])
        glLightfv(GL_LIGHT0, GL_SPECULAR,  [0.4, 0.4, 0.4, 1.0])

        # Background — deep space dark
        glClearColor(0.04, 0.04, 0.10, 1.0)

        # PIP texture
        self._pip_texture = glGenTextures(1)
        logger.info("GLRenderer setup complete (%dx%d)", viewport_w, viewport_h)

    # ------------------------------------------------------------------
    def render(
        self,
        ghost:       Optional[tuple[int,int,int]],
        erase_ghost: Optional[tuple[int,int,int]],
        selected_set: set,
        webcam_frame: Optional[np.ndarray],
    ) -> None:
        """Full render pass — call every frame."""
        glViewport(0, 0, self._vp_w, self._vp_h)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        # --- Projection ---
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(
            self.gl_cfg.fov,
            self._vp_w / max(self._vp_h, 1),
            self.gl_cfg.near_clip,
            self.gl_cfg.far_clip,
        )

        # --- Modelview ---
        glMatrixMode(GL_MODELVIEW)
        self.camera.apply()

        # --- Draw grid reference ---
        self._draw_grid()

        # --- Draw placed cubes ---
        for cube in self.world.cubes:
            color = self._LAYER_COLORS[min(cube.gz, len(self._LAYER_COLORS)-1)]
            is_selected = cube in selected_set
            self._draw_cube(cube.gx, cube.gy, cube.gz, color,
                           alpha=1.0, selected=is_selected)

        # --- Draw ghost ---
        if ghost:
            gx, gy, gz = ghost
            color = self._LAYER_COLORS[min(gz, len(self._LAYER_COLORS)-1)]
            self._draw_cube(gx, gy, gz, color, alpha=0.35)

        # --- Draw erase ghost ---
        if erase_ghost:
            gx, gy, gz = erase_ghost
            self._draw_cube(gx, gy, gz, self._ERASE_COLOR, alpha=0.4)

        # --- PIP webcam ---
        if webcam_frame is not None:
            self._draw_pip(webcam_frame)

    # ------------------------------------------------------------------
    def _draw_cube(
        self,
        gx: int, gy: int, gz: int,
        color: tuple[float,float,float],
        alpha: float = 1.0,
        selected: bool = False,
    ) -> None:
        glPushMatrix()
        # Grid spacing = 1 unit per cell
        # Y inverted so gy=0 is at top visually
        glTranslatef(float(gx), -float(gy), -float(gz))

        if alpha < 1.0:
            glDepthMask(GL_FALSE)

        glColor4f(color[0], color[1], color[2], alpha)

        # Draw faces
        glBegin(GL_TRIANGLES)
        for idx in _CUBE_INDICES:
            glNormal3fv(_CUBE_NORMALS[idx])
            glVertex3fv(_CUBE_VERTICES[idx])
        glEnd()

        if alpha < 1.0:
            glDepthMask(GL_TRUE)

        # Selected outline
        if selected:
            glDisable(GL_LIGHTING)
            glColor4f(*self._SELECTED_COLOR, 1.0)
            glLineWidth(2.0)
            glPolygonMode(GL_FRONT_AND_BACK, GL_LINE)
            glBegin(GL_TRIANGLES)
            for idx in _CUBE_INDICES:
                glVertex3fv(_CUBE_VERTICES[idx])
            glEnd()
            glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)
            glEnable(GL_LIGHTING)

        glPopMatrix()

    # ------------------------------------------------------------------
    def _draw_grid(self) -> None:
        """Draw a subtle reference grid on the Y=0 plane."""
        glDisable(GL_LIGHTING)
        glColor4f(0.15, 0.15, 0.25, 1.0)
        glLineWidth(1.0)
        size = 16
        glBegin(GL_LINES)
        for i in range(-size, size + 1):
            glVertex3f(float(i), 0.5, float(-size))
            glVertex3f(float(i), 0.5, float(size))
            glVertex3f(float(-size), 0.5, float(i))
            glVertex3f(float(size),  0.5, float(i))
        glEnd()
        glEnable(GL_LIGHTING)

    # ------------------------------------------------------------------
    def _draw_pip(self, frame_bgr: np.ndarray) -> None:
        """Draw webcam feed as 2D overlay in bottom-right corner."""
        import cv2

        pip = cv2.resize(frame_bgr, (self._pip_w, self._pip_h))
        pip = cv2.flip(pip, 1)                         # mirror
        pip_rgb = cv2.cvtColor(pip, cv2.COLOR_BGR2RGB)
        pip_rgb = np.flipud(pip_rgb)                   # GL origin is bottom-left

        # Switch to 2D ortho for overlay
        glMatrixMode(GL_PROJECTION)
        glPushMatrix()
        glLoadIdentity()
        glOrtho(0, self._vp_w, 0, self._vp_h, -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glPushMatrix()
        glLoadIdentity()

        glDisable(GL_LIGHTING)
        glDisable(GL_DEPTH_TEST)

        # Upload texture
        glBindTexture(GL_TEXTURE_2D, self._pip_texture)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB,
                     self._pip_w, self._pip_h, 0,
                     GL_RGB, GL_UNSIGNED_BYTE, pip_rgb)

        glEnable(GL_TEXTURE_2D)

        # Position: bottom-right corner with 10px margin
        x = self._vp_w - self._pip_w - 10
        y = 10

        glColor4f(1.0, 1.0, 1.0, 1.0)
        glBegin(GL_QUADS)
        glTexCoord2f(0, 0); glVertex2f(x,              y)
        glTexCoord2f(1, 0); glVertex2f(x + self._pip_w, y)
        glTexCoord2f(1, 1); glVertex2f(x + self._pip_w, y + self._pip_h)
        glTexCoord2f(0, 1); glVertex2f(x,              y + self._pip_h)
        glEnd()

        # Border
        glDisable(GL_TEXTURE_2D)
        glColor4f(0.0, 0.8, 0.8, 1.0)
        glLineWidth(2.0)
        glBegin(GL_LINE_LOOP)
        glVertex2f(x,               y)
        glVertex2f(x + self._pip_w, y)
        glVertex2f(x + self._pip_w, y + self._pip_h)
        glVertex2f(x,               y + self._pip_h)
        glEnd()

        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)

        glMatrixMode(GL_PROJECTION)
        glPopMatrix()
        glMatrixMode(GL_MODELVIEW)
        glPopMatrix()
