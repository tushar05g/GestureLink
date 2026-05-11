# 🚀 GestureLink Deployment Guide

This guide explains how to deploy GestureLink so you can control your PC from anywhere in the world.

---

## 📱 Phase 1: Mobile Frontend (Vercel)

The mobile app is hosted on Vercel. This acts as the "remote control" in your pocket.

### 1. Vercel Project Settings
In the **Vercel Dashboard**, ensure your settings match these exactly:
- **Framework Preset**: `Vite`
- **Root Directory**: `src/web/mobile`
- **Node.js Version**: `22.x` (Important!)
- **Build Command**: `npm run build`
- **Output Directory**: `dist`

### 2. Required Files (Already created for you)
Make sure these files are pushed to your GitHub:
- `src/web/mobile/package.json` (Updated with TS 5.7+)
- `src/web/mobile/.node-version` (Forced to Node 22)
- `src/web/mobile/vercel.json` (Routing & Framework config)

---

## 💻 Phase 2: Hub Server (Your Local PC)

The Hub is the "brain" that lives on your computer. It must be running for the mobile app to work.

### 1. Choose your Tunnel (Remote Access)

#### Option A: Cloudflare Tunnel (Recommended - FREE & Faster)
1. Download `cloudflared` from [Cloudflare Releases](https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.msi).
2. Install it.
3. GestureLink will **automatically** create a secure `trycloudflare.com` tunnel every time it starts.

#### Option B: ngrok (Fallback)
1. Get your token for free at [ngrok.com](https://dashboard.ngrok.com/get-started/your-authtoken).
2. Add it to your `.env` file:
```env
NGROK_AUTH_TOKEN=your_token_here
HUB_NAME=My Awesome PC
FRONTEND_URL=https://your-vercel-app.vercel.app
```

### 2. Launch the Hub
Run the following command in your terminal:
```bash
python -m src.hub.server
```
- **What happens?** 
  - It detects your ngrok token.
  - It creates a **Public Tunnel**.
  - It **Automatically opens your browser** to the dashboard.
  - It **Generates a QR Code** on the screen.

---

## 🔗 Phase 3: Connecting Everything

1. **Open the Hub Dashboard** on your PC (it should open automatically when you run the server).
2. **Scan the QR Code** using your phone.
3. **Enjoy!** Your phone will now use the Vercel app to talk to your PC over the internet tunnel.

---

## 🛠 Troubleshooting

### "No Output Directory" on Vercel
- Check that your **Root Directory** is set to `src/web/mobile`.
- Ensure you pushed the `vercel.json` file I just created.

### "Command not found" on Vercel
- Ensure your `package.json` uses the correct versions:
  - `typescript: ^5.7.3`
  - `vite: ^8.0.12`

### Slow Connection
- The system will automatically try to switch to **Local LAN mode** if your phone and PC are on the same Wi-Fi for zero-latency control.