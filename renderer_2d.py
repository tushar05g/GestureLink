import cv2
import numpy as np
from world import CubeWorld

class IsometricRenderer2D:
    def __init__(self, size=40, offset=(400, 300)):
        self.size = size
        self.offset_x, self.offset_y = offset
        self.colors = {
            "top": (200, 200, 255),    # Light Blue
            "left": (100, 100, 180),   # Medium Blue
            "right": (150, 150, 220),  # Lighter Blue
            "border": (255, 255, 255)  # White
        }

    def project(self, gx, gy, gz):
        sx = self.offset_x + (gx - gy) * (self.size * 0.866)
        sy = self.offset_y + (gx + gy) * (self.size * 0.5) - (gz * self.size)
        return int(sx), int(sy)

    def draw_cube(self, frame, gx, gy, gz, alpha=0.7):
        p1 = self.project(gx, gy, gz + 1)
        p2 = self.project(gx + 1, gy, gz + 1)
        p3 = self.project(gx + 1, gy + 1, gz + 1)
        p4 = self.project(gx, gy + 1, gz + 1)
        p5 = self.project(gx + 1, gy + 1, gz)
        p6 = self.project(gx + 1, gy, gz)
        p7 = self.project(gx, gy + 1, gz)

        overlay = frame.copy()
        pts_top = np.array([p1, p2, p3, p4], np.int32)
        cv2.fillPoly(overlay, [pts_top], self.colors["top"])
        pts_right = np.array([p2, p6, p5, p3], np.int32)
        cv2.fillPoly(overlay, [pts_right], self.colors["right"])
        pts_left = np.array([p4, p3, p5, p7], np.int32)
        cv2.fillPoly(overlay, [pts_left], self.colors["left"])

        cv2.polylines(overlay, [pts_top], True, self.colors["border"], 1)
        cv2.polylines(overlay, [pts_right], True, self.colors["border"], 1)
        cv2.polylines(overlay, [pts_left], True, self.colors["border"], 1)
        cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)

    def render(self, frame, world: CubeWorld, ghost=None):
        sorted_cubes = sorted(world.cubes, key=lambda c: (c.gz, c.gx + c.gy))
        for cube in sorted_cubes:
            self.draw_cube(frame, cube.gx, cube.gy, cube.gz)
        if ghost:
            gx, gy, gz = ghost
            self.draw_cube(frame, gx, gy, gz, alpha=0.4)
