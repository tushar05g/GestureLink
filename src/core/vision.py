"""
vision.py — MediaPipe hand tracking, gesture classification, two-hand support.

Gestures
--------
Cursor Mode (single hand):
  THUMB_MOVE    : Thumb out, all other fingers CURLED  -> move cursor
  THUMB_CLICK   : Thumb tip + Index tip pinch touch    -> left click
  ROCK_RCLICK   : Index + Pinky raised (rock sign)     -> right click
  SCROLL        : All 4 fingers raised, no thumb       -> scroll up/down
  THREE_FINGERS : Index + Middle + Ring raised          -> shortcut
  MODE_SWITCH   : Pinky ONLY raised, hold 20 frames    -> switch mode

Builder Mode (single hand) — UNCHANGED:
  POINTING      : Index only                           -> ghost preview
  PINCH         : Index + Middle up + drag             -> paint cubes
  THUMB_PINCH   : Thumb + Index touch                  -> move cube/group
  SCROLL        : 3 fingertips                         -> erase
  RIGHT_CLICK   : Rock sign                            -> undo
  MODE_SWITCH   : Pinky hold                           -> switch mode

Builder Mode (two hands):
  Left fist + Right index sweep      -> rotate
  Left fist + Right thumb+index open -> zoom in
  Left fist + Right thumb+index close-> zoom out
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
    # --- Cursor mode (Restored Index-based) ---
    INDEX_MOVE    = "INDEX_MOVE"    # index only → move cursor
    LEFT_CLICK    = "LEFT_CLICK"    # index + middle → left click/drag
    RIGHT_CLICK   = "RIGHT_CLICK"   # rock sign → right click
    # Shared / Builder
    POINTING      = "POINTING"      # index only → ghost preview
    PINCH         = "PINCH"         # index+middle → paint
    THUMB_PINCH   = "THUMB_PINCH"   # thumb + index touch → move cube
    SCROLL        = "SCROLL"        # 3 fingertips
    MODE_SWITCH   = "MODE_SWITCH"   # pinky hold
    # --- Legacy aliases (kept for controller compatibility) ---
    ONE_FINGER    = "ONE_FINGER"
    TWO_FINGERS   = "TWO_FINGERS"
    THREE_FINGERS = "THREE_FINGERS"
    FOUR_FINGERS  = "FOUR_FINGERS"


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
    # Mode info: 0=Cursor, 1=Canvas, 2=Builder
    active_mode:        int     = 0
    # For Canvas Mode rendering
    canvas_paths:       list    = field(default_factory=list)

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

    def classify_cursor(self, lm) -> tuple[Gesture, float, float]:
        """Classify gestures for Cursor (Productivity) mode — Index Based."""
        gc = self.gc

        index_ext  = _finger_extended(lm, 8,  6)
        middle_ext = _finger_extended(lm, 12, 10)
        ring_ext   = _finger_extended(lm, 16, 14)
        pinky_ext  = _finger_extended(lm, 20, 18)

        # Index tip position as cursor anchor
        cx, cy = 1.0 - lm[8].x, lm[8].y

        # Priority 0: Mode switch — pinky only hold
        if pinky_ext and not index_ext and not middle_ext and not ring_ext:
            return Gesture.MODE_SWITCH, cx, cy

        # Priority 1: Right click — rock sign (index + pinky)
        if index_ext and pinky_ext and not middle_ext and not ring_ext:
            return Gesture.RIGHT_CLICK, cx, cy

        # Priority 2: Scroll — 3 fingertips raised
        index_tip_up  = _fingertip_raised(lm, 8,  5)
        middle_tip_up = _fingertip_raised(lm, 12, 9)
        ring_tip_up   = _fingertip_raised(lm, 16, 13)
        if index_tip_up and middle_tip_up and ring_tip_up and not pinky_ext:
            scroll_y = (lm[8].y + lm[12].y + lm[16].y) / 3.0
            return Gesture.SCROLL, cx, scroll_y

        # Priority 3: Left click / drag — index + middle up (V-sign)
        if index_ext and middle_ext and not ring_ext and not pinky_ext:
            return Gesture.LEFT_CLICK, cx, cy

        # Priority 4: Move cursor — index only (strictly raised)
        if index_tip_up and not middle_tip_up and not ring_tip_up and not pinky_ext:
            return Gesture.INDEX_MOVE, cx, cy

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

        cx, cy = 1.0 - lm[8].x, lm[8].y

        # Priority 0: Mode switch — pinky only
        if pinky_ext and not index_ext and not middle_ext and not ring_ext:
            return Gesture.MODE_SWITCH, cx, cy

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
# Drawing Helpers (Isometric)
# ---------------------------------------------------------------------------

def _cube_origin(gx, gy, gz, gs, ox, oy):
    return int(gx*gs + gz*ox), int(gy*gs - gz*oy)

def _draw_iso_cube(frame, px, py, gs, color_bgr, alpha=1.0, outline=False, outline_color=(0,255,255)):
    h = gs // 2
    b, g, r = color_bgr

    def shade(f):
        return (int(min(255,b*f)), int(min(255,g*f)), int(min(255,r*f)))

    top   = np.array([[px+gs//2,py],[px+gs,py+h],[px+gs//2,py+gs//2+h//2],[px,py+h]], np.int32)
    left  = np.array([[px,py+h],[px+gs//2,py+gs//2+h//2],[px+gs//2,py+gs],[px,py+gs-h//2]], np.int32)
    right = np.array([[px+gs//2,py+gs//2+h//2],[px+gs,py+h],[px+gs,py+gs-h//2],[px+gs//2,py+gs]], np.int32)

    if alpha < 1.0:
        ov = frame.copy()
        cv2.fillPoly(ov, [top],   shade(1.4))
        cv2.fillPoly(ov, [left],  shade(1.0))
        cv2.fillPoly(ov, [right], shade(0.6))
        cv2.addWeighted(ov, alpha, frame, 1-alpha, 0, frame)
    else:
        cv2.fillPoly(frame, [top],   shade(1.4))
        cv2.fillPoly(frame, [left],  shade(1.0))
        cv2.fillPoly(frame, [right], shade(0.6))

    edge = outline_color if outline else shade(0.3)
    lw   = 2 if outline else 1
    for poly in [top, left, right]:
        cv2.polylines(frame, [poly], True, edge, lw, cv2.LINE_AA)

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
        """Pure-sync version safe to call from a thread-pool executor.
        
        Does NOT call asyncio.run() — safe when called from run_in_executor()
        inside a running event loop.
        """
        import cv2
        if isinstance(frame_input, bytes):
            frame = self.decode_frame(frame_input)
        else:
            frame = frame_input

        if frame is None:
            return GestureState()

        self.last_landmarks = None
        state = GestureState()

        # Local MediaPipe inference (synchronous, no cloud path)
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = self._mp_image_cls(image_format=self._mp_format, data=frame_rgb)
        result = self._landmarker.detect(mp_image)

        if not result.hand_landmarks:
            self._pinky_hold_frames = 0
            return state

        hands = result.hand_landmarks
        handedness = result.handedness

        right_lm = None
        left_lm = None

        if hands:
            # If two hands, pick them by label first
            for i, hand in enumerate(hands):
                label = handedness[i][0].category_name
                if label == "Right": right_lm = hand
                else: left_lm = hand
            
            # If only one hand found, ALWAYS treat it as the primary controller
            if right_lm is None and left_lm is not None:
                right_lm = left_lm
                left_lm = None

        self.last_landmarks = right_lm
        self.last_left_landmarks = left_lm

        if left_lm:
            state.left_fist = _is_fist(left_lm, self.cfg.opengl.fist_threshold)

        if right_lm:
            state.right_thumb_index_dist = _dist(right_lm[4], right_lm[8])
            state.right_index_pos = (right_lm[8].x, right_lm[8].y)
            state.landmarks = [{"x": l.x, "y": l.y} for l in right_lm]

        if right_lm is None:
            self._pinky_hold_frames = 0
            self._prev_scroll_y = None
            return state

        lm = right_lm
        if builder_mode:
            gesture, raw_x, raw_y = self._classifier.classify_builder(lm)
        else:
            gesture, raw_x, raw_y = self._classifier.classify_cursor(lm)

        if gesture == Gesture.MODE_SWITCH:
            self._pinky_hold_frames += 1
            if self._pinky_hold_frames == self._PINKY_HOLD_REQUIRED:
                state.mode_switch = True
                self._pinky_hold_frames = 0
        else:
            self._pinky_hold_frames = 0

        cursor_x = raw_x
        state.gesture = gesture
        state.cursor_x = cursor_x
        state.cursor_y = raw_y
        state.pinch_active = gesture == Gesture.PINCH
        state.thumb_pinch_active = gesture == Gesture.THUMB_PINCH
        state.finger_count = (3 if gesture == Gesture.THREE_FINGERS else
                              4 if gesture in (Gesture.FOUR_FINGERS, Gesture.SCROLL) else 0)

        if gesture == Gesture.SCROLL:
            if self._prev_scroll_y is not None:
                state.scroll_dy = self._prev_scroll_y - raw_y
            self._prev_scroll_y = raw_y
        else:
            self._prev_scroll_y = None

        return state

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


        # --- Handedness Logic ---
        # Since we flip the frame in the server, the user's Right hand 
        # may be labeled "Left" by MediaPipe. We take the most confident hand.
        right_lm = None
        left_lm = None

        if hands:
            for i, hand in enumerate(hands):
                label = handedness[i][0].category_name
                if label == "Right": right_lm = hand
                else: left_lm = hand
            
            # One-hand fallback: Always use whichever hand is visible
            if right_lm is None and left_lm is not None:
                right_lm = left_lm
                left_lm = None

        self.last_landmarks      = right_lm
        self.last_left_landmarks = left_lm

        # --- Left fist detection ---
        left_fist = False
        if left_lm:
            left_fist = _is_fist(left_lm, self.cfg.opengl.fist_threshold)
            state.left_fist = left_fist

        # --- Right hand data ---
        if right_lm:
            state.right_thumb_index_dist = _dist(right_lm[4], right_lm[8])
            state.right_index_pos = (right_lm[8].x, right_lm[8].y)
            state.landmarks = [{"x": l.x, "y": l.y} for l in right_lm]

        # --- Classify gesture ---
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

        # Use raw_x directly because frame is pre-flipped in server loop
        cursor_x = raw_x

        state.gesture            = gesture
        state.cursor_x           = cursor_x
        state.cursor_y           = raw_y
        state.pinch_active       = gesture == Gesture.PINCH
        state.thumb_pinch_active = gesture == Gesture.THUMB_PINCH
        state.finger_count       = 3 if gesture == Gesture.THREE_FINGERS else 4 if gesture in (Gesture.FOUR_FINGERS, Gesture.SCROLL) else 0

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

        # ── Draw Canvas Paths (2D mode) ─────────────────────────────────────
        if state.active_mode == 1 and hasattr(state, 'canvas_paths'):
            for path in state.canvas_paths:
                if len(path) < 2: continue
                points = np.array([ [int(p[0]*w), int(p[1]*h)] for p in path], np.int32)
                color = path[0][2] if len(path[0]) > 2 else (0, 255, 149)
                cv2.polylines(annotated, [points], False, color, 3, cv2.LINE_AA)

        # ── Draw Builder Cubes (3D mode) ────────────────────────────────────
        if state.active_mode == 2 and hasattr(state, 'builder_world') and state.builder_world:
            cc = self.cfg.cube
            gs, ox, oy = cc.grid_size, cc.iso_offset_x, cc.iso_offset_y
            world = state.builder_world
            selected_set = set(world.selected_group) if hasattr(world, 'selected_group') else set()

            # Draw back→front layers
            for gz in range(cc.num_layers-1, -1, -1):
                color = cc.layer_colors[gz]
                layer_cubes = sorted(
                    [c for c in world.cubes if c.gz == gz],
                    key=lambda c: (-c.gy, -c.gx)
                )
                for cube in layer_cubes:
                    px, py = _cube_origin(cube.gx, cube.gy, cube.gz, gs, ox, oy)
                    is_sel = cube in selected_set
                    _draw_iso_cube(annotated, px, py, gs, color, 1.0, outline=is_sel, outline_color=cc.selected_color)

            # Draw Ghost preview
            if hasattr(state, 'builder_ghost') and state.builder_ghost:
                px, py = _cube_origin(*state.builder_ghost, gs, ox, oy)
                _draw_iso_cube(annotated, px, py, gs, cc.layer_colors[state.builder_ghost[2]], 0.5, True, (200,200,200))

        # ── Draw Premium HUD Overlay ────────────────────────────────────────
        active = state.gesture and state.gesture != Gesture.IDLE
        mode_text = "[BUILDER]" if state.active_mode == 2 else "[CANVAS]" if state.active_mode == 1 else "[CURSOR]"
        label = f"{mode_text} {state.gesture.upper()}" if active else f"{mode_text} SCANNING..."
        color = (0, 255, 149) if active else (200, 200, 200)

        # Draw semi-transparent background box
        overlay = annotated.copy()
        box_w = 300 if active else 220
        cv2.rectangle(overlay, (10, 10), (box_w, 60), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.6, annotated, 0.4, 0, annotated)

        # Draw label text
        cv2.putText(annotated, label, (25, 45),
                    cv2.FONT_HERSHEY_DUPLEX, 0.8, color, 1, cv2.LINE_AA)
        
        # Draw small status light
        light_color = (0, 255, 0) if active else (0, 0, 255)
        cv2.circle(annotated, (20, 36), 4, light_color, -1)

        return annotated

    def close(self) -> None:
        if self._landmarker:
            self._landmarker.close()
        logger.info("VisionProcessor closed.")
