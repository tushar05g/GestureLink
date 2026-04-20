"""
vision.py — MediaPipe hand tracking, gesture classification, two-hand support.

Gestures
--------
Cursor Mode (single hand):
  THUMB_MOVE    : Thumb extended, others down       -> move cursor
  THUMB_CLICK   : Thumb tip + Index tip touch       -> left click
  THUMB_RCLICK  : Thumb tip + Middle tip touch      -> right click
  SCROLL        : 3 fingertips raised               -> scroll
  MODE_SWITCH   : Pinky only, hold                  -> switch mode

Builder Mode (single hand):
  POINTING      : Index only                        -> ghost preview
  PINCH         : Index + Middle up + drag          -> paint cubes
  THUMB_PINCH   : Thumb + Index touch               -> move cube/group
  SCROLL        : 3 fingertips                      -> erase
  RIGHT_CLICK   : Rock sign                         -> undo
  MODE_SWITCH   : Pinky hold                        -> switch mode

Builder Mode (two hands):
  Left fist + Right index sweep  -> rotate
  Left fist + Right thumb+index close -> zoom out
  Left fist + Right thumb+index open  -> zoom in
"""
from __future__ import annotations

import logging
import math
import os
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model download
# ---------------------------------------------------------------------------
_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
)
_MODEL_DIR  = os.path.join(os.path.dirname(__file__), "models")
_MODEL_PATH = os.path.join(_MODEL_DIR, "hand_landmarker.task")


def _ensure_model() -> str:
    if os.path.exists(_MODEL_PATH):
        return _MODEL_PATH
    os.makedirs(_MODEL_DIR, exist_ok=True)
    logger.info("Downloading hand landmarker model (~26MB) — one-time setup...")
    urllib.request.urlretrieve(_MODEL_URL, _MODEL_PATH)
    logger.info("Model saved to %s", _MODEL_PATH)
    return _MODEL_PATH


# ---------------------------------------------------------------------------
# Gesture enum
# ---------------------------------------------------------------------------
class Gesture(str, Enum):
    IDLE          = "IDLE"
    # Cursor mode
    THUMB_MOVE    = "THUMB_MOVE"    # thumb extended → move cursor
    THUMB_CLICK   = "THUMB_CLICK"   # thumb + index touch → left click
    THUMB_RCLICK  = "THUMB_RCLICK"  # thumb + middle touch → right click
    # Shared / Builder
    POINTING      = "POINTING"      # index only → ghost preview
    PINCH         = "PINCH"         # index + middle → paint
    THUMB_PINCH   = "THUMB_PINCH"   # thumb + index touch → move cube
    RIGHT_CLICK   = "RIGHT_CLICK"   # rock sign → undo in builder / right click productivity
    SCROLL        = "SCROLL"        # 3 fingertips
    MODE_SWITCH   = "MODE_SWITCH"   # pinky hold


# ---------------------------------------------------------------------------
# Gesture + Two-hand State
# ---------------------------------------------------------------------------
@dataclass
class GestureState:
    # Primary (right) hand
    gesture:            Gesture = Gesture.IDLE
    cursor_x:           float   = 0.5
    cursor_y:           float   = 0.5
    scroll_dy:          float   = 0.0
    pinch_active:       bool    = False   # index+middle (paint)
    thumb_pinch_active: bool    = False   # thumb+index (move)
    mode_switch:        bool    = False

    # Two-hand state (Builder only)
    left_fist:          bool    = False   # left hand making fist
    right_thumb_index_dist: float = 0.2  # normalised dist for zoom
    right_index_pos:    tuple[float,float] = field(default_factory=lambda: (0.5, 0.5))


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _dist(a, b) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _finger_extended(lm, tip: int, pip: int) -> bool:
    return lm[tip].y < lm[pip].y


def _fingertip_raised(lm, tip: int, mcp: int, threshold: float = 0.03) -> bool:
    return (lm[mcp].y - lm[tip].y) > threshold


def _is_fist(lm, threshold: float = 0.06) -> bool:
    """
    Detect fist: all 4 fingertips are close to the palm centre.
    Palm centre approximated as average of MCP joints.
    """
    palm_x = (lm[5].x + lm[9].x + lm[13].x + lm[17].x) / 4
    palm_y = (lm[5].y + lm[9].y + lm[13].y + lm[17].y) / 4

    class _P:
        def __init__(self, x, y): self.x = x; self.y = y

    palm = _P(palm_x, palm_y)
    tips = [lm[8], lm[12], lm[16], lm[20]]
    avg_dist = sum(_dist(t, palm) for t in tips) / 4
    return avg_dist < threshold


