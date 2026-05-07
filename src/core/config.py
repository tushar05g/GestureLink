"""
config.py — Centralised configuration for Gesture Control.
Tune all sensitivity and threshold values here without touching logic files.
"""
from __future__ import annotations
from pydantic import BaseModel, Field
# Default desktop size; mouse controller uses pyautogui for actual movement.
SCREEN_W, SCREEN_H = 1920, 1080


class CameraConfig(BaseModel):
    camera_index: int = Field(0, description="Webcam index (0 = default camera).")
    frame_width: int = Field(640, description="Capture width in pixels.")
    frame_height: int = Field(480, description="Capture height in pixels.")
    fps: int = Field(30, description="Target capture FPS.")


class GestureConfig(BaseModel):
    # --- Mouse Movement ---
    # Only the centre region of the camera maps to the full screen
    # (margins reduce jitter at edges)
    frame_margin: float = Field(
        0.15, description="Fractional margin to ignore at each edge (0.0–0.4)."
    )
    smoothing: float = Field(
        0.15, description="EMA smoothing alpha for cursor (lower = more lag)."
    )
    movement_factor: float = Field(
        2.5, description="Multiplier for hand movement vs screen cursor."
    )
    move_threshold_px: float = Field(
        2.0,
        description="Minimum pixel delta before moving cursor (reduces micro-jitter).",
    )

    # --- Cursor mode — thumb-based gestures ---
    # Thumb extension: tip far from index MCP = thumb is out
    thumb_extend_threshold: float = Field(
        0.07, description="Min thumb-tip to index-MCP dist for thumb-pointing gesture."
    )
    # Thumb+Index touch = left click
    thumb_index_click: float = Field(
        0.1, description="Max thumb-index tip dist for left click."
    )
    # Thumb+Middle touch = right click
    thumb_middle_click: float = Field(
        0.1, description="Max thumb-middle tip dist for right click."
    )

    # --- Pinch / Click (kept for builder thumb-pinch move) ---
    pinch_threshold: float = Field(
        0.1,
        description=(
            "Normalised thumb-index distance to trigger a click (0.0–1.0). "
            "Hair-trigger: 0.045. Relaxed: 0.06."
        ),
    )
    pinch_cooldown_frames: int = Field(
        12,
        description="Frames to wait after a click before allowing another (debounce).",
    )

    # --- Drag ---
    drag_hold_frames: int = Field(
        8,
        description="Frames pinch must be held continuously before drag starts.",
    )

    # --- Scroll ---
    # Two fingers (index + middle) extended, wrist moves up/down
    scroll_threshold: float = Field(
        0.02,
        description="Minimum normalised vertical delta to trigger a scroll tick.",
    )
    scroll_speed: int = Field(
        12, description="pyautogui scroll units per tick (positive = up)."
    )
    scroll_cooldown_frames: int = Field(
        2, description="Frames between scroll ticks (controls scroll speed)."
    )


class OverlayConfig(BaseModel):
    show_overlay: bool = Field(True, description="Show OpenCV debug overlay window.")
    window_title: str = "Gesture Control — Overlay"
    # Neon green for active gestures, white for idle
    color_active: tuple[int, int, int] = (0, 255, 180)
    color_idle: tuple[int, int, int] = (200, 200, 200)
    color_pinch: tuple[int, int, int] = (0, 180, 255)


class ShortcutConfig(BaseModel):
    hold_frames: int = Field(
        45, description="Frames a gesture must be held to trigger its shortcut."
    )
    cooldown_frames: int = Field(25, description="Frames to wait before allowing the next shortcut launch.")


class OpenGLConfig(BaseModel):
    fov: float = Field(45.0, description="Field of view in degrees.")
    near_clip: float = Field(0.1, description="Near clipping plane.")
    far_clip: float = Field(500.0, description="Far clipping plane.")
    cam_distance: float = Field(15.0, description="Initial camera distance from origin.")
    cam_distance_min: float = Field(3.0, description="Minimum zoom distance.")
    cam_distance_max: float = Field(60.0, description="Maximum zoom distance.")
    rotate_sensitivity: float = Field(0.5, description="Degrees per normalised unit of hand movement.")
    zoom_sensitivity: float = Field(8.0, description="Zoom units per normalised pinch delta.")
    # Webcam PIP corner size
    pip_w: int = Field(240, description="Width of webcam picture-in-picture.")
    pip_h: int = Field(180, description="Height of webcam PIP.")
    # Fist detection threshold
    fist_threshold: float = Field(0.06, description="Max avg fingertip-to-palm dist to detect fist.")
    # Thumb+index pinch for zoom
    zoom_pinch_threshold: float = Field(0.08, description="Thumb-index dist considered 'touching' for zoom.")


class CubeConfig(BaseModel):
    grid_size: int = Field(40, description="Pixel size of each cube face.")
    num_layers: int = Field(7, description="Number of depth layers (0=nearest).")

    # Neon cyan palette — brightness drops with depth
    # Each entry is (B, G, R) for OpenCV
    layer_colors: list[tuple[int,int,int]] = [
        (255, 255,   0),   # layer 0 — nearest, brightest cyan
        (220, 220,   0),
        (185, 185,   0),
        (150, 150,   0),   # layer 3 — mid
        (115, 115,   0),
        ( 80,  80,   0),
        ( 50,  50,   0),   # layer 6 — farthest, darkest
    ]
    ghost_alpha: float = Field(0.35, description="Opacity of ghost cube preview.")
    selected_color: tuple[int,int,int] = (0, 255, 255)   # bright cyan for selected
    erase_color:    tuple[int,int,int] = (0,   0, 255)   # red for erase brush

    # Isometric projection offsets per depth layer (px shift right+up per layer)
    iso_offset_x: int = Field(6,  description="X offset per depth layer.")
    iso_offset_y: int = Field(4,  description="Y offset per depth layer.")

    # Toggle button geometry (left side of overlay)
    toggle_btn_x: int = Field(10,  description="Left edge of toggle button.")
    toggle_btn_y: int = Field(120, description="Top edge of toggle button.")
    toggle_btn_w: int = Field(130, description="Width of toggle button.")
    toggle_btn_h: int = Field(60,  description="Height of toggle button.")

    # Paint / erase
    paint_hold_frames: int = Field(3, description="Frames index+middle must be held before painting starts.")
    max_undo: int = Field(20, description="Maximum undo history length.")


class AppConfig(BaseModel):
    screen_w: int = Field(default_factory=lambda: SCREEN_W)
    screen_h: int = Field(default_factory=lambda: SCREEN_H)
    camera: CameraConfig = Field(default_factory=CameraConfig)
    gesture: GestureConfig = Field(default_factory=GestureConfig)
    overlay: OverlayConfig = Field(default_factory=OverlayConfig)
    shortcuts: ShortcutConfig = Field(default_factory=ShortcutConfig)
    cube: CubeConfig = Field(default_factory=CubeConfig)
    opengl: OpenGLConfig = Field(default_factory=OpenGLConfig)


# Singleton
CONFIG = AppConfig()
