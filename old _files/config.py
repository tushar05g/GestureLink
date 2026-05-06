"""
config.py — All tuneable constants for Gesture Control.
"""
from __future__ import annotations
from pydantic import BaseModel, Field
import pyautogui

SCREEN_W, SCREEN_H = pyautogui.size()


class CameraConfig(BaseModel):
    camera_index: int  = Field(0,   description="Webcam index.")
    frame_width:  int  = Field(640, description="Capture width.")
    frame_height: int  = Field(480, description="Capture height.")
    fps:          int  = Field(30,  description="Target FPS.")


class GestureConfig(BaseModel):
    # --- Cursor mode (thumb-based) ---
    thumb_extend_threshold: float = Field(0.10, description="Min thumb-MCP dist for THUMB_MOVE.")
    thumb_index_click:      float = Field(0.05, description="Max thumb-index dist for left click.")
    thumb_middle_click:     float = Field(0.05, description="Max thumb-middle dist for right click.")

    # --- Smoothing / movement ---
    frame_margin:     float = Field(0.15, description="Dead zone fraction at each edge.")
    smoothing:        float = Field(0.25, description="Cursor smoothing factor.")
    move_threshold_px: float = Field(4.0, description="Min px delta before moving cursor.")

    # --- Click debounce ---
    pinch_cooldown_frames: int = Field(12, description="Frames between clicks.")

    # --- Scroll ---
    scroll_threshold:      float = Field(0.008, description="Min vertical delta for scroll tick.")
    scroll_speed:          int   = Field(5,     description="Scroll units per tick.")
    scroll_cooldown_frames: int  = Field(2,     description="Frames between scroll ticks.")

    # --- Builder thumb-pinch (move cubes) ---
    pinch_threshold: float = Field(0.045, description="Thumb-index dist for THUMB_PINCH.")

    # --- Builder paint ---
    paint_hold_frames: int = Field(3, description="Frames index+middle held before painting.")

    # --- Mode switch ---
    pinky_hold_required: int = Field(20, description="Frames pinky held to switch mode.")


class OverlayConfig(BaseModel):
    window_title: str                  = "Gesture Control"
    color_active: tuple[int,int,int]   = (0, 255, 180)
    color_idle:   tuple[int,int,int]   = (200, 200, 200)


class CubeConfig(BaseModel):
    grid_size:   int = Field(40, description="Pixel size per cube face.")
    num_layers:  int = Field(7,  description="Depth layers (0=nearest).")

    # Neon cyan BGR — brightness drops with depth (B=255,G=255,R=0 = cyan)
    layer_colors: list[tuple[int,int,int]] = [
        (255, 255, 0),   # layer 0 — nearest, brightest
        (220, 220, 0),
        (185, 185, 0),
        (150, 150, 0),   # layer 3 — mid
        (115, 115, 0),
        ( 80,  80, 0),
        ( 50,  50, 0),   # layer 6 — farthest, darkest
    ]
    ghost_alpha:    float            = Field(0.35)
    selected_color: tuple[int,int,int] = (0, 255, 255)
    erase_color:    tuple[int,int,int] = (0,   0, 255)

    iso_offset_x: int = Field(6, description="X px shift per depth layer.")
    iso_offset_y: int = Field(4, description="Y px shift per depth layer.")

    toggle_btn_x: int = Field(10,  description="Toggle button left edge.")
    toggle_btn_y: int = Field(120, description="Toggle button top edge.")
    toggle_btn_w: int = Field(130, description="Toggle button width.")
    toggle_btn_h: int = Field(60,  description="Toggle button height.")

    max_undo: int = Field(20, description="Max undo steps.")


class AppConfig(BaseModel):
    screen_w: int = Field(default_factory=lambda: SCREEN_W)
    screen_h: int = Field(default_factory=lambda: SCREEN_H)
    camera:   CameraConfig  = Field(default_factory=CameraConfig)
    gesture:  GestureConfig = Field(default_factory=GestureConfig)
    overlay:  OverlayConfig = Field(default_factory=OverlayConfig)
    cube:     CubeConfig    = Field(default_factory=CubeConfig)


CONFIG = AppConfig()
