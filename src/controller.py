"""
controller.py — Cursor Mode: translates GestureState → Windows mouse actions.

New gestures:
  THUMB_MOVE   : thumb tip position → move cursor
  THUMB_CLICK  : thumb+index touch  → left click
  THUMB_RCLICK : thumb+middle touch → right click
  SCROLL       : 3 fingertips       → scroll
"""
from __future__ import annotations

import logging
from enum import Enum, auto

import pyautogui

from src.vision import Gesture, GestureState
from src.shortcuts import ShortcutManager

logger = logging.getLogger(__name__)

pyautogui.PAUSE    = 0.0
pyautogui.FAILSAFE = True


class _DragState(Enum):
    IDLE     = auto()
    COUNTING = auto()
    DRAGGING = auto()


class MouseController:
    def __init__(self, cfg, shortcuts: ShortcutManager | None = None, responsive: bool = False) -> None:
        self.cfg = cfg
        self.gc  = cfg.gesture
        self.sc  = cfg.shortcuts
        self.shortcuts = shortcuts
        self.responsive = responsive

        sw, sh = pyautogui.size()
        self.cfg.screen_w = sw
        self.cfg.screen_h = sh

        self._smooth_x: float = cfg.screen_w / 2.0
        self._smooth_y: float = cfg.screen_h / 2.0

        self._left_click_cooldown:  int = 0
        self._right_click_cooldown: int = 0
        self._scroll_cooldown:      int = 0

        self._drag_state:      _DragState = _DragState.IDLE
        self._drag_hold_count: int        = 0
        self._drag_release_grace: int      = 0

        self._prev_gesture: Gesture = Gesture.IDLE

        self._shortcut_hold_frames = self.sc.hold_frames
        self._shortcut_cooldown = 0
        self._shortcut_counts = {
            Gesture.ONE_FINGER: 0,
            Gesture.TWO_FINGERS: 0,
            Gesture.THREE_FINGERS: 0,
            Gesture.FOUR_FINGERS: 0,
        }
        self._shortcut_triggered = False
        self._shortcut_start_pos: tuple[float, float] | None = None

        logger.info("MouseController ready — screen %dx%d",
                    cfg.screen_w, cfg.screen_h)

    # ------------------------------------------------------------------
    def update(self, state: GestureState) -> str:
        status = "IDLE"

        if self._left_click_cooldown  > 0: self._left_click_cooldown  -= 1
        if self._right_click_cooldown > 0: self._right_click_cooldown -= 1
        if self._scroll_cooldown      > 0: self._scroll_cooldown      -= 1
        if self._shortcut_cooldown    > 0: self._shortcut_cooldown    -= 1

        sx, sy = self._map_to_screen(state.cursor_x, state.cursor_y)

        # FAST PATH: Move and Clicks are immediate
        if state.gesture in (Gesture.THUMB_MOVE, Gesture.THUMB_CLICK, Gesture.THUMB_RCLICK):
            if state.gesture == Gesture.THUMB_MOVE:
                self._handle_move(sx, sy)
                self._reset_drag()
                status = "MOVE"
            elif state.gesture == Gesture.THUMB_CLICK:
                self._handle_move(sx, sy)
                if self._drag_state != _DragState.DRAGGING:
                    if self._left_click_cooldown == 0:
                        pyautogui.mouseDown(button="left", _pause=False)
                        self._drag_state = _DragState.DRAGGING
                        self._drag_release_grace = 0
                status = "DRAGGING"
            elif state.gesture == Gesture.THUMB_RCLICK:
                self._handle_move(sx, sy)
                if self._right_click_cooldown == 0:
                    pyautogui.click(button="right", _pause=False)
                    self._right_click_cooldown = self.gc.pinch_cooldown_frames
                status = "RIGHT CLICK"
            
            # Reset stabilization for non-complex gestures
            self._reset_shortcut_state()
            self._prev_gesture = state.gesture
            return status

        # STABILIZED PATH: Scroll (4 fingers) and Shortcuts require hold time
        if state.gesture == Gesture.SCROLL:
            # We use the existing shortcut_counts to stabilize scroll too
            self._shortcut_counts[Gesture.FOUR_FINGERS] += 1
            if self._shortcut_counts[Gesture.FOUR_FINGERS] > 3: # 3 frames of stability
                status = self._handle_scroll(state.scroll_dy)
            else:
                status = "STABILIZING SCROLL"
        elif state.gesture in (Gesture.ONE_FINGER, Gesture.TWO_FINGERS, Gesture.THREE_FINGERS):
            self._handle_move(sx, sy) # Keep cursor moving during hold
            status = self._handle_shortcuts(state)
        else:
            self._reset_drag()
            self._reset_shortcut_state()

        self._prev_gesture = state.gesture
        return status

    # ------------------------------------------------------------------
    def _map_to_screen(self, nx: float, ny: float) -> tuple[float, float]:
        m  = self.gc.frame_margin
        ax = max(m, min(1.0 - m, nx))
        ay = max(m, min(1.0 - m, ny))
        sx = (ax - m) / (1.0 - 2 * m) * self.cfg.screen_w
        sy = (ay - m) / (1.0 - 2 * m) * self.cfg.screen_h
        return sx, sy

    def _handle_move(self, tx: float, ty: float) -> None:
        alpha = self.gc.smoothing
        self._smooth_x += alpha * (tx - self._smooth_x)
        self._smooth_y += alpha * (ty - self._smooth_y)
        
        dx = abs(self._smooth_x - tx)
        dy = abs(self._smooth_y - ty)
        
        # Only move if the change is above the threshold to prevent "pixel vibration"
        if dx > self.gc.move_threshold_px or dy > self.gc.move_threshold_px:
            pyautogui.moveTo(int(self._smooth_x), int(self._smooth_y),
                             duration=0, _pause=False)

    def _reset_drag(self) -> None:
        if self._drag_state == _DragState.DRAGGING:
            # Add a small grace period (e.g. 3 frames) to handle camera jitter
            # during a pinch-drag.
            self._drag_release_grace += 1
            if self._drag_release_grace < 5:
                return

            pyautogui.mouseUp(button="left", _pause=False)
            self._left_click_cooldown = self.gc.pinch_cooldown_frames
            logger.debug("Drag end (mouseUp).")
        self._drag_state      = _DragState.IDLE
        self._drag_hold_count = 0
        self._drag_release_grace = 0

    def _handle_scroll(self, dy: float) -> str:
        if self._scroll_cooldown > 0:
            return "SCROLL"
        if abs(dy) > self.gc.scroll_threshold:
            direction = 1 if dy > 0 else -1
            pyautogui.scroll(direction * self.gc.scroll_speed, _pause=False)
            self._scroll_cooldown = self.gc.scroll_cooldown_frames
            return f"SCROLL {'UP' if direction > 0 else 'DOWN'}"
        return "SCROLL READY"

    def _handle_shortcuts(self, state: GestureState) -> str:
        current = state.gesture
        # Only reset drag if we've actually moved away from a drag gesture.
        # But _handle_shortcuts is only called for ONE/TWO/THREE_FINGERS.
        self._reset_drag()

        current = state.gesture
        for key in self._shortcut_counts:
            if key != current:
                self._shortcut_counts[key] = 0

        self._shortcut_counts[current] += 1

        # --- Movement check for shortcuts ---
        # If the hand moves significantly while holding a shortcut gesture,
        # reset the counter so it doesn't trigger while the user is just moving/dragging.
        sx, sy = self._map_to_screen(state.cursor_x, state.cursor_y)
        if self._shortcut_start_pos is None or self._prev_gesture != current:
            self._shortcut_start_pos = (sx, sy)

        dist = ((sx - self._shortcut_start_pos[0])**2 + (sy - self._shortcut_start_pos[1])**2)**0.5
        # Use a slightly more generous threshold for "stillness" during hold (e.g. 15px)
        if dist > self.gc.move_threshold_px * 4.0:
            self._shortcut_counts[current] = 0
            self._shortcut_start_pos = (sx, sy)
            return "SHORTCUT MOVING"

        # (Removed three-finger scroll behavior to allow 4-finger dedicated scrolling)

        if self._shortcut_cooldown > 0:
            return "SHORTCUT COOLDOWN"

        if self._shortcut_triggered:
            return "SHORTCUT TRIGGERED"

        if self._shortcut_counts[current] < self._shortcut_hold_frames:
            return "SHORTCUT READY"

        self._shortcut_triggered = True
        self._shortcut_cooldown = self.sc.cooldown_frames

        slot = {
            Gesture.ONE_FINGER: "one_finger",
            Gesture.TWO_FINGERS: "two_fingers",
            Gesture.THREE_FINGERS: "three_fingers",
        }[current]

        if not self.shortcuts:
            return "SHORTCUT NO MANAGER"

        result = self.shortcuts.trigger(slot)
        return f"SHORTCUT {slot}: {result}"

    def _reset_shortcut_state(self) -> None:
        for key in self._shortcut_counts:
            self._shortcut_counts[key] = 0
        self._shortcut_triggered = False
        self._shortcut_start_pos = None

    def handle_touch_move(self, dx: float, dy: float) -> str:
        """Handle relative movement from phone trackpad."""
        sens = self.gc.smoothing * 15.0 
        pyautogui.moveRel(int(dx * sens), int(dy * sens), _pause=False)
        return "TOUCH MOVE"

    def handle_click(self, button: str = "left") -> str:
        """Handle tap clicks from phone."""
        pyautogui.click(button=button, _pause=False)
        return f"{button.upper()} CLICK"

    def handle_touch_scroll(self, dy: float) -> str:
        """Handle vertical scroll from phone."""
        scroll_amount = int(dy * self.gc.scroll_speed * 0.1)
        if abs(scroll_amount) >= 1:
            pyautogui.scroll(scroll_amount, _pause=False)
            return "TOUCH SCROLL"
        return "TOUCH READY"
