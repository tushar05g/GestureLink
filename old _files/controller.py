"""
controller.py — Cursor Mode mouse actions.

Gestures:
  INDEX_MOVE  : index only          -> move cursor
  LEFT_CLICK  : index + middle tap  -> left click
  LEFT_CLICK  : index + middle hold -> drag
  RIGHT_CLICK : rock sign           -> right click
  SCROLL      : 3 fingertips        -> scroll
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
    COUNTING = auto()   # left click held, counting frames
    DRAGGING = auto()   # mouse button held down


class MouseController:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.gc  = cfg.gesture

        self._smooth_x = cfg.screen_w / 2.0
        self._smooth_y = cfg.screen_h / 2.0

        self._left_cooldown:   int = 0
        self._right_cooldown:  int = 0
        self._scroll_cooldown: int = 0

        self._drag_state: _DragState = _DragState.IDLE
        self._drag_count: int        = 0

        logger.info("MouseController ready — %dx%d", cfg.screen_w, cfg.screen_h)

    # ------------------------------------------------------------------
    def update(self, state: GestureState) -> str:
        if self._left_cooldown   > 0: self._left_cooldown   -= 1
        if self._right_cooldown  > 0: self._right_cooldown  -= 1
        if self._scroll_cooldown > 0: self._scroll_cooldown -= 1

        sx, sy = self._map(state.cursor_x, state.cursor_y)
        status = "IDLE"

        if state.gesture == Gesture.INDEX_MOVE:
            self._move(sx, sy)
            self._reset_drag()
            status = "MOVE"

        elif state.gesture == Gesture.LEFT_CLICK:
            # Move cursor to current position first
            self._move(sx, sy)
            status = self._handle_click_drag()

        elif state.gesture == Gesture.RIGHT_CLICK:
            self._reset_drag()
            if self._right_cooldown == 0:
                pyautogui.click(button="right", _pause=False)
                self._right_cooldown = self.gc.pinch_cooldown_frames
                logger.debug("Right click.")
            status = "RIGHT CLICK"

        elif state.gesture == Gesture.SCROLL:
            self._reset_drag()
            status = self._scroll(state.scroll_dy)

        else:
            # Any other gesture — release drag if active, fire click if short hold
            self._reset_drag()

        return status

    # ------------------------------------------------------------------
    def _map(self, nx, ny) -> tuple:
        m  = self.gc.frame_margin
        ax = max(m, min(1.0-m, nx))
        ay = max(m, min(1.0-m, ny))
        sx = (ax-m) / (1.0-2*m) * self.cfg.screen_w
        sy = (ay-m) / (1.0-2*m) * self.cfg.screen_h
        return sx, sy

    def _move(self, tx, ty) -> None:
        a = self.gc.smoothing
        self._smooth_x += a * (tx - self._smooth_x)
        self._smooth_y += a * (ty - self._smooth_y)
        if (abs(self._smooth_x - tx) > self.gc.move_threshold_px or
                abs(self._smooth_y - ty) > self.gc.move_threshold_px):
            pyautogui.moveTo(int(self._smooth_x), int(self._smooth_y),
                             duration=0, _pause=False)

    def _handle_click_drag(self) -> str:
        """
        State machine:
          IDLE     → COUNTING
          COUNTING → click fired on release (short tap)
          COUNTING → DRAGGING after drag_hold_frames (long hold)
          DRAGGING → mouse held, moves with cursor
        """
        if self._drag_state == _DragState.IDLE:
            self._drag_state = _DragState.COUNTING
            self._drag_count = 1
            return "CLICK"

        elif self._drag_state == _DragState.COUNTING:
            self._drag_count += 1
            if self._drag_count >= self.gc.drag_hold_frames:
                pyautogui.mouseDown(button="left", _pause=False)
                self._drag_state = _DragState.DRAGGING
                logger.debug("Drag started.")
                return "DRAG START"
            return "CLICK HOLD"

        elif self._drag_state == _DragState.DRAGGING:
            return "DRAGGING"

        return "CLICK"

    def _reset_drag(self) -> None:
        if self._drag_state == _DragState.COUNTING:
            # Short hold = click
            if self._left_cooldown == 0:
                pyautogui.click(_pause=False)
                self._left_cooldown = self.gc.pinch_cooldown_frames
                logger.debug("Left click.")
        elif self._drag_state == _DragState.DRAGGING:
            pyautogui.mouseUp(button="left", _pause=False)
            logger.debug("Drag released.")
        self._drag_state = _DragState.IDLE
        self._drag_count = 0

    def _scroll(self, dy: float) -> str:
        if self._scroll_cooldown > 0:
            return "SCROLL"
        if abs(dy) > self.gc.scroll_threshold:
            d = 1 if dy > 0 else -1
            pyautogui.scroll(d * self.gc.scroll_speed, _pause=False)
            self._scroll_cooldown = self.gc.scroll_cooldown_frames
            return f"SCROLL {'UP' if d>0 else 'DOWN'}"
        return "SCROLL READY"
