# 🛰 GestureLink: Professional AI Gesture Suite

[![Version](https://img.shields.io/badge/version-3.0.0-blue.svg)](https://github.com/tushar05g/GestureLink)
[![Latency](https://img.shields.io/badge/latency-near--zero-brightgreen.svg)](https://github.com/tushar05g/GestureLink)
[![Engine](https://img.shields.io/badge/engine-WebRTC-orange.svg)](https://github.com/tushar05g/GestureLink)

GestureLink transforms your computer into a futuristic, touchless command center. By combining **MediaPipe AI** with a high-speed **WebRTC streaming engine**, GestureLink delivers near-zero latency control from any device, anywhere in the world.

---

## 🚀 What's New in v3.0 (Low-Latency Update)

*   **⚡ WebRTC Integration**: Replaced legacy MJPEG/WebSockets with high-performance WebRTC video and data tracks. Experience sub-100ms latency for both video and mouse control.
*   **🌐 Global Tunneling**: Integrated Cloudflare Tunnels (and ngrok fallback) to allow secure remote control over the internet without port forwarding.
*   **📱 Smart Cloud Console**: The mobile controller is now hosted on Vercel, allowing you to connect to your PC simply by scanning a QR code—no local IP typing required.
*   **🔐 Seamless Auto-Pairing**: QR codes now embed your secure 6-digit PIN, allowing for instant, one-tap authentication.

---

## ✨ Key Features

### 🖐 AI Vision Engine
*   **Adaptive Cursor**: Silky smooth movement using **One Euro Filter** adaptive smoothing.
*   **Gesture Recognition**: Custom hand poses (Pinch, V-Sign, Rock) mapped to mouse clicks and system shortcuts.
*   **Low-Light Optimized**: Enhanced detection logic for reliable control in varied lighting conditions.

### 📱 Professional Mobile Remote
*   **Multi-Touch Precision**: Supports two-finger scrolling, pinch-to-zoom, and native-feel dragging.
*   **Haptic Feedback**: Real-time physical response on your phone for every click and gesture.
*   **Integrated Keyboard**: Full keyboard and clipboard sync between your phone and PC.

### 🏢 Enterprise Deployment
*   **Hub & Agent Architecture**: Control multiple remote PCs (Agents) from a single master Hub.
*   **Windows Installer**: Distributed as a professional `.exe` package built with PyInstaller and Inno Setup.

---

## 🛠 Technology Stack

*   **Core Engine:** Python 3.11 with FastAPI & `aiortc`.
*   **AI Framework:** Google MediaPipe Vision (Hand Landmarker).
*   **Network Layer:** WebRTC (UDP-based) + Cloudflare Tunnels + Zeroconf mDNS.
*   **Frontend:** TypeScript, Vanilla CSS, and Vite (hosted on Vercel).
*   **Build System:** PyInstaller & Inno Setup 6.

---

## 🎮 Gesture Guide

| Action | Hand Gesture | Mobile Touchpad |
| :--- | :--- | :--- |
| **Move Cursor** | Index Finger Only | Single Finger Slide |
| **Left Click** | V-Sign (Index + Middle) | Single Tap |
| **Right Click** | Rock Sign (Index + Pinky) | Two-Finger Tap |
| **Scroll** | Three Fingers Up | Two-Finger Slide |
| **Zoom** | Finger Pinch | Pinch In/Out |
| **Drag & Drop** | Hold V-Sign (0.3s) | Long-Press + Drag |

---

## 🛡 Security
*   **P2P Encryption**: All WebRTC traffic is encrypted end-to-end.
*   **PIN Authentication**: Secure 6-digit handshake for all new device pairings.
*   **Local Privacy**: Camera data is processed locally; no video is ever sent to the cloud.

---

*Developed with ❤️ for the future of Human-Computer Interaction.*
