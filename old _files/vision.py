"""
vision.py — MediaPipe hand tracking + gesture classification.

Cursor Mode gestures:
  THUMB_MOVE   : thumb extended, others relaxed  -> move cursor
  THUMB_CLICK  : thumb tip + index tip touch     -> left click
  THUMB_RCLICK : thumb tip + middle tip touch    -> right click
  SCROLL       : 3 fingertips raised             -> scroll
  MODE_SWITCH  : pinky only, hold                -> switch to Builder

Builder Mode gestures:
  POINTING     : index only                      -> ghost preview
  PINCH        : index + middle up + drag        -> paint cubes
  THUMB_PINCH  : thumb + index touch             -> move cube/group
  SCROLL       : 3 fingertips raised             -> erase cubes
  RIGHT_CLICK  : rock sign (index+pinky)         -> undo
  MODE_SWITCH  : pinky only, hold                -> switch to Cursor
"""
from __future__ import annotations

import logging
import math
import os
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_MODEL_URL  = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
)
_MODEL_DIR  = os.path.join(os.path.dirname(__file__), "models")
_MODEL_PATH = os.path.join(_MODEL_DIR, "hand_landmarker.task")


def _ensure_model() -> str:
    if os.path.exists(_MODEL_PATH):
        return _MODEL_PATH
    os.makedirs(_MODEL_DIR, exist_ok=True)
    logger.info("Downloading hand landmarker model (~26MB)...")
    urllib.request.urlretrieve(_MODEL_URL, _MODEL_PATH)
    logger.info("Model saved to %s", _MODEL_PATH)
    return _MODEL_PATH


# ---------------------------------------------------------------------------
# Enums & State
# ---------------------------------------------------------------------------

class Gesture(str, Enum):
    IDLE         = "IDLE"
    # Cursor mode
    INDEX_MOVE   = "INDEX_MOVE"    # index only           -> move cursor
    LEFT_CLICK   = "LEFT_CLICK"    # index + middle tap   -> left click
    RIGHT_CLICK  = "RIGHT_CLICK"   # rock sign            -> right click
    SCROLL       = "SCROLL"        # 3 fingertips         -> scroll
    # Builder mode (shared)
    POINTING     = "POINTING"      # index only           -> ghost preview
    PINCH        = "PINCH"         # index + middle drag  -> paint
    THUMB_PINCH  = "THUMB_PINCH"   # thumb + index        -> move cube
    MODE_SWITCH  = "MODE_SWITCH"   # pinky hold           -> switch mode


@dataclass
class GestureState:
    gesture:            Gesture = Gesture.IDLE
    cursor_x:           float   = 0.5
    cursor_y:           float   = 0.5
    scroll_dy:          float   = 0.0
    pinch_active:       bool    = False   # index+middle (paint in Builder)
    thumb_pinch_active: bool    = False   # thumb+index  (move in Builder)
    click_active:       bool    = False   # index+middle held (drag in Cursor)
    mode_switch:        bool    = False


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _dist(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)

def _finger_extended(lm, tip: int, pip: int) -> bool:
    return lm[tip].y < lm[pip].y

def _fingertip_raised(lm, tip: int, mcp: int, threshold: float = 0.03) -> bool:
    return (lm[mcp].y - lm[tip].y) > threshold


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

class GestureClassifier:
    def __init__(self, cfg) -> None:
        self.gc = cfg.gesture

    def classify_cursor(self, lm) -> tuple[Gesture, float, float]:
        index_ext  = _finger_extended(lm, 8,  6)
        middle_ext = _finger_extended(lm, 12, 10)
        ring_ext   = _finger_extended(lm, 16, 14)
        pinky_ext  = _finger_extended(lm, 20, 18)
        cx, cy     = lm[8].x, lm[8].y

        # P0: mode switch — pinky only hold
        if pinky_ext and not index_ext and not middle_ext and not ring_ext:
            return Gesture.MODE_SWITCH, cx, cy

        # P1: right click — rock sign (index + pinky, middle + ring down)
        if index_ext and pinky_ext and not middle_ext and not ring_ext:
            return Gesture.RIGHT_CLICK, cx, cy

        # P2: scroll — 3 fingertips raised (must be before left click check)
        if (_fingertip_raised(lm,8,5) and _fingertip_raised(lm,12,9)
                and _fingertip_raised(lm,16,13) and not pinky_ext):
            return Gesture.SCROLL, cx, (lm[8].y+lm[12].y+lm[16].y)/3.0

        # P3: left click / drag — index + middle up, ring + pinky down
        if index_ext and middle_ext and not ring_ext and not pinky_ext:
            return Gesture.LEFT_CLICK, cx, cy

        # P4: move cursor — index only
        if index_ext and not middle_ext and not ring_ext and not pinky_ext:
            return Gesture.INDEX_MOVE, cx, cy

        return Gesture.IDLE, cx, cy

    def classify_builder(self, lm) -> tuple[Gesture, float, float]:
        gc = self.gc
        index_ext  = _finger_extended(lm, 8,  6)
        middle_ext = _finger_extended(lm, 12, 10)
        ring_ext   = _finger_extended(lm, 16, 14)
        pinky_ext  = _finger_extended(lm, 20, 18)
        cx, cy     = lm[8].x, lm[8].y

        # P0: mode switch
        if pinky_ext and not index_ext and not middle_ext and not ring_ext:
            return Gesture.MODE_SWITCH, cx, cy

        # P1: thumb+index touch = move cube
        if _dist(lm[4], lm[8]) < gc.pinch_threshold:
            return Gesture.THUMB_PINCH, cx, cy

        # P2: erase — 3 fingertips raised
        if (_fingertip_raised(lm,8,5) and _fingertip_raised(lm,12,9)
                and _fingertip_raised(lm,16,13) and not pinky_ext):
            return Gesture.SCROLL, cx, (lm[8].y+lm[12].y+lm[16].y)/3.0

        # P3: paint — index + middle up, ring + pinky down
        if index_ext and middle_ext and not ring_ext and not pinky_ext:
            return Gesture.PINCH, cx, cy

        # P4: pointing — index only
        if index_ext and not middle_ext and not ring_ext and not pinky_ext:
            return Gesture.POINTING, cx, cy

        # P5: undo — rock sign
        if index_ext and pinky_ext and not middle_ext and not ring_ext:
            return Gesture.RIGHT_CLICK, cx, cy

        return Gesture.IDLE, cx, cy


