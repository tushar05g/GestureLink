"""
gl_renderer.py — Optimised PyOpenGL 3D cube renderer.

Performance improvements over v1:
  - VBO for cube geometry (upload once, draw many)
  - glTexSubImage2D for PIP updates (no re-allocation)
  - Cached overlay texture (only rebuilt when status changes)
  - Instanced-style draw loop with minimal state changes
"""
from __future__ import annotations

import ctypes
import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

try:
    from OpenGL.GL import *
    from OpenGL.GLU import *
    import cv2
    _GL_AVAILABLE = True
except ImportError:
    _GL_AVAILABLE = False
    logger.warning("PyOpenGL not available.")


# ---------------------------------------------------------------------------
# Cube geometry (unit cube, centred at origin) — built once into a VBO
# ---------------------------------------------------------------------------

def _build_cube_data() -> tuple[np.ndarray, np.ndarray]:
    """Return (vertices+normals interleaved, indices) for a unit cube."""
    faces = [
        # (normal,  4 vertices)
        (( 0, 0, 1), [(-0.5,-0.5, 0.5),( 0.5,-0.5, 0.5),( 0.5, 0.5, 0.5),(-0.5, 0.5, 0.5)]),
        (( 0, 0,-1), [(-0.5,-0.5,-0.5),(-0.5, 0.5,-0.5),( 0.5, 0.5,-0.5),( 0.5,-0.5,-0.5)]),
        (( 0, 1, 0), [(-0.5, 0.5,-0.5),(-0.5, 0.5, 0.5),( 0.5, 0.5, 0.5),( 0.5, 0.5,-0.5)]),
        (( 0,-1, 0), [(-0.5,-0.5,-0.5),( 0.5,-0.5,-0.5),( 0.5,-0.5, 0.5),(-0.5,-0.5, 0.5)]),
        (( 1, 0, 0), [( 0.5,-0.5,-0.5),( 0.5, 0.5,-0.5),( 0.5, 0.5, 0.5),( 0.5,-0.5, 0.5)]),
        ((-1, 0, 0), [(-0.5,-0.5,-0.5),(-0.5,-0.5, 0.5),(-0.5, 0.5, 0.5),(-0.5, 0.5,-0.5)]),
    ]
    verts, indices = [], []
    vi = 0
    for normal, quad in faces:
        for v in quad:
            verts += list(v) + list(normal)   # x,y,z, nx,ny,nz
        # Two triangles per quad
        indices += [vi, vi+1, vi+2, vi, vi+2, vi+3]
        vi += 4
    return (np.array(verts, dtype=np.float32),
            np.array(indices, dtype=np.uint32))


# ---------------------------------------------------------------------------
# Orbit Camera
# ---------------------------------------------------------------------------

class OrbitCamera:
    def __init__(self, cfg) -> None:
        self.distance: float = cfg.opengl.cam_distance
        self.yaw:      float = 30.0
        self.pitch:    float = 25.0
        self._min     = cfg.opengl.cam_distance_min
        self._max     = cfg.opengl.cam_distance_max

    def rotate(self, dyaw: float, dpitch: float) -> None:
        self.yaw   = (self.yaw + dyaw) % 360.0
        self.pitch = max(-89.0, min(89.0, self.pitch + dpitch))

    def zoom(self, delta: float) -> None:
        self.distance = max(self._min, min(self._max, self.distance + delta))

    def apply(self) -> None:
        glLoadIdentity()
        glTranslatef(0.0, 0.0, -self.distance)
        glRotatef(self.pitch, 1.0, 0.0, 0.0)
        glRotatef(self.yaw,   0.0, 1.0, 0.0)


# ---------------------------------------------------------------------------
# Main GL Renderer
# ---------------------------------------------------------------------------

_LAYER_COLORS = [
    (0.0, 1.0,  1.0),
    (0.0, 0.86, 0.86),
    (0.0, 0.73, 0.73),
    (0.0, 0.59, 0.59),
    (0.0, 0.45, 0.45),
    (0.0, 0.31, 0.31),
    (0.0, 0.20, 0.20),
]
_SELECTED_COLOR = (1.0, 1.0, 0.0)
_GHOST_COLOR    = (0.0, 0.8, 0.8)
_ERASE_COLOR    = (1.0, 0.2, 0.2)


