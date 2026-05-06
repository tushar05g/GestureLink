"""
app.py — Main loop. Routes gestures to Cursor or Builder mode.
"""
from __future__ import annotations
import logging, sys, time
from typing import Optional
import cv2
from config import CONFIG
from controller import MouseController
from modes import AppMode, BuilderController
from vision import Gesture, VisionProcessor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("gesture_control.app")

_HAND_CONNECTIONS = [
    (0,1),(1,2),(2,3),(3,4),(0,5),(5,6),(6,7),(7,8),
    (0,9),(9,10),(10,11),(11,12),(0,13),(13,14),(14,15),(15,16),
    (0,17),(17,18),(18,19),(19,20),(5,9),(9,13),(13,17),
]


def _draw_landmarks(frame, lm):
    if not lm: return
    h, w = frame.shape[:2]
    pts = [(int((1.0-lm[i].x)*w), int(lm[i].y*h)) for i in range(21)]
    for a,b in _HAND_CONNECTIONS:
        cv2.line(frame, pts[a], pts[b], (0,180,120), 2)
    for pt in pts:
        cv2.circle(frame, pt, 5, (0,255,180), -1)


def _draw_cursor_overlay(frame, vision, status, fps, pinky_frames, pinky_req):
    h, w = frame.shape[:2]
    frame = cv2.flip(frame, 1)
    _draw_landmarks(frame, vision.last_landmarks)

    # Mode badge
    cv2.rectangle(frame, (w//2-130,8), (w//2+130,48), (20,20,20), -1)
    cv2.rectangle(frame, (w//2-130,8), (w//2+130,48), (0,255,180), 1)
    cv2.putText(frame, "CURSOR MODE", (w//2-95,34),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,255,180), 2, cv2.LINE_AA)

    # Status
    is_active = status not in ("IDLE","SCROLL READY")
    col = (0,255,180) if is_active else (200,200,200)
    cv2.rectangle(frame, (10,10), (280,55), (20,20,20), -1)
    cv2.rectangle(frame, (10,10), (280,55), col, 1)
    cv2.putText(frame, f"Gesture: {status}", (18,40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.75, col, 2, cv2.LINE_AA)

    # FPS
    cv2.putText(frame, f"{fps:.0f} FPS", (w-85,35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (120,120,120), 1, cv2.LINE_AA)

    # Pinky progress bar
    if pinky_frames > 0:
        bw = int((w-20) * pinky_frames/pinky_req)
        cv2.rectangle(frame, (10,54), (w-10,66), (40,40,40), -1)
        cv2.rectangle(frame, (10,54), (10+bw,66), (0,200,255), -1)
        cv2.putText(frame, "Hold pinky to switch to Builder...",
                    (12,82), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0,200,255), 1, cv2.LINE_AA)

    legend = [
        "[1] Index only           ->  Move cursor",
        "[2] Index + Middle (tap) ->  Left click",
        "[3] Index + Middle (hold)->  Drag",
        "[4] 3 fingertips raised  ->  Scroll",
        "[5] Rock sign            ->  Right click",
        "[6] Pinky only, hold     ->  Switch to Builder",
    ]
    for i, line in enumerate(legend):
        cv2.putText(frame, line, (12, h-15-(len(legend)-1-i)*20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (150,150,150), 1, cv2.LINE_AA)
    return frame


def _draw_builder_overlay(frame, vision, status, fps, pinky_frames, pinky_req):
    h, w = frame.shape[:2]
    # frame already flipped + cubes drawn by renderer
    _draw_landmarks(frame, vision.last_landmarks)

    # Mode badge
    cv2.rectangle(frame, (w//2-130,8), (w//2+130,48), (20,20,20), -1)
    cv2.rectangle(frame, (w//2-130,8), (w//2+130,48), (0,220,255), 1)
    cv2.putText(frame, "BUILDER MODE", (w//2-105,34),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,220,255), 2, cv2.LINE_AA)

    # Status
    cv2.rectangle(frame, (10,10), (280,55), (20,20,20), -1)
    cv2.rectangle(frame, (10,10), (280,55), (0,220,255), 1)
    cv2.putText(frame, f"Builder: {status}", (18,40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0,220,255), 2, cv2.LINE_AA)

    cv2.putText(frame, f"{fps:.0f} FPS", (w-85,35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (120,120,120), 1, cv2.LINE_AA)

    if pinky_frames > 0:
        bw = int((w-20)*pinky_frames/pinky_req)
        cv2.rectangle(frame, (10,54), (w-10,66), (40,40,40), -1)
        cv2.rectangle(frame, (10,54), (10+bw,66), (0,200,255), -1)
        cv2.putText(frame, "Hold pinky to switch to Cursor...",
                    (12,82), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0,200,255), 1, cv2.LINE_AA)

    legend = [
        "[1] Index only              ->  Ghost preview",
        "[2] Index+Middle + drag     ->  Paint cubes",
        "[3] Thumb+Index touch       ->  Move cube/group",
        "[4] 3 fingertips            ->  Erase cubes",
        "[5] Rock sign               ->  Undo",
        "[6] Point at toggle button  ->  Single/Group mode",
        "[7] Pinky hold              ->  Switch to Cursor",
    ]
    for i, line in enumerate(legend):
        cv2.putText(frame, line, (12, h-15-(len(legend)-1-i)*20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (150,150,150), 1, cv2.LINE_AA)
    return frame


def run():
    cfg = CONFIG
    cc, oc = cfg.camera, cfg.overlay
    logger.info("Starting Gesture Control")

    cap = cv2.VideoCapture(cc.camera_index, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  cc.frame_width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cc.frame_height)
    cap.set(cv2.CAP_PROP_FPS, cc.fps)
    if not cap.isOpened():
        logger.error("Cannot open camera %d", cc.camera_index)
        sys.exit(1)

    vision  = VisionProcessor(cfg)
    mouse   = MouseController(cfg)
    builder = BuilderController(cfg)
    mode    = AppMode.CURSOR

    _thumb_pinch_start: Optional[tuple] = None
    _thumb_pinch_held:  bool = False

    prev_time = time.perf_counter()
    fps = 0.0

    try:
        while True:
            ret, frame = cap.read()
            if not ret: continue

            gs = vision.process_frame(frame, builder_mode=(mode==AppMode.BUILDER))

            # Mode switch
            if gs.mode_switch:
                mode = AppMode.BUILDER if mode==AppMode.CURSOR else AppMode.CURSOR
                logger.info("Switched to %s", mode.name)
                _thumb_pinch_start = None
                _thumb_pinch_held  = False

            now = time.perf_counter()
            fps = 0.9*fps + 0.1*(1.0/max(now-prev_time,1e-6))
            prev_time = now

            pf = vision._pinky_hold_frames
            pr = vision._PINKY_HOLD_REQUIRED

            # ---- CURSOR MODE ----------------------------------------
            if mode == AppMode.CURSOR:
                status     = mouse.update(gs)
                frame_disp = _draw_cursor_overlay(frame, vision, status, fps, pf, pr)

            # ---- BUILDER MODE ---------------------------------------
            else:
                fw, fh = cc.frame_width, cc.frame_height
                # Flip first so cubes + landmarks are on same mirrored frame
                frame_disp = cv2.flip(frame, 1)
                nx, ny = gs.cursor_x, gs.cursor_y

                if gs.thumb_pinch_active:
                    if not _thumb_pinch_held:
                        _thumb_pinch_start = (nx, ny)
                        _thumb_pinch_held  = True
                    status = builder.handle_thumb_pinch_drag(
                        nx, ny, fw, fh, frame_disp,
                        drag_start_norm=_thumb_pinch_start,
                        is_dragging=True,
                    )
                else:
                    if _thumb_pinch_held:
                        builder.handle_thumb_pinch_drag(
                            nx, ny, fw, fh, frame_disp,
                            drag_start_norm=_thumb_pinch_start,
                            is_dragging=False,
                        )
                    _thumb_pinch_held  = False
                    _thumb_pinch_start = None
                    status = builder.update(
                        gs.gesture.value, nx, ny, fw, fh, frame_disp
                    )

                frame_disp = _draw_builder_overlay(
                    frame_disp, vision, status, fps, pf, pr
                )

            cv2.imshow(oc.window_title, frame_disp)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                break

    except KeyboardInterrupt:
        logger.info("Interrupted.")
    finally:
        cap.release()
        cv2.destroyAllWindows()
        vision.close()
        logger.info("Stopped.")


if __name__ == "__main__":
    run()
