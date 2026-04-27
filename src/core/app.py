"""
app.py — Main loop. Routes gesture state to Productivity or Builder mode.

Productivity: OpenCV window + mouse control
Builder:      GLFW + PyOpenGL 3D window
"""
from __future__ import annotations

import logging
import sys
import time
from typing import Optional

import cv2
import numpy as np

from src.core.config import CONFIG
from src.core.controller import MouseController
from src.core.modes import AppMode, BuilderController
from src.core.vision import Gesture, VisionProcessor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("gesture_control.app")

_HAND_CONNECTIONS = [
    (0,1),(1,2),(2,3),(3,4),
    (0,5),(5,6),(6,7),(7,8),
    (0,9),(9,10),(10,11),(11,12),
    (0,13),(13,14),(14,15),(15,16),
    (0,17),(17,18),(18,19),(19,20),
    (5,9),(9,13),(13,17),
]


# ---------------------------------------------------------------------------
# Landmark drawing
# ---------------------------------------------------------------------------

def _draw_landmarks(frame, lm_list, mirrored: bool):
    if not lm_list:
        return
    h, w = frame.shape[:2]
    pts = [
        (int((1.0 - lm_list[i].x) * w) if mirrored else int(lm_list[i].x * w),
         int(lm_list[i].y * h))
        for i in range(21)
    ]
    for a, b in _HAND_CONNECTIONS:
        cv2.line(frame, pts[a], pts[b], (0, 180, 120), 2)
    for pt in pts:
        cv2.circle(frame, pt, 5, (0, 255, 180), -1)


# ---------------------------------------------------------------------------
# Productivity overlay
# ---------------------------------------------------------------------------

def _draw_productivity_overlay(frame, vision, status, fps, cfg):
    oc = cfg.overlay
    h, w = frame.shape[:2]
    frame_disp = cv2.flip(frame, 1)

    _draw_landmarks(frame_disp, vision.last_landmarks, mirrored=True)

    is_active = status not in ("IDLE", "SCROLL READY")
    color = oc.color_active if is_active else oc.color_idle
    cv2.rectangle(frame_disp, (10, 10), (300, 55), (20, 20, 20), -1)
    cv2.rectangle(frame_disp, (10, 10), (300, 55), color, 1)
    cv2.putText(frame_disp, f"Gesture: {status}", (18, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.75, color, 2, cv2.LINE_AA)

    cv2.putText(frame_disp, f"{fps:.0f} FPS", (w - 85, 35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (120, 120, 120), 1, cv2.LINE_AA)

    # Mode badge
    cv2.rectangle(frame_disp, (w//2-130, 8), (w//2+130, 48), (20,20,20), -1)
    cv2.rectangle(frame_disp, (w//2-130, 8), (w//2+130, 48), (0,255,180), 1)
    cv2.putText(frame_disp, "PRODUCTIVITY MODE", (w//2-118, 34),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0,255,180), 2, cv2.LINE_AA)

    # Pinky progress
    pf = vision._pinky_hold_frames
    pr = vision._PINKY_HOLD_REQUIRED
    if pf > 0:
        bw = int((w - 20) * pf / pr)
        cv2.rectangle(frame_disp, (10, 54), (w-10, 66), (40,40,40), -1)
        cv2.rectangle(frame_disp, (10, 54), (10+bw, 66), (0,200,255), -1)

    legend = [
        "[1] Thumb extended          ->  Move cursor",
        "[2] Thumb + Index touch     ->  Left click",
        "[3] Thumb + Middle touch    ->  Right click",
        "[4] 3 fingertips raised     ->  Scroll",
        "[5] Pinky only, hold        ->  Switch to Builder",
    ]
    for i, line in enumerate(legend):
        cv2.putText(frame_disp, line,
                    (12, h - 15 - (len(legend) - 1 - i) * 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (150,150,150), 1, cv2.LINE_AA)

def _draw_builder_overlay(frame, vision, builder, status, fps, cfg):
    h, w = frame.shape[:2]
    frame_disp = cv2.flip(frame, 1)
    builder.render(frame_disp)
    _draw_landmarks(frame_disp, vision.last_landmarks, mirrored=True)
    cv2.rectangle(frame_disp, (10, 10), (300, 55), (20, 20, 20), -1)
    cv2.rectangle(frame_disp, (10, 10), (300, 55), (0, 200, 255), 1)
    cv2.putText(frame_disp, f"Builder: {status}", (18, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 200, 255), 2, cv2.LINE_AA)
    cv2.rectangle(frame_disp, (w//2-130, 8), (w//2+130, 48), (20,20,20), -1)
    cv2.rectangle(frame_disp, (w//2-130, 8), (w//2+130, 48), (255,100,50), 1)
    cv2.putText(frame_disp, "BUILDER MODE", (w//2-90, 34),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255,100,50), 2, cv2.LINE_AA)
    return frame_disp


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run() -> None:
    cfg = CONFIG
    cc  = cfg.camera
    oc  = cfg.overlay

    logger.info("Starting Gesture Control")

    cap = cv2.VideoCapture(cc.camera_index, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  cc.frame_width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cc.frame_height)
    cap.set(cv2.CAP_PROP_FPS, cc.fps)

    if not cap.isOpened():
        logger.error("Cannot open camera %d", cc.camera_index)
        sys.exit(1)

    vision   = VisionProcessor(cfg)
    mouse    = MouseController(cfg)
    builder  = BuilderController(cfg)
    mode     = AppMode.PRODUCTIVITY

    # GL window (lazy init when entering Builder mode)
    gl_win = None

    # Thumb pinch drag state (Builder)
    _thumb_pinch_start: Optional[tuple] = None
    _thumb_pinch_held:  bool = False

    prev_time = time.perf_counter()
    fps = 0.0

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                continue

            # --- Vision (pass builder flag for correct classifier) ---
            gs = vision.process_frame(frame, builder_mode=(mode == AppMode.BUILDER))

            if gs.mode_switch:
                mode = AppMode.BUILDER if mode == AppMode.PRODUCTIVITY else AppMode.PRODUCTIVITY
                logger.info("Switched to %s", mode.name)

            # --- FPS ---
            now = time.perf_counter()
            fps = 0.9 * fps + 0.1 * (1.0 / max(now - prev_time, 1e-6))
            prev_time = now

            # ---- Productivity Mode --------------------------------------
            if mode == AppMode.PRODUCTIVITY:
                status     = mouse.update(gs)
                frame_disp = _draw_productivity_overlay(frame, vision, status, fps, cfg)
                cv2.imshow(oc.window_title, frame_disp)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break

            # ---- Builder Mode -------------------------------------------
            else:
                nx, ny = gs.cursor_x, gs.cursor_y
                status = builder.update(gs.gesture.value, nx, ny, cc.frame_width, cc.frame_height, gs)
                frame_disp = _draw_builder_overlay(frame, vision, builder, status, fps, cfg)
                cv2.imshow(oc.window_title, frame_disp)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break

    except KeyboardInterrupt:
        logger.info("Interrupted.")
    finally:
        cap.release()
        cv2.destroyAllWindows()
        if gl_win:
            gl_win.close()
        vision.close()
        logger.info("Stopped.")


if __name__ == "__main__":
    run()
