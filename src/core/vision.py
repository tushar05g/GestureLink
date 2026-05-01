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

import cv2
import numpy as np

from src.core.utils import resource_path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model download
# ---------------------------------------------------------------------------
_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
)

# Use resource_path so the model is found correctly inside a PyInstaller bundle
_MODEL_PATH = resource_path("src/core/models/hand_landmarker.task")
_MODEL_DIR  = _MODEL_PATH.parent



def _ensure_model() -> str:
    if _MODEL_PATH.exists():
        return str(_MODEL_PATH)
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading hand landmarker model (~26MB) — one-time setup...")
    urllib.request.urlretrieve(_MODEL_URL, str(_MODEL_PATH))
    logger.info("Model saved to %s", _MODEL_PATH)
    return str(_MODEL_PATH)


# ---------------------------------------------------------------------------
# Gesture enum
# ---------------------------------------------------------------------------
class Gesture(str, Enum):
    IDLE          = "IDLE"
    # Cursor mode
    THUMB_MOVE    = "THUMB_MOVE"    # thumb extended → move cursor
    THUMB_CLICK   = "THUMB_CLICK"   # thumb + index touch → left click
    THUMB_RCLICK  = "THUMB_RCLICK"  # thumb + middle touch → right click
    ONE_FINGER    = "ONE_FINGER"    # index finger up
    TWO_FINGERS   = "TWO_FINGERS"   # index + middle up
    THREE_FINGERS = "THREE_FINGERS" # index + middle + ring up
    FOUR_FINGERS  = "FOUR_FINGERS"  # index + middle + ring + pinky up
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
    finger_count:       int = 0
    landmarks:          list[dict[str, float]] | None = None


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
        self.last_gesture = Gesture.IDLE
        self.gesture_count = 0
        self.MIN_FRAMES = 2 # minimum frames to hold a gesture
        
        # Velocity Tracking
        self._prev_dist_ti = 1.0
        self._prev_dist_tm = 1.0
        self._click_cooldown = 0

    def _get_palm_size(self, lm) -> float:
        # Distance between wrist (0) and middle finger MCP (9)
        return _dist(lm[0], lm[9])

    def classify_cursor(self, lm) -> tuple[Gesture, float, float]:
        """Classify gestures for Cursor (Productivity) mode."""
        gc = self.gc
        palm_size = self._get_palm_size(lm)
        if palm_size < 0.01: palm_size = 0.1 # avoid div zero

        index_ext  = _finger_extended(lm, 8,  6)
        middle_ext = _finger_extended(lm, 12, 10)
        ring_ext   = _finger_extended(lm, 16, 14)
        pinky_ext  = _finger_extended(lm, 20, 18)

        # Normalize thresholds by palm size
        click_thresh  = gc.thumb_index_click * (palm_size / 0.1)
        rclick_thresh = gc.thumb_middle_click * (palm_size / 0.1)
        move_thresh   = gc.thumb_extend_threshold * (palm_size / 0.1)

        thumb_index_dist  = _dist(lm[4], lm[8])
        thumb_middle_dist = _dist(lm[4], lm[12])

        # Velocity Check: positive if closing
        vel_ti = self._prev_dist_ti - thumb_index_dist
        vel_tm = self._prev_dist_tm - thumb_middle_dist
        self._prev_dist_ti = thumb_index_dist
        self._prev_dist_tm = thumb_middle_dist

        # Default anchor
        cx, cy = lm[4].x, lm[4].y
        raw_gesture = Gesture.IDLE

        # --- Priority Decision Tree ---

        # 0. Mode switch (Pinky hold)
        if pinky_ext and not index_ext and not middle_ext and not ring_ext:
            raw_gesture = Gesture.MODE_SWITCH

        # 1. Multi-finger Scroll (4 fingers up)
        elif index_ext and middle_ext and ring_ext and pinky_ext:
            raw_gesture = Gesture.SCROLL
            cy = (lm[8].y + lm[12].y + lm[16].y + lm[20].y) / 4.0

        # 2. Shortcuts (1, 2, 3 fingers)
        elif index_ext and middle_ext and ring_ext and not pinky_ext:
            raw_gesture = Gesture.THREE_FINGERS
            cx, cy = (lm[8].x + lm[12].x + lm[16].x) / 3.0, (lm[8].y + lm[12].y + lm[16].y) / 3.0
        
        elif index_ext and middle_ext and not ring_ext:
            raw_gesture = Gesture.TWO_FINGERS
            cx, cy = (lm[8].x + lm[12].x) / 2.0, (lm[8].y + lm[12].y) / 2.0

        elif index_ext and not middle_ext and not ring_ext:
            raw_gesture = Gesture.ONE_FINGER
            cx, cy = lm[8].x, lm[8].y

        # 3. Thumb-based Clicks (Velocity-aware)
        elif thumb_index_dist < click_thresh:
            if vel_ti > 0.005 or thumb_index_dist < (click_thresh * 0.7):
                raw_gesture = Gesture.THUMB_CLICK
        
        elif thumb_middle_dist < rclick_thresh:
            if vel_tm > 0.005 or thumb_middle_dist < (rclick_thresh * 0.7):
                raw_gesture = Gesture.THUMB_RCLICK

        # 4. Standard Move (Thumb extended)
        else:
            thumb_mcp_dist = _dist(lm[4], lm[5])
            if thumb_mcp_dist > move_thresh:
                raw_gesture = Gesture.THUMB_MOVE

        # --- Hysteresis / Stability ---
        if raw_gesture == self.last_gesture:
            self.gesture_count += 1
        else:
            if self.gesture_count < self.MIN_FRAMES:
                raw_gesture = self.last_gesture
            else:
                self.last_gesture = raw_gesture
                self.gesture_count = 0

        return raw_gesture, cx, cy

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

        # Try Modal cloud client first
        from src.core.modal_vision import get_modal_client
        self._modal_client = get_modal_client()

        # Always initialize local MediaPipe as fallback
        import mediapipe as mp
        model_path = _ensure_model()
        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=model_path),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            num_hands=2,
            min_hand_detection_confidence=0.3,
            min_hand_presence_confidence=0.3,
            min_tracking_confidence=0.3,
        )
        self._landmarker   = mp.tasks.vision.HandLandmarker.create_from_options(options)
        self._mp_image_cls = mp.Image
        self._mp_format    = mp.ImageFormat.SRGB
        
        if self._modal_client:
            logger.info("VisionProcessor using Modal cloud (with local fallback).")
        else:
            logger.info("VisionProcessor using local MediaPipe.")

    # ------------------------------------------------------------------
    def decode_frame(self, jpeg_bytes: bytes) -> Optional[np.ndarray]:
        """Convert JPEG bytes into an OpenCV-compatible BGR array."""
        try:
            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            return frame
        except Exception as e:
            logger.error("Failed to decode frame: %s", e)
            return None

    async def process_frame(self, frame_input, builder_mode=False) -> GestureState:
        """Async version for the main server loop. Handles bytes or numpy."""
        if isinstance(frame_input, bytes):
            frame = self.decode_frame(frame_input)
        else:
            frame = frame_input
            
        if frame is None: return GestureState()
        return await self._process(frame, builder_mode)

    def process_frame_sync(self, frame_input, builder_mode=False) -> GestureState:
        """Sync version for the vision worker process. Handles bytes or numpy."""
        if isinstance(frame_input, bytes):
            frame = self.decode_frame(frame_input)
        else:
            frame = frame_input
            
        if frame is None: return GestureState()
        return asyncio.run(self._process(frame, builder_mode))

    async def _process(self, frame_bgr: np.ndarray, builder_mode: bool = False) -> GestureState:
        import cv2
        self.last_landmarks = None
        state = GestureState()

        # --- High-Performance Cloud Path ---
        hands, handedness = [], []
        # Bypass cloud for Builder Mode to ensure sub-10ms latency
        use_cloud = self._modal_client and os.environ.get("USE_MODAL", "false").lower() == "true" and not builder_mode
        
        if use_cloud:
            try:
                raw = await self._modal_client.detect(frame_bgr)
                if raw and raw.get("hands"):
                    hands, handedness = self._parse_modal_result(raw)
            except Exception as e:
                logger.warning("Cloud inference delayed or failed: %s", e)

        # --- Local Fallback Path (if cloud fails or is disabled) ---
        if not hands:
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            mp_image = self._mp_image_cls(image_format=self._mp_format, data=frame_rgb)
            result = self._landmarker.detect(mp_image)
            if result.hand_landmarks:
                hands = result.hand_landmarks
                handedness = result.handedness
            else:
                self._pinky_hold_frames = 0
                return state

        right_lm = None
        left_lm = None

        for i, hand in enumerate(hands):
            if i < len(handedness):
                label = handedness[i][0].category_name
                logger.info("Detected hand: %s", label)
                if label == "Right":
                    right_lm = hand
                else:
                    left_lm  = hand
            else:
                if right_lm is None: right_lm = hand
                else: left_lm = hand

        # Mirroring Fallback: If only one hand is seen and it's labeled "Left", 
        # treat it as "Right" for cursor control.
        if right_lm is None and left_lm is not None:
            logger.info("Mirroring fallback: Using left hand as right cursor.")
            right_lm = left_lm

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
            state.landmarks = [{"x": l.x, "y": l.y} for l in right_lm]

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

        if gesture != Gesture.IDLE:
            logger.info("Detected gesture: %s", gesture)

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
        state.finger_count       = 1 if gesture == Gesture.ONE_FINGER else 2 if gesture == Gesture.TWO_FINGERS else 3 if gesture == Gesture.THREE_FINGERS else 0

        # --- Scroll delta (4 fingers only) ---
        if gesture == Gesture.SCROLL:
            if self._prev_scroll_y is not None:
                state.scroll_dy = self._prev_scroll_y - raw_y
            self._prev_scroll_y = raw_y
        else:
            self._prev_scroll_y = None

        return state

    def _parse_modal_result(self, raw: dict):
        """Convert Modal JSON result into MediaPipe-compatible landmark objects."""
        class _LM:
            def __init__(self, d): self.x=d["x"]; self.y=d["y"]; self.z=d["z"]
        class _Hand:
            def __init__(self, lms): self._lms = [_LM(l) for l in lms]
            def __getitem__(self, i): return self._lms[i]
            def __len__(self): return len(self._lms)

        class _Handedness:
            def __init__(self, label):
                self.category_name = label
            def __getitem__(self, i): return self

        hands      = [_Hand(h["landmarks"]) for h in raw["hands"]]
        handedness = [[_Handedness(h["handedness"])] for h in raw["hands"]]
        return hands, handedness

    def draw_landmarks(self, frame: np.ndarray, state: GestureState) -> np.ndarray:
        """Draw hand landmarks and gesture status onto the frame."""
        annotated = frame.copy()
        h, w, _ = annotated.shape

        # state.landmarks is a flat list[dict[str,float]] with 21 entries (one per landmark)
        if state.landmarks:
            for lm in state.landmarks:
                cx = int(lm['x'] * w)
                cy = int(lm['y'] * h)
                cv2.circle(annotated, (cx, cy), 4, (0, 255, 149), -1)

        # Draw Gesture Status text
        if state.gesture and state.gesture != Gesture.IDLE:
            cv2.putText(annotated, f"GESTURE: {state.gesture.name}", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 149), 2)
        else:
            cv2.putText(annotated, "WAITING FOR HAND...", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (150, 150, 150), 2)

        return annotated

    def close(self) -> None:
        if self._landmarker:
            self._landmarker.close()
        logger.info("VisionProcessor closed.")