# ---------------------------------------------------------------------------
# Gesture Classifier  (stateless per-frame, mode-aware)
# ---------------------------------------------------------------------------

class GestureClassifier:
    def __init__(self, cfg) -> None:
        self.gc = cfg.gesture

    def classify_cursor(self, lm) -> tuple[Gesture, float, float]:
        """Classify gestures for Cursor (Productivity) mode."""
        gc = self.gc

        index_ext  = _finger_extended(lm, 8,  6)
        middle_ext = _finger_extended(lm, 12, 10)
        ring_ext   = _finger_extended(lm, 16, 14)
        pinky_ext  = _finger_extended(lm, 20, 18)

        thumb_index_dist  = _dist(lm[4], lm[8])
        thumb_middle_dist = _dist(lm[4], lm[12])

        # Thumb tip position as cursor anchor
        cx = lm[4].x
        cy = lm[4].y

        # Priority 0: Mode switch — pinky only hold
        if pinky_ext and not index_ext and not middle_ext and not ring_ext:
            return Gesture.MODE_SWITCH, cx, cy

        # Priority 1: Thumb + Index touch → left click
        if thumb_index_dist < gc.thumb_index_click:
            return Gesture.THUMB_CLICK, cx, cy

        # Priority 2: Thumb + Middle touch → right click
        if thumb_middle_dist < gc.thumb_middle_click:
            return Gesture.THUMB_RCLICK, cx, cy

        # Priority 3: Scroll — 3 fingertips raised
        index_tip_up  = _fingertip_raised(lm, 8,  5)
        middle_tip_up = _fingertip_raised(lm, 12, 9)
        ring_tip_up   = _fingertip_raised(lm, 16, 13)
        if index_tip_up and middle_tip_up and ring_tip_up and not pinky_ext:
            scroll_y = (lm[8].y + lm[12].y + lm[16].y) / 3.0
            return Gesture.SCROLL, cx, scroll_y

        # Priority 4: Thumb move — thumb extended, others relaxed
        thumb_mcp_dist = _dist(lm[4], lm[5])
        if thumb_mcp_dist > gc.thumb_extend_threshold and not index_ext:
            return Gesture.THUMB_MOVE, lm[4].x, lm[4].y

        return Gesture.IDLE, cx, cy

    def classify_builder(self, lm) -> tuple[Gesture, float, float]:
        """Classify gestures for Builder mode (single hand)."""
        gc = self.gc

        index_ext  = _finger_extended(lm, 8,  6)
        middle_ext = _finger_extended(lm, 12, 10)
        ring_ext   = _finger_extended(lm, 16, 14)
        pinky_ext  = _finger_extended(lm, 20, 18)

        index_tip_up  = _fingertip_raised(lm, 8,  5)
        middle_tip_up = _fingertip_raised(lm, 12, 9)
        ring_tip_up   = _fingertip_raised(lm, 16, 13)

        thumb_index_dist = _dist(lm[4], lm[8])

        cx = lm[8].x
        cy = lm[8].y

        # Priority 0: Mode switch — pinky only
        if pinky_ext and not index_ext and not middle_ext and not ring_ext:
            return Gesture.MODE_SWITCH, cx, cy

        # Priority 1: Thumb pinch (move cube) — thumb + index touch
        if thumb_index_dist < gc.pinch_threshold:
            return Gesture.THUMB_PINCH, cx, cy

        # Priority 2: Scroll/Erase — 3 fingertips raised
        if index_tip_up and middle_tip_up and ring_tip_up and not pinky_ext:
            scroll_y = (lm[8].y + lm[12].y + lm[16].y) / 3.0
            return Gesture.SCROLL, cx, scroll_y

        # Priority 3: Paint — index + middle up, ring + pinky down
        if index_ext and middle_ext and not ring_ext and not pinky_ext:
            return Gesture.PINCH, cx, cy

        # Priority 4: Pointing — index only
        if index_ext and not middle_ext and not ring_ext and not pinky_ext:
            return Gesture.POINTING, cx, cy

        # Priority 5: Undo — rock sign
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
        self.last_landmarks       = None   # right hand landmarks
        self.last_left_landmarks  = None   # left hand landmarks
        self.mirrored: bool = True

        # Pinky hold counter
        self._pinky_hold_frames: int = 0
        self._PINKY_HOLD_REQUIRED: int = 20

        # Click cooldowns
        self._left_click_cooldown:  int = 0
        self._right_click_cooldown: int = 0

        import mediapipe as mp
        model_path = _ensure_model()

        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=model_path),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            num_hands=2,                       # detect up to 2 hands
            min_hand_detection_confidence=0.6,
            min_hand_presence_confidence=0.6,
            min_tracking_confidence=0.5,
        )

        self._landmarker   = mp.tasks.vision.HandLandmarker.create_from_options(options)
        self._mp_image_cls = mp.Image
        self._mp_format    = mp.ImageFormat.SRGB

        logger.info("VisionProcessor ready (2-hand mode).")

    # ------------------------------------------------------------------
    def process_frame(self, frame_bgr: np.ndarray, builder_mode: bool = False) -> GestureState:
        import cv2

        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        frame_rgb.flags.writeable = False
        mp_image  = self._mp_image_cls(image_format=self._mp_format, data=frame_rgb)
        result    = self._landmarker.detect(mp_image)

        self.last_landmarks      = None
        self.last_left_landmarks = None
        state = GestureState()

        if not result.multi_hand_landmarks if hasattr(result, 'multi_hand_landmarks') else not result.hand_landmarks:
            self._pinky_hold_frames = 0
            self._prev_scroll_y     = None
            return state

        # --- Sort hands into left/right using handedness ---
        hands = result.hand_landmarks
        handedness = result.handedness if hasattr(result, 'handedness') else []

        right_lm = None
        left_lm  = None

        for i, hand in enumerate(hands):
            if i < len(handedness):
                label = handedness[i][0].category_name  # "Left" or "Right"
                # MediaPipe labels are mirrored for front camera
                if label == "Left":
                    right_lm = hand   # front cam: "Left" = user's right
                else:
                    left_lm  = hand   # front cam: "Right" = user's left
            else:
                # Fallback: first hand = right
                if right_lm is None:
                    right_lm = hand
                else:
                    left_lm = hand

        self.last_landmarks      = right_lm
        self.last_left_landmarks = left_lm

        # --- Left fist detection ---
        left_fist = False
        if left_lm:
            left_fist = _is_fist(left_lm, self.cfg.opengl.fist_threshold)
            state.left_fist = left_fist

        # --- Right hand two-hand state ---
        if right_lm:
            state.right_thumb_index_dist = _dist(right_lm[4], right_lm[8])
            state.right_index_pos = (right_lm[8].x, right_lm[8].y)

        # --- Classify right hand gesture ---
        if right_lm is None:
            self._pinky_hold_frames = 0
            self._prev_scroll_y     = None
            return state

        lm = right_lm

        if builder_mode:
            gesture, raw_x, raw_y = self._classifier.classify_builder(lm)
        else:
            gesture, raw_x, raw_y = self._classifier.classify_cursor(lm)

        # --- Mode switch (pinky hold) ---
        if gesture == Gesture.MODE_SWITCH:
            self._pinky_hold_frames += 1
            if self._pinky_hold_frames == self._PINKY_HOLD_REQUIRED:
                state.mode_switch = True
                self._pinky_hold_frames = 0
        else:
            self._pinky_hold_frames = 0

        # --- Always mirror X (front camera) ---
        cursor_x = 1.0 - raw_x

        state.gesture            = gesture
        state.cursor_x           = cursor_x
        state.cursor_y           = raw_y
        state.pinch_active       = gesture == Gesture.PINCH
        state.thumb_pinch_active = gesture == Gesture.THUMB_PINCH

        # --- Scroll delta ---
        if gesture == Gesture.SCROLL:
            if self._prev_scroll_y is not None:
                state.scroll_dy = self._prev_scroll_y - raw_y
            self._prev_scroll_y = raw_y
        else:
            self._prev_scroll_y = None

        return state

    def close(self) -> None:
        self._landmarker.close()
        logger.info("VisionProcessor closed.")