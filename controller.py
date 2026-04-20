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

from vision import Gesture, GestureState

logger = logging.getLogger(__name__)

pyautogui.PAUSE    = 0.0
pyautogui.FAILSAFE = True


class _DragState(Enum):
    IDLE     = auto()
    COUNTING = auto()
    DRAGGING = auto()


class MouseController:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.gc  = cfg.gesture

        self._smooth_x: float = cfg.screen_w / 2.0
        self._smooth_y: float = cfg.screen_h / 2.0

        self._left_click_cooldown:  int = 0
        self._right_click_cooldown: int = 0
        self._scroll_cooldown:      int = 0

        self._drag_state:      _DragState = _DragState.IDLE
        self._drag_hold_count: int        = 0

        self._prev_gesture: Gesture = Gesture.IDLE

        logger.info("MouseController ready — screen %dx%d",
                    cfg.screen_w, cfg.screen_h)

    # ------------------------------------------------------------------
    def update(self, state: GestureState) -> str:
        status = "IDLE"

        if self._left_click_cooldown  > 0: self._left_click_cooldown  -= 1
        if self._right_click_cooldown > 0: self._right_click_cooldown -= 1
        if self._scroll_cooldown      > 0: self._scroll_cooldown      -= 1

        sx, sy = self._map_to_screen(state.cursor_x, state.cursor_y)

        if state.gesture == Gesture.THUMB_MOVE:
            self._handle_move(sx, sy)
            self._reset_drag()
            status = "MOVE"

        elif state.gesture == Gesture.THUMB_CLICK:
            self._handle_move(sx, sy)
            self._reset_drag()
            if self._left_click_cooldown == 0:
                pyautogui.click(_pause=False)
                self._left_click_cooldown = self.gc.pinch_cooldown_frames
                logger.debug("Left click.")
            status = "LEFT CLICK"

        elif state.gesture == Gesture.THUMB_RCLICK:
            self._handle_move(sx, sy)
            self._reset_drag()
            if self._right_click_cooldown == 0:
                pyautogui.click(button="right", _pause=False)
                self._right_click_cooldown = self.gc.pinch_cooldown_frames
                logger.debug("Right click.")
            status = "RIGHT CLICK"

        elif state.gesture == Gesture.SCROLL:
            self._reset_drag()
            status = self._handle_scroll(state.scroll_dy)

        else:
            self._reset_drag()

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
        if dx > self.gc.move_threshold_px or dy > self.gc.move_threshold_px:
            pyautogui.moveTo(int(self._smooth_x), int(self._smooth_y),
                             duration=0, _pause=False)

    def _reset_drag(self) -> None:
        if self._drag_state == _DragState.DRAGGING:
            pyautogui.mouseUp(button="left", _pause=False)
        self._drag_state      = _DragState.IDLE
        self._drag_hold_count = 0

    def _handle_scroll(self, dy: float) -> str:
        if self._scroll_cooldown > 0:
            return "SCROLL"
        if abs(dy) > self.gc.scroll_threshold:
            direction = 1 if dy > 0 else -1
            pyautogui.scroll(direction * self.gc.scroll_speed, _pause=False)
            self._scroll_cooldown = self.gc.scroll_cooldown_frames
            return f"SCROLL {'UP' if direction > 0 else 'DOWN'}"
        return "SCROLL READY"

    def _handle_right_click(self) -> str:
        if self._right_click_cooldown == 0:
            pyautogui.click(button="right", _pause=False)
            self._right_click_cooldown = self.gc.pinch_cooldown_frames
        return "RIGHT CLICK"