# ---------------------------------------------------------------------------
# Vision Processor
# ---------------------------------------------------------------------------

class VisionProcessor:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._classifier = GestureClassifier(cfg)
        self._prev_scroll_y: Optional[float] = None
        self.last_landmarks = None
        self.mirrored: bool = True

        self._pinky_hold_frames:   int = 0
        self._PINKY_HOLD_REQUIRED: int = cfg.gesture.pinky_hold_required

        # Try Modal first, fall back to local
        self._modal_client = None
        try:
            from modal_vision import get_modal_client
            self._modal_client = get_modal_client()
        except Exception:
            pass

        if self._modal_client:
            logger.info("VisionProcessor: using Modal cloud inference.")
            self._landmarker = self._mp_image_cls = self._mp_format = None
        else:
            import mediapipe as mp
            opts = mp.tasks.vision.HandLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=_ensure_model()),
                running_mode=mp.tasks.vision.RunningMode.IMAGE,
                num_hands=1,
                min_hand_detection_confidence=0.6,
                min_hand_presence_confidence=0.6,
                min_tracking_confidence=0.5,
            )
            self._landmarker   = mp.tasks.vision.HandLandmarker.create_from_options(opts)
            self._mp_image_cls = mp.Image
            self._mp_format    = mp.ImageFormat.SRGB
            logger.info("VisionProcessor: using local MediaPipe inference.")

    def process_frame(self, frame_bgr: np.ndarray, builder_mode: bool = False) -> GestureState:
        import cv2
        self.last_landmarks = None
        state = GestureState()

        # Get landmarks
        if self._modal_client:
            raw = self._modal_client.detect(frame_bgr)
            if not raw["hands"]:
                self._pinky_hold_frames = 0
                self._prev_scroll_y = None
                return state
            lm = self._parse_modal_lm(raw["hands"][0]["landmarks"])
        else:
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            frame_rgb.flags.writeable = False
            result = self._landmarker.detect(
                self._mp_image_cls(image_format=self._mp_format, data=frame_rgb)
            )
            if not result.hand_landmarks:
                self._pinky_hold_frames = 0
                self._prev_scroll_y = None
                return state
            lm = result.hand_landmarks[0]

        self.last_landmarks = lm

        # Classify
        if builder_mode:
            gesture, raw_x, raw_y = self._classifier.classify_builder(lm)
        else:
            gesture, raw_x, raw_y = self._classifier.classify_cursor(lm)

        # Mode switch hold logic
        if gesture == Gesture.MODE_SWITCH:
            self._pinky_hold_frames += 1
            if self._pinky_hold_frames == self._PINKY_HOLD_REQUIRED:
                state.mode_switch = True
                self._pinky_hold_frames = 0
        else:
            self._pinky_hold_frames = 0

        # Mirror X for front camera
        cursor_x = 1.0 - raw_x

        state.gesture            = gesture
        state.cursor_x           = cursor_x
        state.cursor_y           = raw_y
        state.pinch_active       = gesture == Gesture.PINCH
        state.thumb_pinch_active = gesture == Gesture.THUMB_PINCH
        state.click_active       = gesture == Gesture.LEFT_CLICK

        if gesture == Gesture.SCROLL:
            if self._prev_scroll_y is not None:
                state.scroll_dy = self._prev_scroll_y - raw_y
            self._prev_scroll_y = raw_y
        else:
            self._prev_scroll_y = None

        return state

    def _parse_modal_lm(self, raw_lms: list):
        class _LM:
            def __init__(self, d): self.x=d["x"]; self.y=d["y"]; self.z=d["z"]
        class _Hand:
            def __init__(self, ls): self._l=[_LM(l) for l in ls]
            def __getitem__(self,i): return self._l[i]
            def __len__(self): return len(self._l)
        return _Hand(raw_lms)

    def close(self) -> None:
        if self._landmarker:
            self._landmarker.close()
        logger.info("VisionProcessor closed.")
