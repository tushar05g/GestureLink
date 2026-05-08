# 🛰 GestureLink: The Unified AI Gesture Suite

[![Version](https://img.shields.io/badge/version-2.1.0-brightgreen.svg)](https://github.com/tushar05g/GestureLink)
[![Platform](https://img.shields.io/badge/platform-windows-blue.svg)](https://github.com/tushar05g/GestureLink)
[![License](https://img.shields.io/badge/license-MIT-orange.svg)](LICENSE)

GestureLink transforms your computer into a futuristic, touchless command center. Using only your hand movements or your mobile phone as a high-precision remote trackpad, you can control multiple computers with professional-grade accuracy.

---

## ✨ Key Features

### 🖐 High-Precision AI Vision
*   **Adaptive Cursor**: Silky smooth movement using **One Euro Filter** adaptive smoothing.
*   **Gesture Recognition**: Custom hand poses (Pinch, V-Sign, Rock) for mouse actions.
*   **ROI Mapping**: Intelligent "Safe Zone" detection for ergonomic hand movement.

### 📱 Mobile Remote Console
*   **Zero-Install PWA**: Scan a QR code to instantly turn your phone into a controller.
*   **Multi-Touch Precision**: Two-finger scroll, right-click tap, and **Pinch-to-Zoom**.
*   **Remote Sensitivity**: Tune your cursor speed and smoothing directly from your phone.

### 🌐 Multi-PC Ecosystem
*   **Hub & Agent**: Control your main workstation and remote laptops from one interface.
*   **Auto-Discovery**: Zeroconf (mDNS) protocol finds your devices instantly on Wi-Fi.
*   **Encrypted Relay**: Secure WebSocket (WSS) tunneling for low-latency remote control.

---

## 🚀 Installation & Updates

GestureLink is now distributed as a professional Windows package.

1.  **Download the Installer**: Run `GestureLink_Setup.exe`.
2.  **Seamless Updates**: The installer automatically detects running instances, closes them gracefully, and restarts them after the update.
3.  **Firewall Auto-Fix**: One-click setup for Windows Firewall rules to allow mobile connections.

---

## 🛠 Technology Stack

GestureLink is built with a focus on performance and reliability:

*   **Core:** Python 3.10+ with FastAPI & Uvicorn.
*   **AI Vision:** MediaPipe Hand Landmarker (Dual-model pipeline).
*   **Input:** PyAutoGUI with sub-pixel precision accumulators.
*   **Networking:** Zeroconf (RFC 6762/6763) & Secure WebSockets.
*   **Installer:** Inno Setup with Windows Restart Manager integration.

> [!TIP]
> For a deep-dive into the engineering behind GestureLink, check out our [Technical Documentation](TECHNICAL_DETAILS.md).

---

## 🎮 Gestures Map

| Action | Hand Gesture | Mobile Touchpad |
| :--- | :--- | :--- |
| **Move Cursor** | Index Finger Only | Single Finger Slide |
| **Left Click** | Index + Middle (V-Sign) | Single Tap |
| **Right Click** | Rock Sign (Index + Pinky) | Two-Finger Tap |
| **Scroll** | Three Fingers Up | Two-Finger Slide |
| **Drag & Drop** | Hold V-Sign (0.3s) | Long-Press + Drag |
| **Shortcuts** | Pinky Hold (1s) | 3/4-Finger Tap |

---

## 🔒 Security & Privacy

*   **6-Digit PIN Pairing**: Prevents unauthorized devices from controlling your PC.
*   **Manual Consent**: Hub users must approve any new connection request.
*   **Local Processing**: No video data ever leaves your local network.

---

*Developed with ❤️ by the GestureLink Team.*
