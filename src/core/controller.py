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

from src.core.vision import Gesture, GestureState
from src.core.shortcuts import ShortcutManager
from src.core.utils import OneEuroFilter

logger = logging.getLogger(__name__)

pyautogui.PAUSE    = 0.0
pyautogui.FAILSAFE = False # Prevent crashes when cursor hits corners
import time


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

        # Adaptive Smoothing: One Euro Filter
        # freq=30 (matches camera), mincutoff=0.01 (heavy smooth at stop), beta=0.01 (low lag at speed)
        self._filter_x = OneEuroFilter(freq=30, mincutoff=0.01, beta=0.01)
        self._filter_y = OneEuroFilter(freq=30, mincutoff=0.01, beta=0.01)

        self._smooth_x: float = cfg.screen_w / 2.0
        self._smooth_y: float = cfg.screen_h / 2.0

        self._left_click_cooldown:  int = 0
        self._right_click_cooldown: int = 0
        self._scroll_cooldown:      int = 0
        self._move_lockout:         int = 0  # frames to block clicks after THUMB_MOVE

        self._drag_state:      _DragState = _DragState.IDLE
        self._drag_hold_count: int        = 0
        self._drag_release_grace: int      = 0
        self._click_start_time: float      = 0.0

        self._prev_gesture: Gesture = Gesture.IDLE

        self._shortcut_hold_frames = self.sc.hold_frames
        self._shortcut_cooldown = 0
        self._shortcut_counts = {
            Gesture.SCROLL: 0,
            # (other shortcuts if needed)
        }
        self._shortcut_triggered = False
        self._shortcut_start_pos: tuple[float, float] | None = None

        self._frac_x: float = 0.0
        self._frac_y: float = 0.0
        self._last_move_time = 0.0
        self._move_interval = 0.016 # ~60fps cap for mouse moves

        logger.info("MouseController ready — screen %dx%d",
                    cfg.screen_w, cfg.screen_h)

    # ------------------------------------------------------------------
    def update(self, state: GestureState) -> str:
        if self._left_click_cooldown  > 0: self._left_click_cooldown  -= 1
        if self._right_click_cooldown > 0: self._right_click_cooldown -= 1
        if self._scroll_cooldown      > 0: self._scroll_cooldown      -= 1
        if self._shortcut_cooldown    > 0: self._shortcut_cooldown    -= 1
        if self._move_lockout         > 0: self._move_lockout         -= 1

        sx, sy = self._map_to_screen(state.cursor_x, state.cursor_y)

        # 1. Index-based gestures (Restored)
        if state.gesture in (Gesture.INDEX_MOVE, Gesture.LEFT_CLICK, Gesture.RIGHT_CLICK):
            return self._handle_cursor_action(state, sx, sy)

        # 2. Multi-finger stabilized gestures (Shortcuts/Scroll)
        return self._handle_stabilized_action(state, sx, sy)

    def _handle_cursor_action(self, state: GestureState, sx: float, sy: float) -> str:
        if state.gesture == Gesture.INDEX_MOVE:
            self._handle_move(sx, sy)
            self._reset_drag()
            status = "MOVE"
        elif state.gesture == Gesture.LEFT_CLICK:
            self._handle_move(sx, sy)
            now = time.time()
            if self._drag_state == _DragState.IDLE and self._left_click_cooldown == 0:
                self._drag_state = _DragState.COUNTING
                self._click_start_time = now
            elif self._drag_state == _DragState.COUNTING:
                # If held for more than 300ms, start dragging
                if now - self._click_start_time > 0.3:
                    pyautogui.mouseDown(button="left", _pause=False)
                    self._drag_state = _DragState.DRAGGING
            status = "DRAGGING" if self._drag_state == _DragState.DRAGGING else "CLICK_WAIT"
        elif state.gesture == Gesture.RIGHT_CLICK:
            # Rock sign → right click
            self._reset_drag()
            if self._right_click_cooldown == 0:
                pyautogui.click(button="right", _pause=False)
                self._right_click_cooldown = self.gc.pinch_cooldown_frames
            status = "RIGHT CLICK"
        else:
            status = "IDLE"

        self._reset_shortcut_state()
        self._prev_gesture = state.gesture
        return status

    def _handle_stabilized_action(self, state: GestureState, sx: float, sy: float) -> str:
        status = "IDLE"
        if state.gesture == Gesture.SCROLL:
            self._shortcut_counts[Gesture.SCROLL] += 1
            if self._shortcut_counts[Gesture.SCROLL] > 3:
                status = self._handle_scroll(state.scroll_dy)
            else:
                status = "STABILIZING SCROLL"
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
        
        # Clamp to prevent pyautogui.FailSafeException at screen corners (e.g. 0,0)
        sx = max(1.0, min(self.cfg.screen_w - 2.0, sx))
        sy = max(1.0, min(self.cfg.screen_h - 2.0, sy))
        
        return sx, sy

    def _handle_move(self, tx: float, ty: float) -> None:
        self._smooth_x = self._filter_x(tx)
        self._smooth_y = self._filter_y(ty)
        
        dx = abs(self._smooth_x - tx)
        dy = abs(self._smooth_y - ty)
        
        # Only move if the change is above the threshold
        if dx > self.gc.move_threshold_px or dy > self.gc.move_threshold_px:
            try:
                pyautogui.moveTo(int(self._smooth_x), int(self._smooth_y),
                                 duration=0, _pause=False)
            except Exception as e:
                logger.error(f"Move Error: {e}")

    def _reset_drag(self) -> None:
        if self._drag_state == _DragState.COUNTING:
            # Reached here because gesture changed or was lost before 300ms
            pyautogui.click(button="left", _pause=False)
            self._left_click_cooldown = self.gc.pinch_cooldown_frames
            logger.debug("Single click triggered from short hold.")
        
        elif self._drag_state == _DragState.DRAGGING:
            # Add a small grace period (e.g. 5 frames) to handle camera jitter
            self._drag_release_grace += 1
            if self._drag_release_grace < 5:
                return

            pyautogui.mouseUp(button="left", _pause=False)
            self._left_click_cooldown = self.gc.pinch_cooldown_frames
            logger.debug("Drag end (mouseUp).")

        self._drag_state      = _DragState.IDLE
        self._drag_hold_count = 0
        self._drag_release_grace = 0
        self._click_start_time = 0.0

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

        return "SHORTCUT TRIGGERED"

    def _reset_shortcut_state(self) -> None:
        for key in self._shortcut_counts:
            self._shortcut_counts[key] = 0
        self._shortcut_triggered = False
        self._shortcut_start_pos = None

    def handle_touch_move(self, dx: float, dy: float) -> str:
        """Handle relative movement from phone trackpad."""
        now = time.time()
        if now - self._last_move_time < self._move_interval:
             return "RATE_LIMITED"
        
        # Dynamic sensitivity based on movement speed
        speed = (dx*dx + dy*dy)**0.5
        boost = 1.0 + min(2.0, speed / 5.0) # Move faster when finger moves faster
        # Use the explicit trackpad sensitivity setting
        sens = self.gc.trackpad_sensitivity * boost 
        
        self._frac_x += dx * sens
        self._frac_y += dy * sens
        
        move_x = int(self._frac_x)
        move_y = int(self._frac_y)
        
        self._frac_x -= move_x
        self._frac_y -= move_y
        
        if move_x != 0 or move_y != 0:
            try:
                pyautogui.moveRel(move_x, move_y, _pause=False)
                self._last_move_time = now
            except Exception as e:
                logger.error(f"Touch Move Error: {e}")
            
        return "TOUCH MOVE"

    def handle_click(self, button: str = "left") -> str:
        """Handle tap clicks from phone."""
        pyautogui.click(button=button, _pause=False)
        return f"{button.upper()} CLICK"

    def handle_click_state(self, button: str, is_down: bool) -> str:
        """Handle persistent click states (down/up) for dragging."""
        if is_down:
            pyautogui.mouseDown(button=button, _pause=False)
            return f"{button.upper()} DOWN"
        else:
            pyautogui.mouseUp(button=button, _pause=False)
            return f"{button.upper()} UP"

    def handle_touch_scroll(self, dy: float) -> str:
        """Handle vertical scroll from phone."""
        # Multiplier to make it feel natural
        scroll_amount = int(dy * -1.5)
        if abs(scroll_amount) >= 1:
            pyautogui.scroll(scroll_amount, _pause=False)
            return "TOUCH SCROLL"
        return "TOUCH READY"

    def handle_touch_zoom(self, delta: float) -> str:
        """Handle pinch-to-zoom from phone."""
        # Sensitivity multiplier
        zoom_dir = 1 if delta > 0 else -1
        # Ctrl + Scroll is the universal zoom shortcut
        pyautogui.keyDown('ctrl')
        pyautogui.scroll(zoom_dir * 10, _pause=False)
        pyautogui.keyUp('ctrl')
        return "TOUCH ZOOM"

    def handle_touch_shortcut(self, slot: str) -> str:
        """Handle multi-finger tap shortcuts from phone."""
        if not self.shortcuts:
            return "TOUCH SHORTCUT NO MANAGER"
        
        # Maps slot 'touch_3_finger' to existing 'three_fingers' logic
        # or we can add new slots.
        logic_slot = {
            "touch_3_finger": "three_fingers",
            "touch_4_finger": "four_fingers" # You can add four_fingers to shortcuts too
        }.get(slot, slot)

        result = self.shortcuts.trigger(logic_slot)
        return f"TOUCH SHORTCUT {slot}: {result}"
