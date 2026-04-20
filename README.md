# ✦ Gesture Control — Productivity Edition

> Control your Windows PC with hand gestures via your webcam. No special hardware needed.

---

## Gesture Reference

| Hand Shape | Action |
|---|---|
| ☝️ Index finger only, pointing | **Move cursor** — tip position maps to screen |
| 🤏 Pinch (thumb + index close) | **Left click** |
| 🤏 Pinch + hold 8 frames | **Click & drag** — release pinch to drop |
| ✌️ Index + middle up, move hand up/down | **Scroll** |

---

## File Structure

```
gesture_control/
├── src/
│   ├── __init__.py
│   ├── config.py       # All thresholds & sensitivity (tune here)
│   ├── vision.py       # MediaPipe hand tracking + gesture classifier
│   ├── controller.py   # PyAutoGUI mouse/scroll actions
│   └── app.py          # Main loop + OpenCV overlay
├── run.py              # Entry point
├── requirements.txt
└── README.md
```

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Run
```bash
python run.py
```

An overlay window will open showing your webcam feed with landmarks.
Press **Q** or **Escape** to quit.

---

## How It Works

```
Webcam frame (OpenCV)
      │
      ▼
VisionProcessor (MediaPipe)
  → 21 hand landmarks detected
  → Gesture classified (POINTING / PINCH / SCROLL / IDLE)
      │
      ▼
MouseController (PyAutoGUI)
  → POINTING  : smoothed cursor movement
  → PINCH     : click or drag (state machine)
  → SCROLL    : scroll wheel tick
      │
      ▼
OpenCV Overlay
  → Landmarks + gesture status + FPS displayed
```

---

## Tuning Guide (`src/config.py`)

| Setting | Default | What it does |
|---|---|---|
| `gesture.pinch_threshold` | `0.045` | Lower = harder to click accidentally. Raise if clicks don't fire. |
| `gesture.smoothing` | `0.25` | Lower = smoother cursor, slightly laggier. |
| `gesture.frame_margin` | `0.15` | Increase if cursor hits edges too easily. |
| `gesture.drag_hold_frames` | `8` | Frames of pinch before drag starts. |
| `gesture.scroll_speed` | `3` | Scroll units per tick. |
| `gesture.scroll_threshold` | `0.03` | Min hand movement to trigger scroll tick. |

---

## Tips

- **Good lighting** makes a big difference for MediaPipe accuracy.
- Keep your hand **30–60 cm** from the camera.
- The overlay window is a **mirror** — move right to go right on screen.
- The **frame margin** (outer 15% of camera view) is dead zone — helps prevent cursor from getting stuck at screen edges.

---

## Coming Next (Phase 2 — Creativity)
- Air drawing with index finger
- Peace sign ✌️ → Screenshot
- Fist → Undo (Ctrl+Z)
- Open palm → Show desktop
