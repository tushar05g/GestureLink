# 🚀 GestureLink Professional Deployment Guide

Follow these 5 steps to take GestureLink from "Local" to "Global."

### 1. Host the Frontend (The "App" URL)
*   **Platform**: [Vercel](https://vercel.com) or [Netlify](https://netlify.com).
*   **Action**: Connect your GitHub repo. Set the "Build Command" to `npm run build` and "Output Directory" to `src/web/mobile/dist`.
*   **Result**: You get a dedicated URL like `https://gesturelink-app.vercel.app`.

### 2. Set Up the Tunnel (The "API" URL)
*   **Service**: [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started-for-free/create-a-remote-tunnel/).
*   **Action**: Buy a domain (e.g., `gesturelink.com`) on Cloudflare. Install `cloudflared` on your PC and map `api.gesturelink.com` to `localhost:8000`.
*   **Result**: Your Hub PC is now securely online with a permanent address.

### 3. Add a TURN Server (The "Lag-Killer")
*   **Service**: [Metered.ca](https://www.metered.ca/stun-turn) (Free tier available) or [Twilio](https://www.twilio.com/stun-turn).
*   **Action**: Get your `Username`, `Credential`, and `URL`. 
*   **Update**: Put these into your `.env` file on your Hub PC. The updated GestureLink code will automatically pick them up and share them with your phone.

### 4. Update Environment Variables
Add these to your `.env` on your PC:
```env
# Cloudflare/Ngrok URL
NGROK_URL=https://api.gesturelink.com

# TURN Server Config
TURN_URL=turn:your-turn-provider.com:443
TURN_USERNAME=your_username
TURN_PASSWORD=your_password
```

### 5. Deployment Complete!
Now, when you scan the QR code, your phone will:
1. Load the UI from Vercel (Fast).
2. Connect to your PC via Cloudflare (Stable).
3. Use your TURN server to find the shortest path (Zero-Lag).


## App deployment
Use electron to make cross-platform app that can run on desktop and mobile.