class GLRenderer:
    def __init__(self, cfg, world) -> None:
        self.cfg    = cfg
        self.cc     = cfg.cube
        self.gl_cfg = cfg.opengl
        self.world  = world
        self.camera = OrbitCamera(cfg)

        # VBO handles
        self._vbo     = None
        self._ebo     = None
        self._n_idx   = 0

        # PIP texture
        self._pip_tex   = None
        self._pip_w     = cfg.opengl.pip_w
        self._pip_h     = cfg.opengl.pip_h
        self._pip_init  = False   # True after first upload

        # Overlay texture cache
        self._overlay_tex      = None
        self._overlay_status   = ""   # last status rendered
        self._overlay_dirty    = True

        self._vp_w = 0
        self._vp_h = 0

    # ------------------------------------------------------------------
    def setup(self, vp_w: int, vp_h: int) -> None:
        self._vp_w = vp_w
        self._vp_h = vp_h

        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)
        glEnable(GL_LIGHT0)
        glEnable(GL_COLOR_MATERIAL)
        glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
        glEnable(GL_NORMALIZE)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        glLightfv(GL_LIGHT0, GL_POSITION, [5.0, 10.0, 5.0, 1.0])
        glLightfv(GL_LIGHT0, GL_AMBIENT,  [0.3,  0.3,  0.3, 1.0])
        glLightfv(GL_LIGHT0, GL_DIFFUSE,  [0.9,  0.9,  0.9, 1.0])
        glLightfv(GL_LIGHT0, GL_SPECULAR, [0.4,  0.4,  0.4, 1.0])
        glClearColor(0.04, 0.04, 0.10, 1.0)

        # Build VBO
        vdata, idata = _build_cube_data()
        self._n_idx  = len(idata)

        self._vbo = glGenBuffers(1)
        glBindBuffer(GL_ARRAY_BUFFER, self._vbo)
        glBufferData(GL_ARRAY_BUFFER, vdata.nbytes, vdata, GL_STATIC_DRAW)

        self._ebo = glGenBuffers(1)
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, self._ebo)
        glBufferData(GL_ELEMENT_ARRAY_BUFFER, idata.nbytes, idata, GL_STATIC_DRAW)

        glBindBuffer(GL_ARRAY_BUFFER, 0)
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0)

        # Pre-allocate PIP texture
        self._pip_tex = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, self._pip_tex)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB,
                     self._pip_w, self._pip_h, 0,
                     GL_RGB, GL_UNSIGNED_BYTE,
                     np.zeros((self._pip_h, self._pip_w, 3), dtype=np.uint8))
        glBindTexture(GL_TEXTURE_2D, 0)

        # Pre-allocate overlay texture
        self._overlay_tex = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, self._overlay_tex)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA,
                     vp_w, vp_h, 0, GL_RGBA, GL_UNSIGNED_BYTE,
                     np.zeros((vp_h, vp_w, 4), dtype=np.uint8))
        glBindTexture(GL_TEXTURE_2D, 0)

        logger.info("GLRenderer VBO setup complete (%dx%d)", vp_w, vp_h)

    # ------------------------------------------------------------------
    def render(
        self,
        ghost:        Optional[tuple],
        erase_ghost:  Optional[tuple],
        selected_set: set,
        webcam_frame: Optional[np.ndarray],
        status:       str = "",
        pinky_progress: float = 0.0,
    ) -> None:
        glViewport(0, 0, self._vp_w, self._vp_h)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        # Projection
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(self.gl_cfg.fov,
                       self._vp_w / max(self._vp_h, 1),
                       self.gl_cfg.near_clip,
                       self.gl_cfg.far_clip)

        # Modelview
        glMatrixMode(GL_MODELVIEW)
        self.camera.apply()

        self._draw_grid()

        # Bind VBO once
        glBindBuffer(GL_ARRAY_BUFFER, self._vbo)
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, self._ebo)
        glEnableClientState(GL_VERTEX_ARRAY)
        glEnableClientState(GL_NORMAL_ARRAY)
        stride = 6 * 4   # 6 floats × 4 bytes
        glVertexPointer(3, GL_FLOAT, stride, ctypes.c_void_p(0))
        glNormalPointer(GL_FLOAT, stride, ctypes.c_void_p(12))

        # Draw all placed cubes
        for cube in self.world.cubes:
            color = _LAYER_COLORS[min(cube.gz, len(_LAYER_COLORS)-1)]
            is_sel = cube in selected_set
            self._draw_cube_vbo(cube.gx, cube.gy, cube.gz,
                                color, 1.0, is_sel)

        # Ghost
        if ghost:
            gx, gy, gz = ghost
            color = _LAYER_COLORS[min(gz, len(_LAYER_COLORS)-1)]
            self._draw_cube_vbo(gx, gy, gz, color, 0.35)

        # Erase ghost
        if erase_ghost:
            gx, gy, gz = erase_ghost
            self._draw_cube_vbo(gx, gy, gz, _ERASE_COLOR, 0.4)

        glDisableClientState(GL_VERTEX_ARRAY)
        glDisableClientState(GL_NORMAL_ARRAY)
        glBindBuffer(GL_ARRAY_BUFFER, 0)
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0)

        # PIP webcam
        if webcam_frame is not None:
            self._update_pip(webcam_frame)
            self._draw_pip()

        # 2D overlay
        self._draw_overlay(status, pinky_progress)

    # ------------------------------------------------------------------
    def _draw_cube_vbo(
        self,
        gx: int, gy: int, gz: int,
        color: tuple,
        alpha: float = 1.0,
        selected: bool = False,
    ) -> None:
        glPushMatrix()
        glTranslatef(float(gx), -float(gy), -float(gz))

        if alpha < 1.0:
            glDepthMask(GL_FALSE)

        glColor4f(color[0], color[1], color[2], alpha)
        glDrawElements(GL_TRIANGLES, self._n_idx, GL_UNSIGNED_INT,
                       ctypes.c_void_p(0))

        if alpha < 1.0:
            glDepthMask(GL_TRUE)

        if selected:
            glDisable(GL_LIGHTING)
            glColor4f(*_SELECTED_COLOR, 1.0)
            glLineWidth(2.0)
            glPolygonMode(GL_FRONT_AND_BACK, GL_LINE)
            glDrawElements(GL_TRIANGLES, self._n_idx, GL_UNSIGNED_INT,
                           ctypes.c_void_p(0))
            glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)
            glEnable(GL_LIGHTING)

        glPopMatrix()

    # ------------------------------------------------------------------
    def _draw_grid(self) -> None:
        glDisable(GL_LIGHTING)
        glColor4f(0.15, 0.15, 0.25, 1.0)
        glLineWidth(1.0)
        size = 16
        glBegin(GL_LINES)
        for i in range(-size, size + 1):
            glVertex3f(float(i),  0.5, float(-size))
            glVertex3f(float(i),  0.5, float(size))
            glVertex3f(float(-size), 0.5, float(i))
            glVertex3f(float(size),  0.5, float(i))
        glEnd()
        glEnable(GL_LIGHTING)

    # ------------------------------------------------------------------
    def _update_pip(self, frame_bgr: np.ndarray) -> None:
        """Upload webcam frame to PIP texture using glTexSubImage2D."""
        import cv2
        pip     = cv2.resize(frame_bgr, (self._pip_w, self._pip_h))
        pip     = cv2.flip(pip, 1)
        pip_rgb = cv2.cvtColor(pip, cv2.COLOR_BGR2RGB)
        pip_rgb = np.flipud(pip_rgb).copy()

        glBindTexture(GL_TEXTURE_2D, self._pip_tex)
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0,
                        self._pip_w, self._pip_h,
                        GL_RGB, GL_UNSIGNED_BYTE, pip_rgb)
        glBindTexture(GL_TEXTURE_2D, 0)

    # ------------------------------------------------------------------
    def _draw_pip(self) -> None:
        """Draw pre-uploaded PIP texture in bottom-right corner."""
        w, h = self._vp_w, self._vp_h
        x = w - self._pip_w - 10
        y = 10

        self._set_2d_mode()
        glDisable(GL_LIGHTING)
        glDisable(GL_DEPTH_TEST)
        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, self._pip_tex)
        glColor4f(1, 1, 1, 1)
        glBegin(GL_QUADS)
        glTexCoord2f(0,0); glVertex2f(x,              y)
        glTexCoord2f(1,0); glVertex2f(x+self._pip_w,  y)
        glTexCoord2f(1,1); glVertex2f(x+self._pip_w,  y+self._pip_h)
        glTexCoord2f(0,1); glVertex2f(x,              y+self._pip_h)
        glEnd()
        glDisable(GL_TEXTURE_2D)

        # Border
        glColor4f(0.0, 0.8, 0.8, 1.0)
        glLineWidth(2.0)
        glBegin(GL_LINE_LOOP)
        glVertex2f(x,              y)
        glVertex2f(x+self._pip_w,  y)
        glVertex2f(x+self._pip_w,  y+self._pip_h)
        glVertex2f(x,              y+self._pip_h)
        glEnd()

        self._restore_3d_mode()

    # ------------------------------------------------------------------
    def _draw_overlay(self, status: str, pinky_progress: float) -> None:
        """
        Draw status text overlay. Rebuilds texture only when status changes.
        """
        import cv2

        cache_key = f"{status}|{int(pinky_progress*20)}"
        if cache_key != self._overlay_status:
            self._overlay_status = cache_key
            img = np.zeros((self._vp_h, self._vp_w, 4), dtype=np.uint8)

            # Status badge
            cv2.rectangle(img, (10,10), (280,55), (20,20,20,200), -1)
            cv2.rectangle(img, (10,10), (280,55), (0,220,255,255), 1)
            cv2.putText(img, f"Builder: {status}", (18,40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,220,255,255), 2, cv2.LINE_AA)

            # Pinky progress
            if pinky_progress > 0:
                bw = int((self._vp_w - 20) * pinky_progress)
                cv2.rectangle(img, (10,62), (self._vp_w-10,74), (40,40,40,200), -1)
                cv2.rectangle(img, (10,62), (10+bw,74), (0,200,255,255), -1)

            # Legend
            legend = [
                "[1] Index only            ->  Ghost preview",
                "[2] Index+Middle drag     ->  Paint cubes",
                "[3] Thumb+Index touch     ->  Move cube/group",
                "[4] 3 fingertips          ->  Erase",
                "[5] Rock sign             ->  Undo",
                "[6] Left Fist+Right sweep ->  Rotate",
                "[7] Left Fist+Thumb+Index ->  Zoom",
                "[8] Pinky hold            ->  Switch mode",
            ]
            h = self._vp_h
            for i, line in enumerate(legend):
                cv2.putText(img, line,
                            (12, h - 15 - (len(legend)-1-i)*20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.38,
                            (160,160,160,200), 1, cv2.LINE_AA)

            img_flip = np.flipud(img).copy()
            glBindTexture(GL_TEXTURE_2D, self._overlay_tex)
            glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0,
                            self._vp_w, self._vp_h,
                            GL_RGBA, GL_UNSIGNED_BYTE, img_flip)
            glBindTexture(GL_TEXTURE_2D, 0)

        # Draw cached overlay
        self._set_2d_mode()
        glDisable(GL_LIGHTING)
        glDisable(GL_DEPTH_TEST)
        glEnable(GL_BLEND)
        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, self._overlay_tex)
        glColor4f(1,1,1,1)
        w, h = self._vp_w, self._vp_h
        glBegin(GL_QUADS)
        glTexCoord2f(0,0); glVertex2f(0,0)
        glTexCoord2f(1,0); glVertex2f(w,0)
        glTexCoord2f(1,1); glVertex2f(w,h)
        glTexCoord2f(0,1); glVertex2f(0,h)
        glEnd()
        glDisable(GL_TEXTURE_2D)
        self._restore_3d_mode()

    # ------------------------------------------------------------------
    def _set_2d_mode(self) -> None:
        glMatrixMode(GL_PROJECTION)
        glPushMatrix()
        glLoadIdentity()
        glOrtho(0, self._vp_w, 0, self._vp_h, -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glPushMatrix()
        glLoadIdentity()

    def _restore_3d_mode(self) -> None:
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)
        glMatrixMode(GL_PROJECTION)
        glPopMatrix()
        glMatrixMode(GL_MODELVIEW)
        glPopMatrix()
