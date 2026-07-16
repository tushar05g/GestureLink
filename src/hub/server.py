from __future__ import annotations
import subprocess

import asyncio
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from contextlib import asynccontextmanager
import sys
import os

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import io
import json
import logging
import platform
from pathlib import Path
from typing import Dict, Optional, Annotated

from dotenv import load_dotenv
from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import qrcode
import socket
import websockets

from src.core.utils import resource_path
from aiortc import RTCPeerConnection, RTCSessionDescription

load_dotenv()
logger = logging.getLogger("gesture_control.remote")


class EndpointFilter(logging.Filter):
    """Silences aggressive polling logs for specific endpoints."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        silence = [
            "/api/security/pending",
            "/api/connected-clients",
            "/api/discovered"
        ]
        return not any(path in msg for path in silence)


# Camera loop liveness flags (used by _hub_camera_loop and /api/hub/camera/status)
hub_video_frame: bytes | None = None
hub_camera_active: bool = False

APP_DIR = Path(__file__).resolve().parent
HUB_DIR = APP_DIR

# Use resource_path() so these resolve correctly inside a PyInstaller .exe
HUB_HTML = resource_path("src/web/hub/hub.html")
MOBILE_DIST = resource_path("src/web/mobile/dist")
SETTINGS_FILE = HUB_DIR / "settings.json"
SECURITY_FILE = HUB_DIR / "security.json"
CERT_PEM = resource_path("cert.pem")
KEY_PEM = resource_path("key.pem")


def _save_settings(sensitivity: int, scroll_speed: int, trackpad_sensitivity: float = 1.5) -> None:
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump({
                "sensitivity": sensitivity,
                "scroll_speed": scroll_speed,
                "trackpad_sensitivity": trackpad_sensitivity
            }, f)
    except Exception as e:
        logger.error("Failed to save settings: %s", e)


def _load_settings() -> None:
    if SETTINGS_FILE.exists():
        try:
            # Issue: CONFIG is used here but imported inside build_app
            from src.core.config import CONFIG
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
                sens = data.get("sensitivity", 50)
                scroll = data.get("scroll_speed", 20)
                tp_sens = data.get("trackpad_sensitivity", 1.5)

                # Apply vision settings
                alpha = 0.05 + (sens - 5) / 90.0 * 0.45
                thresh = 8.0 - (sens - 5) / 90.0 * 7.0
                CONFIG.gesture.smoothing = alpha
                CONFIG.gesture.move_threshold_px = max(0.5, thresh)

                # Apply trackpad/scroll settings
                CONFIG.gesture.scroll_speed = int(scroll)
                CONFIG.gesture.trackpad_sensitivity = float(tp_sens)
        except Exception as e:
            logger.error("Failed to load settings: %s", e)


def build_app(host: str = "0.0.0.0", port: int = 8000) -> FastAPI:
    from src.core.config import CONFIG
    from src.core.controller import MouseController
    from src.core.shortcuts import ShortcutManager
    from src.core.vision import VisionProcessor
    from src.hub.managers import SecurityManager, TokenManager, DeviceDiscovery, detect_lan_ip
    from src.core.vision_worker import AsyncVisionWorker
    from src.core.modes import MeetPaintController, BuilderController

    def _open_dashboard():
        import webbrowser
        import subprocess
        import os
        import time
        import ssl
        import urllib.request

        proto = "https" if CERT_PEM.exists() else "http"
        local_url = f"{proto}://localhost:{port}/hub"

        # HUB_URL is ONLY set by users who intentionally configured a custom domain.
        # Regular users installing via Inno Setup will NEVER have this — they get
        # localhost which is instant, requires zero config, and never fails.
        hub_url = os.getenv("HUB_URL")

        if hub_url:
            # ── CUSTOM DOMAIN ────────────────────────────────────────────────
            # User has their own domain pointed at this tunnel.
            # Probe the health endpoint until cloudflared connects, then open.
            target_url = hub_url.rstrip("/") + "/hub"
            health_url = hub_url.rstrip("/") + "/health"

            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            print(f"  * Custom domain detected — waiting for tunnel ({health_url})...")
            for attempt in range(40):           # probe every 0.5s, up to 20s
                try:
                    urllib.request.urlopen(health_url, timeout=2, context=ctx)
                    print(f"  * Tunnel live ({attempt * 0.5:.1f}s) — opening dashboard.")
                    break
                except Exception:
                    time.sleep(0.5)
            # Open even if timed out — tunnel may still be warming up
        else:
            # ── DEFAULT (Inno Setup installs / no config) ────────────────────
            # Quick Tunnels (random trycloudflare.com URLs) take 10-30s to
            # propagate through Cloudflare's edge — opening them immediately
            # causes Error 1033.  The phone scans the QR code on the dashboard
            # to get that URL.  The PC dashboard itself is always localhost.
            target_url = local_url
            time.sleep(1.0)     # 1s — just enough for uvicorn to be serving

        # Open Chrome/Edge in App Mode for a clean borderless window
        app_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
        ]
        for path in app_paths:
            if os.path.exists(path):
                try:
                    subprocess.Popen([path, f"--app={target_url}"])
                    return
                except Exception:

                    pass

        webbrowser.open(target_url)     # fallback to system default browser

    # --- LIFESPAN HANDLER (Startup/Shutdown) ---
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # --- Hub State Initialization (MUST BE FIRST) ---
        app.state.cloudflare_url = None
        app.state.cf_proc = None
        app.state.friendly_name = platform.node()

        _load_settings()

        # Load Friendly Name from config
        config_path = os.path.join(os.path.dirname(__file__), "hub_config.json")
        if os.path.exists(config_path):
            try:
                import json
                with open(config_path, "r") as f:
                    app.state.friendly_name = json.load(f).get(
                        "friendly_name", app.state.friendly_name)
            except Exception:

                pass

        # --- WebRTC SIGNALING LISTENER (For Remote/Tunnel) ---
        async def _signaling_listener():
            await asyncio.sleep(2)  # Wait for tunnel to stabilize
            # Use 'hub_pc' as the primary mailbox to match mobile UI expectation
            target_id = "hub_pc"
            logger.info(f"WebRTC Signaling Listener active. Polling mailbox: '{target_id}'")
            while True:
                try:
                    if target_id in signals:
                        payload = await signals[target_id]["q"].get()
                        if payload.get("type") == "offer":
                            logger.info(">>> Received Remote Offer via 'hub_pc'!")
                            offer = RTCSessionDescription(sdp=payload["sdp"], type=payload["type"])
                            reply_target = payload.get("from") or "mobile_client"

                            from aiortc import RTCConfiguration, RTCIceServer
                            pc = RTCPeerConnection(configuration=RTCConfiguration(
                                iceServers=[
                                    RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
                                    RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
                                    RTCIceServer(urls=["stun:stun2.l.google.com:19302"]),
                                    RTCIceServer(
                                        urls=["turn:numb.viagenie.ca"],
                                        username="webrtc@example.com",
                                        credential="webrtcpassword"
                                    )
                                ]
                            ))
                            setup_pc(pc)

                            await pc.setRemoteDescription(offer)
                            answer = await pc.createAnswer()
                            await pc.setLocalDescription(answer)

                            # Send answer back to wherever the phone is listening
                            # Typically mobile apps listen on their own unique session ID or 'mobile_client'
                            await webrtc_signal(reply_target, {
                                "sdp": pc.localDescription.sdp,
                                "type": pc.localDescription.type
                            })
                            logger.info(
                                "<<< Sent Remote Answer to '%s'. Handshake complete.", reply_target)

                    await asyncio.sleep(0.2)
                except Exception as e:
                    logger.error(f"Signaling listener error: {e}")
                    await asyncio.sleep(2)

        asyncio.create_task(_signaling_listener())

        # --- FIREBASE SIGNALING (Replaces Cloudflare) ---
        async def _firebase_signaling_loop():
            import httpx
            from aiortc import RTCConfiguration, RTCIceServer

            FIREBASE_URL = "https://gesturelink-5db9c-default-rtdb.firebaseio.com"
            logger.info("Firebase Signaling active. Waiting for mobile peer...")
            # Create client ONCE to reuse connection pool and avoid TLS handshake blocking the event loop
            async with httpx.AsyncClient() as client:
                while True:
                    pin = tokens.current_pin
                    if not pin or getattr(app.state, "firebase_answered_pin", None) == pin:
                        # If we already connected for this PIN, or no PIN, just sleep (don't poll Firebase)
                        await asyncio.sleep(2)
                        continue

                    try:
                        # Poll the mobile offer
                        res = await client.get(f"{FIREBASE_URL}/sessions/{pin}/mobile.json")
                        data = res.json()

                        if data and "offer" in data and not getattr(app.state, "firebase_answered_pin", None) == pin:
                            logger.info(f">>> Received Remote Offer via Firebase (PIN {pin})!")
                            offer_data = data["offer"]
                            offer = RTCSessionDescription(
                                sdp=offer_data["sdp"], type=offer_data["type"])

                            pc = RTCPeerConnection(configuration=RTCConfiguration(
                                iceServers=[
                                    RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
                                    RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
                                    RTCIceServer(urls=["stun:stun2.l.google.com:19302"]),
                                    RTCIceServer(
                                        urls=["turn:global.relay.metered.ca:80"],
                                        username="d120fb319dff30d8d011f0cf",  # Temporary public Metered credential
                                        credential="W0oY1T/dC+8v3hQf"
                                    ),
                                    RTCIceServer(
                                        urls=["turn:global.relay.metered.ca:443"],
                                        username="d120fb319dff30d8d011f0cf",
                                        credential="W0oY1T/dC+8v3hQf"
                                    )
                                ]
                            ))
                            setup_pc(pc)

                            await pc.setRemoteDescription(offer)
                            answer = await pc.createAnswer()
                            await pc.setLocalDescription(answer)

                            # Wait up to 3 seconds for ICE candidates to gather
                            # aiortc doesn't trickle easily, so we gather all first
                            for _ in range(30):
                                if pc.iceGatheringState == "complete":
                                    break
                                await asyncio.sleep(0.1)

                            # Write answer back to Firebase
                            ans_payload = {
                                "sdp": pc.localDescription.sdp,
                                "type": pc.localDescription.type
                            }
                            await client.put(f"{FIREBASE_URL}/sessions/{pin}/hub/answer.json", json=ans_payload)
                            logger.info("<<< Sent Remote Answer via Firebase. Handshake complete.")
                            app.state.firebase_answered_pin = pin

                        # If a candidate is sent by mobile
                        if data and "candidates" in data:
                            for idx, cand in data["candidates"].items():
                                # In a real implementation we add ICE candidates dynamically,
                                # but aiortc handles them via SDP or addIceCandidate.
                                pass

                    except Exception as e:
                        logger.error(f"Firebase signaling error: {e}")

                    await asyncio.sleep(1.5)

        app.state.firebase_task = asyncio.create_task(_firebase_signaling_loop())

        discovery.start()
        # Removed Cloudflare wait loop

        lan_ip = detect_lan_ip()
        proto = "https" if CERT_PEM.exists() else "http"
        print("\n" + "="*50)
        print("STARTING GESTURELINK HUB...")
        print(f"  * Local Dashboard:  {proto}://localhost:{port}/hub")
        print(f"  * Mobile Access:    {proto}://{lan_ip}:{port}")

        # Display Remote Tunnels
        if getattr(app.state, "cloudflare_url", None):
            print(f"  * Remote (Cloudflare): {app.state.cloudflare_url}")
        if getattr(app.state, "ngrok_url", None):
            print(f"  * Remote (ngrok):      {app.state.ngrok_url}")

        print(f"  * Pairing PIN:      {tokens.current_pin}")
        print("="*50 + "\n")
        logger.info("Hub Started successfully.")

        # Background tasks
        import threading
        threading.Thread(target=_open_dashboard, daemon=True).start()

        app.state.rotation_task = asyncio.create_task(_rotate_pin_periodically())
        app.state.cleanup_task = asyncio.create_task(_cleanup_signals_loop())

        # --- NGROK TUNNEL REMOVED ---

        yield

        # Shutdown
        # if app.state.ngrok_url:
        #     logger.info("Closing ngrok tunnel...")
        #     ngrok.disconnect(app.state.ngrok_url)
        #     ngrok.kill()

        # Cloudflare tunnel logic removed

        discovery.stop()
        vision_worker.stop()
        logger.info("Hub shutting down...")

    app = FastAPI(title="GestureLink Hub", version="1.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # State & Managers
    security = SecurityManager(SECURITY_FILE)
    tokens = TokenManager()
    discovery = DeviceDiscovery(port=port)
    vision_worker = AsyncVisionWorker(CONFIG)  # For remote mobile streams
    vision_worker.start()

    # Unified local processor for Hub's camera
    vision_processor = VisionProcessor(CONFIG)

    shortcuts = ShortcutManager()
    mouse = MouseController(CONFIG, shortcuts=shortcuts, responsive=True)

    # Store in app state for access from other endpoints
    app.state.vision = vision_worker
    app.state.vision_processor = vision_processor
    app.state.camera_active = False
    app.state.camera_task = None
    app.state.mouse = mouse
    app.state.active_mode = 0  # 0=Cursor, 1=MeetPaint, 2=Builder
    app.state.meet_paint = MeetPaintController(CONFIG)
    app.state.builder = BuilderController(CONFIG)

    # Shared state — track live WebSocket sessions for the dashboard
    connected_clients: Dict[str, dict] = {}
    active_hub_dashboards = 0

    # WebRTC Signaling Hub with Timestamp tracking for cleanup
    signals: Dict[str, dict] = {}  # {id: {"q": Queue, "last_poll": timestamp}}

    async def _cleanup_signals_loop():
        """B-02: Purge signaling queues for devices inactive for > 5 mins."""
        import time
        while True:
            await asyncio.sleep(60)
            now = time.time()
            stale = [tid for tid, data in signals.items() if now - data["last_poll"] > 300]
            for tid in stale:
                logger.info(f"Cleaning up stale WebRTC queue for {tid}")
                del signals[tid]

    @app.post("/api/webrtc/signal/{target_id}")
    async def webrtc_signal(target_id: str, payload: Annotated[dict, Body(...)]) -> JSONResponse:
        import time
        if target_id not in signals:
            signals[target_id] = {"q": asyncio.Queue(), "last_poll": time.time()}
        await signals[target_id]["q"].put(payload)
        return JSONResponse({"ok": True})

    @app.get("/api/webrtc/signal/{target_id}")
    async def webrtc_get_signals(target_id: str) -> JSONResponse:
        import time
        if target_id not in signals:
            signals[target_id] = {"q": asyncio.Queue(), "last_poll": time.time()}

        signals[target_id]["last_poll"] = time.time()
        try:
            signal = await asyncio.wait_for(signals[target_id]["q"].get(), timeout=30.0)
            return JSONResponse({"ok": True, "signal": signal})
        except asyncio.TimeoutError:
            return JSONResponse({"ok": False, "error": "timeout"}, status_code=200)

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "healthy", "version": "1.1.0"})

    @app.get("/api/ping")
    async def ping() -> JSONResponse:
        """Lightweight endpoint for network device discovery."""
        return JSONResponse({
            "ok": True,
            "hostname": socket.gethostname(),
            "ip": detect_lan_ip(),
            "service": "gesturelink-hub"  # APK uses this to identify Hub nodes on LAN
        })

    @app.post("/api/validate-token")
    async def validate_token_endpoint(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        """Let mobile verify its stored token is still valid after a server restart."""
        token = payload.get("token")
        valid = tokens.validate_token(token)
        return JSONResponse({"valid": valid})

    @app.post("/api/auth/verify")
    async def verify_auth(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        token = payload.get("token")
        if not token:
            app.state.is_premium = False
            return JSONResponse({"ok": True, "premium": False})

        try:
            import base64
            import json
            import httpx
            parts = token.split(".")
            if len(parts) == 3:
                padded_payload = parts[1] + '=' * (-len(parts[1]) % 4)
                decoded = base64.b64decode(padded_payload)
                user_info = json.loads(decoded)
                uid = user_info.get("user_id")

                if uid:
                    db_url = f"https://gesturelink-5db9c-default-rtdb.firebaseio.com/users/{uid}.json"
                    async with httpx.AsyncClient() as client:
                        resp = await client.get(db_url)
                        data = resp.json()
                        if data and data.get("is_premium"):
                            app.state.is_premium = True
                            return JSONResponse({"ok": True, "premium": True})

                # If not premium by UID, check if their email was upgraded via Stripe
                email = user_info.get("email")
                if email:
                    safe_email = base64.b64encode(email.encode('utf-8')).decode('utf-8')
                    email_url = f"https://gesturelink-5db9c-default-rtdb.firebaseio.com/premium_users/{safe_email}.json"
                    async with httpx.AsyncClient() as client:
                        resp = await client.get(email_url)
                        data = resp.json()
                        if data and data.get("isPremium"):
                            app.state.is_premium = True
                            return JSONResponse({"ok": True, "premium": True})

            app.state.is_premium = False
            return JSONResponse({"ok": True, "premium": False})
        except Exception as e:
            logger.error(f"Auth verify error: {e}")
            app.state.is_premium = False
            return JSONResponse({"ok": False, "error": str(e)})

    @app.post("/api/trial/start")
    async def start_trial(payload: dict = Body(...)):
        import time
        mode_id = payload.get("mode_id")
        if getattr(app.state, "is_premium", False):
            return JSONResponse({"status": "already_premium"})

        if mode_id == 1:
            if getattr(app.state, "trial_active_1", False):
                return JSONResponse({"status": "already_active"})
            app.state.trial_active_1 = True
            app.state.trial_start_time_1 = time.time()
            logger.info("1-minute free trial activated for Meet Paint (Mode 1).")
            return JSONResponse({"status": "success"})
        elif mode_id == 2:
            if getattr(app.state, "trial_active_2", False):
                return JSONResponse({"status": "already_active"})
            app.state.trial_active_2 = True
            app.state.trial_start_time_2 = time.time()
            logger.info("1-minute free trial activated for Builder (Mode 2).")
            return JSONResponse({"status": "success"})

        return JSONResponse({"status": "invalid_mode"})

    @app.get("/api/connected-clients")
    async def get_connected_clients() -> JSONResponse:
        return JSONResponse({"clients": list(connected_clients.values())})

    @app.post("/api/hub/webrtc-client/connect")
    async def api_hub_webrtc_connect(payload: dict) -> JSONResponse:
        client_id = payload.get("ip")
        if client_id:
            connected_clients[client_id] = {
                "ip": client_id,
                "connected_at": int(time.time()),
                "type": "mobile (webrtc)"
            }
        return JSONResponse({"ok": True})

    @app.post("/api/hub/webrtc-client/disconnect")
    async def api_hub_webrtc_disconnect(payload: dict) -> JSONResponse:
        client_id = payload.get("ip")
        if client_id:
            connected_clients.pop(client_id, None)
        return JSONResponse({"ok": True})

    @app.post("/api/hub/camera/flip")
    async def flip_hub_camera():
        if hasattr(app.state, "vision"):
            app.state.vision.mirror = not getattr(app.state.vision, "mirror", False)
            return JSONResponse({"ok": True, "mirror": app.state.vision.mirror})
        return JSONResponse({"ok": False}, status_code=404)

    @app.post("/api/hub/name")
    async def set_hub_name(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        name = payload.get("name", "")
        if name:
            app.state.friendly_name = name
            # Save to local config file for non-tech persistence
            config_path = os.path.join(os.path.dirname(__file__), "hub_config.json")
            try:
                import json
                with open(config_path, "w") as f:
                    json.dump({"friendly_name": name}, f)
            except Exception:

                pass
            logger.info(f"Hub renamed to: {name}")
            return JSONResponse({"ok": True})
        return JSONResponse({"ok": False, "error": "Invalid name"}, status_code=400)

    @app.get("/api/hub/stats")
    async def get_hub_stats():
        import shutil
        # Disk usage of C: drive (or root)
        path = "C:\\" if platform.system() == "Windows" else "/"
        try:
            usage = shutil.disk_usage(path)
            # Simple CPU fallback using wmic (Windows)
            cpu = 0
            if platform.system() == "Windows":
                try:
                    cmd = "wmic cpu get loadpercentage"
                    res = subprocess.check_output(cmd, shell=True, text=True)
                    cpu = int(res.splitlines()[1].strip())
                except Exception:

                    cpu = 5  # Placeholder if wmic fails

            return JSONResponse({
                "cpu": cpu,
                "storage_total": usage.total,
                "storage_free": usage.free,
                "storage_percent": round((usage.used / usage.total) * 100, 1)
            })
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    async def _get_network_profile() -> str:
        if platform.system() != "Windows":
            return "Unknown"
        try:
            cmd = "powershell -Command \"Get-NetConnectionProfile | Select-Object -ExpandProperty NetworkCategory\""
            proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await proc.communicate()
            return stdout.decode().strip() or "Unknown"
        except Exception:
            return "Unknown"

    @app.post("/api/security/fix-firewall")
    async def fix_firewall() -> JSONResponse:
        if platform.system() != "Windows":
            return JSONResponse({"ok": False, "error": "Only supported on Windows"})

        commands = [
            'netsh advfirewall firewall add rule name="GestureLink Hub" dir=in action=allow protocol=TCP localport=8000',
            'netsh advfirewall firewall add rule name="GestureLink Agent" dir=in action=allow protocol=TCP localport=8001'
        ]

        results = []
        for cmd in commands:
            proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                err = stderr.decode().strip() or stdout.decode().strip()
                if "elevation" in err.lower() or "administrator" in err.lower():
                    return JSONResponse({
                        "ok": False,
                        "error": "Access Denied. Please restart GestureLink Hub as Administrator to fix firewall rules automatically."
                    })
                results.append(err)

        if any(results):
            return JSONResponse({"ok": False, "error": "; ".join(results)})

        return JSONResponse({"ok": True, "message": "Firewall rules added successfully!"})

    @app.get("/api/discovered")
    async def get_discovered() -> JSONResponse:
        return JSONResponse({"devices": discovery.discovered_devices})

    @app.post("/api/agent/add-manual")
    async def add_manual_agent(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        """Manually add an Agent IP when mDNS/Zeroconf discovery fails (e.g. on corporate Wi-Fi)."""
        ip = payload.get("ip", "").strip()
        if not ip:
            return JSONResponse({"ok": False, "error": "No IP provided"}, status_code=400)
        # Probe it first
        try:
            import httpx
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.get(f"https://{ip}:8001/api/ping", timeout=3.0)
                data = resp.json()
                hostname = data.get("hostname", ip)
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"Could not reach Agent at {ip}:8001 — {e}"})

        discovery.discovered_devices[ip] = hostname
        logger.info("Manually added Agent: %s at %s", hostname, ip)
        return JSONResponse({"ok": True, "ip": ip, "hostname": hostname})

    @app.post("/api/agent/fix-firewall")
    async def agent_fix_firewall(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        """Proxy request to the Agent PC to trigger its own firewall fix."""
        ip = payload.get("ip", "").strip()
        if not ip:
            return JSONResponse({"ok": False, "error": "No Agent IP provided"}, status_code=400)
        try:
            import httpx
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.post(f"https://{ip}:8001/api/security/fix-firewall", timeout=5.0)
                return JSONResponse(resp.json())
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"Could not reach Agent at {ip}:8001 — {e}"})

    @app.post("/api/agent/connect-cloud")
    async def connect_cloud_agent(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        agent_id = payload.get("agent_id")
        if not agent_id:
            return JSONResponse({"ok": False, "error": "No agent ID"}, status_code=400)

        uid = tokens.firebase_uid
        if not uid:
            return JSONResponse({"ok": False, "error": "Not logged in to Cloud"}, status_code=401)

        from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
        pc = RTCPeerConnection(configuration=RTCConfiguration(
            iceServers=[
                RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
                RTCIceServer(urls=["stun:stun1.l.google.com:19302"])
            ]
        ))
        setup_pc(pc)

        # Create DataChannel for gestures
        channel = pc.createDataChannel("gestures")
        app.state.cloud_agent_channel = channel  # Save for vision_worker to use!

        @channel.on("open")
        def on_open():
            logger.info(f"Cloud DataChannel {channel.label} opened to Agent {agent_id}")
            # Switch target to cloud so Hub UI updates
            app.state.target_agent = f"cloud:{agent_id}"

        @channel.on("close")
        def on_close():
            logger.info("Cloud DataChannel closed")
            if getattr(app.state, "target_agent", "").startswith("cloud:"):
                app.state.target_agent = None
                app.state.cloud_agent_channel = None

        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)

        offer_data = {
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type
        }

        # Put offer into Firebase
        import urllib.request
        import json
        FIREBASE_URL = "https://gesturelink-5db9c-default-rtdb.firebaseio.com"
        try:
            req = urllib.request.Request(
                f"{FIREBASE_URL}/users/{uid}/agents/{agent_id}/signaling/hub_offer.json",
                data=json.dumps(offer_data).encode('utf-8'),
                method='PUT'
            )
            with urllib.request.urlopen(req, timeout=5) as res:
                pass

            # Wait for answer
            import httpx
            import asyncio
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Poll for answer
                for _ in range(15):
                    resp = await client.get(f"{FIREBASE_URL}/users/{uid}/agents/{agent_id}/signaling/agent_answer.json")
                    ans_data = resp.json()
                    if ans_data:
                        # Clear answer
                        try:
                            await client.delete(f"{FIREBASE_URL}/users/{uid}/agents/{agent_id}/signaling/agent_answer.json")
                        except Exception:

                            pass

                        answer = RTCSessionDescription(sdp=ans_data["sdp"], type=ans_data["type"])
                        await pc.setRemoteDescription(answer)
                        return JSONResponse({"ok": True})
                    await asyncio.sleep(1)

            return JSONResponse({"ok": False, "error": "Agent did not answer"})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    async def _hub_camera_loop():
        global hub_video_frame, hub_camera_active
        import cv2

        indices = [0, 1, 2]  # Try multiple common camera IDs
        cap = None

        for idx in indices:
            logger.info(f"Hub camera loop: Trying index {idx}...")
            cap = cv2.VideoCapture(idx)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if cap.isOpened():
                # Test a read
                ret, _ = cap.read()
                if ret:
                    logger.info(f"Hub camera loop: Successfully opened camera at index {idx}")
                    break
                else:
                    logger.warning(f"Hub camera loop: Index {idx} opened but failed to read frame.")
            cap.release()
            cap = None

        if cap is None:
            logger.error(
                "Hub camera loop: Could not find a working camera after trying indices 0, 1, 2.")
            app.state.camera_active = False
            hub_camera_active = False
            return

        # Standardize captured frame size to 640x480 for massive CPU and memory savings
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        hub_camera_active = True
        consecutive_failures = 0
        loop = asyncio.get_event_loop()
        try:
            while app.state.camera_active:
                import time
                # Check Meet Paint trial (Mode 1)
                if getattr(app.state, "trial_active_1", False):
                    if time.time() - getattr(app.state, "trial_start_time_1", 0) >= 60:
                        app.state.trial_active_1 = False
                        logger.info("Meet Paint Free trial expired!")
                        if getattr(app.state, "active_mode", 0) == 1:
                            app.state.active_mode = 0
                            if hasattr(app.state, "meet_paint"):
                                app.state.meet_paint.stop()

                # Check Builder trial (Mode 2)
                if getattr(app.state, "trial_active_2", False):
                    if time.time() - getattr(app.state, "trial_start_time_2", 0) >= 60:
                        app.state.trial_active_2 = False
                        logger.info("Builder Free trial expired!")
                        if getattr(app.state, "active_mode", 0) == 2:
                            app.state.active_mode = 0
                            if hasattr(app.state, "builder"):
                                app.state.builder.stop()

                # Offload blocking I/O frame read to thread pool executor to prevent event loop lag
                ret, frame = await loop.run_in_executor(None, cap.read)
                if not ret:
                    consecutive_failures += 1
                    if consecutive_failures > 30:  # ~1 second of failure
                        logger.error(
                            "Hub camera loop: Too many consecutive frame failures. Exiting.")
                        break
                    await asyncio.sleep(0.1)  # 100ms polling for faster signaling
                    continue

                consecutive_failures = 0
                # Flip at the start so AI and Display are ALWAYS in sync
                frame = cv2.flip(frame, 1)

                try:
                    is_builder_mode = app.state.active_mode == 2
                    state = await vision_processor.process_frame(frame, is_builder_mode)
                    state.active_mode = app.state.active_mode

                    # --- Handle Mode Switching ---
                    if state.mode_switch:
                        old_mode = app.state.active_mode
                        next_mode = (app.state.active_mode + 1) % 3

                        is_prem = getattr(app.state, "is_premium", False)
                        allowed = False
                        if next_mode == 1 and (is_prem or getattr(app.state, "trial_active_1", False)):
                            allowed = True
                        elif next_mode == 2 and (is_prem or getattr(app.state, "trial_active_2", False)):
                            allowed = True
                        elif next_mode == 0:
                            allowed = True

                        if not allowed:
                            logger.warning(
                                "Blocked mode switch: Premium required for Meet Paint and Builder Mode.")
                        else:
                            app.state.active_mode = next_mode
                            logger.info(f"Mode switched! Active: {app.state.active_mode}")
                            # Meet Paint overlay lifecycle
                            if app.state.active_mode == 1:
                                app.state.meet_paint.start()
                            elif old_mode == 1:
                                app.state.meet_paint.stop()
                            # Builder overlay lifecycle
                            if app.state.active_mode == 2:
                                app.state.builder.start()
                            elif old_mode == 2:
                                app.state.builder.stop()

                    # --- Mode Logic ---
                    if app.state.active_mode == 1:  # MEET PAINT
                        from src.core.config import CONFIG
                        sw, sh = CONFIG.screen_w, CONFIG.screen_h
                        # Translate cursor-mode gesture names → meet-paint gesture names.
                        # VisionProcessor.classify_cursor() emits LEFT_CLICK / INDEX_MOVE,
                        # but MeetPaintController.update() expects PINCH / POINTING.
                        _MEET_PAINT_MAP = {
                            "LEFT_CLICK":  "PINCH",       # index + middle → draw stroke
                            "INDEX_MOVE":  "POINTING",    # index only     → laser pointer
                            "RIGHT_CLICK": "RIGHT_CLICK",  # rock sign      → erase nearest
                            "SCROLL":      "SCROLL",      # 3 fingers      → hold-to-clear
                        }
                        mp_gesture = _MEET_PAINT_MAP.get(state.gesture.value, state.gesture.value)
                        app.state.meet_paint.update(
                            mp_gesture,
                            state.cursor_x, state.cursor_y,
                            sw, sh
                        )
                    elif app.state.active_mode == 2:  # BUILDER
                        if state.gesture.value == "THUMB_PINCH":
                            app.state.builder.handle_thumb_pinch_drag(
                                state.cursor_x, state.cursor_y, sw, sh,
                                (state.cursor_x, state.cursor_y), True
                            )
                        else:
                            app.state.builder.update(
                                state.gesture.value, state.cursor_x, state.cursor_y, sw, sh, state
                            )
                        # Overlay is updated inside builder.update() / handle_thumb_pinch_drag()
                    elif app.state.active_mode == 0:
                        dispatch_mouse(state, mouse)

                    # Hub GUI video feed removed per user request. No need to encode frames here.
                except Exception as e:
                    logger.error(f"Hub loop error: {e}")
                await asyncio.sleep(0.01)
        except Exception as e:
            logger.error(f"Hub camera loop crashed: {e}")
        finally:
            if cap:
                cap.release()
            app.state.camera_active = False
            logger.info("Hub camera loop: Terminated.")

    @app.post("/api/hub/camera/toggle")
    async def toggle_hub_camera(payload: Annotated[dict, Body(...)], target: Optional[str] = Query(None)) -> JSONResponse:
        active = payload.get("active", False)
        lan_ip = detect_lan_ip()
        logger.info(f"Toggle request: target={target}, lan_ip={lan_ip}, active={active}")

        # If target matches hub or is omitted, control local camera
        ngrok_host = ""
        if hasattr(app.state, "ngrok_url") and app.state.ngrok_url:
            from urllib.parse import urlparse
            ngrok_host = urlparse(app.state.ngrok_url).hostname

        cloudflare_host = ""
        if hasattr(app.state, "cloudflare_url") and app.state.cloudflare_url:
            from urllib.parse import urlparse
            cloudflare_host = urlparse(app.state.cloudflare_url).hostname

        custom_host = ""
        hub_url = os.getenv("HUB_URL")
        if hub_url:
            from urllib.parse import urlparse
            custom_host = urlparse(hub_url).hostname

        is_hub = (
            not target
            or target in ("localhost", "127.0.0.1", lan_ip)
            or (ngrok_host and target == ngrok_host)
            or (cloudflare_host and target == cloudflare_host)
            or (custom_host and target == custom_host)
        )

        if is_hub:
            if active and not app.state.camera_active:
                app.state.camera_active = True
                app.state.camera_task = asyncio.create_task(_hub_camera_loop())
                logger.info("Hub local camera turned ON")
            elif not active:
                app.state.camera_active = False
                logger.info("Hub local camera turned OFF")
            return JSONResponse({"ok": True, "active": app.state.camera_active})

        # Otherwise proxy to agent
        try:
            import httpx
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.post(f"https://{target}:8001/api/camera/toggle", json=payload, timeout=2.0)
                return JSONResponse(resp.json())
        except Exception as e:
            logger.error("Proxy camera toggle failed for %s: %s", target, e)
            return JSONResponse({"ok": False, "error": str(e)})

    # --- WebRTC Low Latency Hub ---
    pcs = set()

    def setup_pc(pc: RTCPeerConnection):
        pcs.add(pc)

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            if pc.connectionState == "failed" or pc.connectionState == "closed":
                await pc.close()
                pcs.discard(pc)

        # Handle Data Channel for Gestures
        @pc.on("datachannel")
        def on_datachannel(channel):
            class ChannelResponder:
                def __init__(self, ch): self.ch = ch

                async def send_json(self, data):
                    if self.ch.readyState == "open":
                        self.ch.send(json.dumps(data))

            responder = ChannelResponder(channel)

            @channel.on("message")
            async def on_message(message):
                if isinstance(message, str):
                    try:
                        await _handle_ws_message(responder, {"text": message}, None, mouse)
                    except Exception:

                        pass

    @app.post("/api/webrtc/offer")
    async def webrtc_offer(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        offer = RTCSessionDescription(sdp=payload["sdp"], type=payload["type"])

        # Add STUN + TURN servers for hotspot/double-NAT traversal
        from aiortc import RTCConfiguration, RTCIceServer
        pc = RTCPeerConnection(configuration=RTCConfiguration(
            iceServers=[
                # STUN servers (single-NAT traversal)
                RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
                RTCIceServer(urls=["stun:stun1.l.google.com:19302"]),
                RTCIceServer(urls=["stun:stun2.l.google.com:19302"]),
                # TURN servers (double-NAT fallback for hotspots)
                RTCIceServer(
                    urls=["turn:numb.viagenie.ca"],
                    username="webrtc@example.com",
                    credential="webrtcpassword"
                )
            ]
        ))
        setup_pc(pc)

        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        return JSONResponse({
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type
        })

    @app.get("/api/hub/camera/status")
    async def get_hub_camera_status(target: Optional[str] = Query(None)):
        lan_ip = detect_lan_ip()

        ngrok_host = ""
        if hasattr(app.state, "ngrok_url") and app.state.ngrok_url:
            from urllib.parse import urlparse
            ngrok_host = urlparse(app.state.ngrok_url).hostname

        cloudflare_host = ""
        if hasattr(app.state, "cloudflare_url") and app.state.cloudflare_url:
            from urllib.parse import urlparse
            cloudflare_host = urlparse(app.state.cloudflare_url).hostname

        custom_host = ""
        hub_url = os.getenv("HUB_URL")
        if hub_url:
            from urllib.parse import urlparse
            custom_host = urlparse(hub_url).hostname

        is_hub = (
            not target
            or target in ("localhost", "127.0.0.1", lan_ip)
            or (ngrok_host and target == ngrok_host)
            or (cloudflare_host and target == cloudflare_host)
            or (custom_host and target == custom_host)
        )

        if is_hub:
            status = "inactive"
            if app.state.camera_active:
                status = "active" if hub_camera_active else "starting"
            return JSONResponse({"active": hub_camera_active, "status": status})

        # Otherwise proxy to agent
        try:
            import httpx
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.get(f"https://{target}:8001/api/agent/info", timeout=2.0)
                data = resp.json()
                active = data.get("camera_active", False)
                status = data.get("camera_status")
                if not status:
                    status = "active" if active else "inactive"
                return JSONResponse({"active": active, "status": status})
        except Exception as e:
            logger.error("Proxy camera status failed for %s: %s", target, e)
            return JSONResponse({"active": False, "status": "error", "error": str(e)})

    @app.get("/api/hub/mode")
    async def get_hub_mode():
        return JSONResponse({"mode": app.state.active_mode})

    @app.post("/api/hub/mode")
    async def set_hub_mode(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        mode = payload.get("mode", 0)
        old_mode = app.state.active_mode
        new_mode = mode % 3
        app.state.active_mode = new_mode
        logger.info(f"Hub mode set to {new_mode} via API (was {old_mode})")
        # Manage Meet Paint overlay lifecycle
        if new_mode == 1 and old_mode != 1:
            app.state.meet_paint.start()
            logger.info("MeetPaintController: overlay started via API mode switch.")
        elif old_mode == 1 and new_mode != 1:
            app.state.meet_paint.stop()
            logger.info("MeetPaintController: overlay stopped via API mode switch.")

        # Manage Builder overlay lifecycle
        if new_mode == 2 and old_mode != 2:
            app.state.builder.start()
            logger.info("BuilderController: overlay started via API mode switch.")
        elif old_mode == 2 and new_mode != 2:
            app.state.builder.stop()
            logger.info("BuilderController: overlay stopped via API mode switch.")

        return JSONResponse({"ok": True, "mode": new_mode})

    # --- Meet Paint Remote Control Endpoints ---

    @app.post("/api/hub/meet-paint/color")
    async def set_meet_paint_color(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        """Change the active ink colour for the Meet Paint overlay."""
        color = payload.get("color", "#FF4757")
        app.state.meet_paint.set_color(color)
        logger.info(f"MeetPaint color set to {color}")
        return JSONResponse({"ok": True, "color": color})

    @app.post("/api/hub/meet-paint/size")
    async def set_meet_paint_size(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        """Change the brush size (stroke width in px) for the Meet Paint overlay."""
        size = int(payload.get("size", 4))
        app.state.meet_paint.set_size(size)
        logger.info(f"MeetPaint size set to {size}")
        return JSONResponse({"ok": True, "size": size})

    @app.post("/api/hub/meet-paint/clear")
    async def clear_meet_paint(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        """Wipe all strokes from the Meet Paint overlay (remote clear)."""
        app.state.meet_paint.remote_clear()
        logger.info("MeetPaint: canvas cleared via API")
        return JSONResponse({"ok": True})

    @app.get("/api/hub/modal/status")
    async def get_modal_status():
        """Returns the connection status and latency of the Modal cloud inference."""
        import os
        # Check if USE_MODAL is true in env
        use_modal = os.environ.get("USE_MODAL", "false").lower() == "true"
        client = None
        if hasattr(app.state, 'vision_processor') and hasattr(app.state.vision_processor, '_modal_client'):
            client = app.state.vision_processor._modal_client

        return JSONResponse({
            "enabled": use_modal,
            "connected": bool(client)
        })

    @app.post("/api/hub/modal/toggle")
    async def toggle_modal(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        """Enables or disables Modal cloud inference dynamically."""
        import os
        from dotenv import set_key

        enabled = payload.get("enabled", False)
        val = "true" if enabled else "false"
        os.environ["USE_MODAL"] = val

        env_path = os.path.join(APP_DIR, ".env")
        if os.path.exists(env_path):
            set_key(env_path, "USE_MODAL", val)

        # If enabling and client doesn't exist, we must re-init the vision processor's client
        if enabled and hasattr(app.state, 'vision_processor'):
            if not app.state.vision_processor._modal_client:
                from src.core.modal_vision import get_modal_client
                app.state.vision_processor._modal_client = get_modal_client()

        # Also update the worker process if it exists
        if enabled and hasattr(app.state, 'vision') and app.state.vision:
            # We don't have a direct way to update the vision_worker's env inside its process
            # easily without a restart, but we can restart the worker.
            app.state.vision.stop()
            app.state.vision.start()

        logger.info(f"Modal cloud inference set to: {enabled}")
        return JSONResponse({"ok": True, "enabled": enabled})

    @app.get("/api/hub/info")
    async def get_hub_info():
        return {
            "hostname": app.state.friendly_name,
            "hub_id": f"GL-HUB-{platform.node()}",
            "local_ip": detect_lan_ip(),
            "port": port,
            "cloudflare_url": getattr(app.state, "cloudflare_url", None),
            "ssl_active": CERT_PEM.exists(),
            "pin": tokens.current_pin
        }

    @app.post("/api/hub/shutdown")
    async def hub_shutdown():
        """Called by hub.html when the browser window/tab is closed (sendBeacon).
        Triggers a full force-kill so no ghost processes remain."""
        import subprocess
        import threading
        logger.info("Shutdown requested via browser close.")

        def _kill():
            import time
            import os
            time.sleep(0.3)  # brief delay so HTTP response can be sent first
            try:
                subprocess.run(
                    ["taskkill", "/F", "/IM", "GestureLink_Hub.exe"],
                    capture_output=True
                )
            except Exception:
                pass
            os._exit(0)

        threading.Thread(target=_kill, daemon=True).start()
        return JSONResponse({"status": "shutting_down"})

    @app.get("/api/apps")
    async def get_apps(ip: Optional[str] = None) -> JSONResponse:
        # If IP is provided and not local, proxy to agent
        if ip and ip not in ("localhost", "127.0.0.1", detect_lan_ip()):
            try:
                import httpx
                async with httpx.AsyncClient(verify=False) as client:
                    resp = await client.get(f"https://{ip}:8001/api/apps", timeout=2.0)
                    return JSONResponse(resp.json())
            except Exception as e:
                logger.error("Proxy apps failed for %s: %s", ip, e)
                return JSONResponse({"apps": [], "error": str(e)})

        # Default: local hub apps
        apps = shortcuts.get_available_apps()
        return JSONResponse({"apps": apps})

    @app.get("/api/shortcuts")
    async def get_shortcuts() -> JSONResponse:
        return JSONResponse({"shortcuts": shortcuts.get_bindings()})

    @app.post("/api/shortcuts")
    async def set_shortcuts(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        new_shortcuts = payload.get("shortcuts", {})
        shortcuts.set_bindings(new_shortcuts)
        return JSONResponse({"ok": True})

    # In-memory device nickname store (persisted to settings dir)
    _NICKNAMES_FILE = HUB_DIR / "device_nicknames.json"
    _device_nicknames: dict = {}
    if _NICKNAMES_FILE.exists():
        try:
            import json as _json
            _device_nicknames = _json.loads(_NICKNAMES_FILE.read_text())
        except Exception:
            pass

    def _save_nicknames():
        try:
            _NICKNAMES_FILE.write_text(json.dumps(_device_nicknames))
        except Exception as e:
            logger.error("Failed to save nicknames: %s", e)

    @app.post("/api/pair")
    async def initiate_pair(request: Request, payload: Annotated[dict, Body(...)]) -> JSONResponse:
        pin = payload.get("pin")
        hostname = payload.get("hostname", "Unknown Phone")
        client_ip = request.client.host if request.client else "0.0.0.0"
        logger.info(f"Pair attempt from {client_ip} ({hostname}) with PIN {pin}")

        if str(pin) != tokens.current_pin:
            logger.warning(
                f"Invalid PIN from {client_ip}. Expected {tokens.current_pin}, got {pin}")
            return JSONResponse({"status": "error", "error": "Invalid PIN"}, status_code=401)

        # Auto-approve trusted IPs — no popup needed for known devices
        if client_ip in security.trusted_ips:
            token = tokens.generate_token(client_ip)
            logger.info("Auto-approved trusted device %s (%s)", client_ip, hostname)
            return JSONResponse({"status": "approved", "token": token})

        # New/unknown device — go through pending approval popup
        req_id = security.add_pending_request(client_ip, hostname)
        logger.info("Pair request from %s (%s) -> pending ID %s", client_ip, hostname, req_id)
        return JSONResponse({"status": "pending", "request_id": req_id})

    @app.post("/api/pair-request")
    async def pair_request(request: Request, payload: Annotated[dict, Body(...)]) -> JSONResponse:
        """PIN-less pairing initiation for the native APK scanner flow.
        The device sends its name and a unique device ID; the Hub shows an
        approval popup (same as the existing security/pending flow).
        """
        hostname = payload.get("hostname", "Mobile Device")
        device_id = payload.get("device_id", "")
        client_ip = request.client.host if request.client else "0.0.0.0"
        logger.info("APK pair-request from %s (%s) device_id=%s", client_ip, hostname, device_id)

        # Auto-approve if this device is already trusted
        if client_ip in security.trusted_ips:
            token = tokens.generate_token(client_ip)
            logger.info("Auto-approved trusted APK device %s (%s)", client_ip, hostname)
            return JSONResponse({"status": "approved", "token": token})

        # Add to pending — Hub UI will show the approval popup
        req_id = security.add_pending_request(client_ip, hostname)
        logger.info("APK pair-request pending for %s (%s) -> req_id=%s",
                    client_ip, hostname, req_id)
        return JSONResponse({"status": "pending", "request_id": req_id})

    @app.get("/api/pair/status/{request_id}")
    async def check_pair_status(request_id: str, request: Request) -> JSONResponse:
        client_ip = request.client.host if request.client else "0.0.0.0"
        token = security.get_token_for_ip(client_ip)

        if token:
            return JSONResponse({"status": "approved", "token": token})

        if request_id in security.pending_requests:
            return JSONResponse({"status": "pending"})

        return JSONResponse({"status": "rejected"})

    @app.post("/api/logout")
    async def logout(request: Request, payload: Annotated[dict, Body(...)]) -> JSONResponse:
        """Issue 5: Invalidate token server-side so it cannot be reused."""
        token = payload.get("token")
        if token and token in tokens.valid_tokens:
            revoked_ip = tokens.valid_tokens.pop(token, "unknown")
            logger.info("Token revoked for IP %s", revoked_ip)
        return JSONResponse({"ok": True})

    @app.get("/api/security/pending")
    async def get_pending_requests() -> JSONResponse:
        return JSONResponse({"pending": list(security.pending_requests.values())})

    @app.post("/api/security/approve")
    async def approve_pairing(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        req_id = payload.get("id")
        req = security.pending_requests.get(req_id)
        if not req:
            return JSONResponse({"ok": False, "error": "Request not found"}, status_code=404)

        token = tokens.generate_token(req["ip"])
        if security.approve_request(req_id, token):
            return JSONResponse({"ok": True})
        return JSONResponse({"ok": False})

    @app.post("/api/security/reject")
    async def reject_pairing(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        req_id = payload.get("id")
        security.reject_request(req_id)
        return JSONResponse({"ok": True})

    @app.post("/api/hub/shutdown")
    async def shutdown_hub() -> JSONResponse:
        import os
        import signal
        logger.info("Hub shutdown requested via API")
        # Send SIGTERM to self. tray.py handles this for a clean exit.
        os.kill(os.getpid(), signal.SIGTERM)
        return JSONResponse({"ok": True})

    @app.get("/api/security")
    async def get_security() -> JSONResponse:
        return JSONResponse({
            "trusted": list(security.trusted_ips),
            "blocked": list(security.blocked_ips),
            "pending": list(security.pending_requests.values())
        })

    @app.post("/api/security/action")
    async def security_action(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        ip, action = payload.get("ip"), payload.get("action")
        if not ip:
            return JSONResponse({"ok": False}, status_code=400)
        if action == "trust":
            security.trusted_ips.add(ip)
            security.blocked_ips.discard(ip)
        elif action == "block":
            security.blocked_ips.add(ip)
            security.trusted_ips.discard(ip)
        security.save()
        return JSONResponse({"ok": True})

    @app.get("/api/devices/nicknames")
    async def get_nicknames() -> JSONResponse:
        return JSONResponse({"nicknames": _device_nicknames})

    @app.post("/api/devices/rename")
    async def rename_device(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        ip = payload.get("ip")
        name = payload.get("name", "").strip()
        if not ip or not name:
            return JSONResponse({"ok": False, "error": "ip and name required"}, status_code=400)
        _device_nicknames[ip] = name
        _save_nicknames()
        return JSONResponse({"ok": True})

    @app.get("/api/settings")
    async def get_settings() -> JSONResponse:
        s = CONFIG.gesture.smoothing
        vision_sensitivity = int((s - 0.05) / 0.45 * 90 + 5)
        return JSONResponse({
            "sensitivity": vision_sensitivity,
            "trackpad_sensitivity": CONFIG.gesture.trackpad_sensitivity,
            "scroll_speed": CONFIG.gesture.scroll_speed
        })

    @app.post("/api/settings")
    async def set_settings(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        sens = payload.get("sensitivity", 50)
        scroll = payload.get("scroll_speed", 20)

        # If 'sensitivity' (0-100) is sent from mobile, map it to the 0.5-3.0 trackpad multiplier
        # Otherwise use the provided trackpad_sensitivity or the current value.
        if "trackpad_sensitivity" in payload:
            tp_sens = float(payload["trackpad_sensitivity"])
        else:
            tp_sens = 0.5 + (sens / 100.0) * 2.5

        _save_settings(sens, scroll, tp_sens)
        _load_settings()  # Re-apply local

        # Propagate to discovered agents
        import httpx

        async def notify_agents():
            for ip in discovery.discovered_devices:
                try:
                    async with httpx.AsyncClient(verify=False) as client:
                        # Map 1.5 base to a 0-100 scale if agent expects "sensitivity"
                        # Or just send trackpad_sensitivity directly
                        await client.post(
                            f"https://{ip}:8001/api/settings",
                            json={"trackpad_sensitivity": tp_sens},
                            timeout=2.0
                        )
                except Exception as e:
                    logger.warning(f"Failed to sync settings to Agent {ip}: {e}")

        asyncio.create_task(notify_agents())

        return JSONResponse({"ok": True})

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket, token: Annotated[Optional[str], Query()] = None, target: Optional[str] = Query(None)):
        client_ip = ws.client.host if ws.client else "unknown"
        local_ip = detect_lan_ip()
        logger.info("WS connect: client=%s, token=%s..., target=%s, local_ip=%s",
                    client_ip, (token or "")[:8], target, local_ip)

        # Token validation IS the security gate.
        if not tokens.validate_token(token):
            logger.warning("WS rejected: invalid token from %s", client_ip)
            await asyncio.sleep(0.1)  # Debounced rejection
            await ws.close(code=4003)
            return

        await ws.accept()
        logger.info("WS accepted: client=%s", client_ip)

        # If target matches local IP OR the ngrok hostname, it's a LOCAL path
        ngrok_host = ""
        if hasattr(app.state, "ngrok_url") and app.state.ngrok_url:
            from urllib.parse import urlparse
            ngrok_host = urlparse(app.state.ngrok_url).hostname

        cloudflare_host = ""
        if hasattr(app.state, "cloudflare_url") and app.state.cloudflare_url:
            from urllib.parse import urlparse
            cloudflare_host = urlparse(app.state.cloudflare_url).hostname

        custom_host = ""
        hub_url = os.getenv("HUB_URL")
        if hub_url:
            from urllib.parse import urlparse
            custom_host = urlparse(hub_url).hostname

        is_local = (
            target is None
            or target == local_ip
            or target == "localhost"
            or (ngrok_host and target == ngrok_host)
            or (cloudflare_host and target == cloudflare_host)
            or (custom_host and target == custom_host)
        )

        if not is_local:
            logger.info("RELAY PATH: proxying %s -> Agent %s", client_ip, target)

            # Try WSS first, fallback to WS if Agent isn't using SSL
            async def connect_to_agent():
                # permissive SSL context for self-signed certs
                import ssl as _ssl
                ssl_ctx = _ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = _ssl.CERT_NONE

                try:
                    agent_url_wss = f"wss://{target}:8001/ws?token=hub_internal"
                    return await websockets.connect(agent_url_wss, ssl=ssl_ctx, open_timeout=2)
                except Exception:
                    logger.info("Agent WSS failed, falling back to WS for %s", target)
                    agent_url_ws = f"ws://{target}:8001/ws?token=hub_internal"
                    return await websockets.connect(agent_url_ws, open_timeout=2)

            try:
                async with await connect_to_agent() as agent_ws:
                    async def mobile_to_agent():
                        try:
                            while True:
                                data = await ws.receive()
                                if data.get("type") == "websocket.disconnect":
                                    break
                                if "text" in data:
                                    await agent_ws.send(data["text"])
                                elif "bytes" in data:
                                    await agent_ws.send(data["bytes"])
                        except Exception:
                            pass
                        finally:
                            await agent_ws.close()

                    async def agent_to_mobile():
                        try:
                            async for message in agent_ws:
                                if isinstance(message, str):
                                    await ws.send_text(message)
                                else:
                                    await ws.send_bytes(message)
                        except Exception:
                            pass
                        finally:
                            try:
                                await ws.close()
                            except Exception:
                                pass

                    await asyncio.gather(mobile_to_agent(), agent_to_mobile())
            except Exception as e:
                logger.error("Failed to proxy to agent %s: %s", target, e)
                try:
                    # Tell the mobile UI the agent is unreachable
                    await ws.send_text(json.dumps({"type": "error", "message": f"Agent {target} is unreachable"}))
                except Exception:
                    pass
                await ws.close()
            return

        # LOCAL HUB LOGIC — register as connected
        logger.info("LOCAL PATH: client=%s entering local hub control loop", client_ip)
        import time
        nonlocal active_hub_dashboards
        if token == "hub_internal":
            active_hub_dashboards += 1

        connected_clients[client_ip] = {
            "ip": client_ip,
            "connected_at": int(time.time()),
            "type": "mobile"
        }
        logger.info("Client connected: %s", client_ip)

        async def ws_receive_loop():
            try:
                while True:
                    msg = await ws.receive()
                    if "bytes" in msg:
                        # Process vision in a background task so it doesn't block the loop
                        asyncio.create_task(_handle_vision_frame(
                            ws, msg["bytes"], vision_worker, mouse))
                    elif "text" in msg:
                        await _handle_ws_message(ws, msg, vision_worker, mouse)
            except WebSocketDisconnect:
                pass
            except Exception as e:
                if "receive" not in str(e):  # Suppress noisy disconnect errors
                    logger.error("WS Loop Error: %s", e)

        try:
            await ws_receive_loop()
        finally:
            connected_clients.pop(client_ip, None)
            logger.info("Client disconnected: %s", client_ip)
            if token == "hub_internal":
                active_hub_dashboards -= 1

                async def check_shutdown():
                    await asyncio.sleep(1.5)
                    if active_hub_dashboards <= 0:
                        logger.info("Local dashboard closed. Shutting down Hub...")
                        # os._exit(0)
                asyncio.create_task(check_shutdown())

    def dispatch_mouse(state, mouse):
        import json
        cloud_ch = getattr(app.state, "cloud_agent_channel", None)
        if cloud_ch and cloud_ch.readyState == "open":
            try:
                cloud_ch.send(json.dumps({
                    "type": "raw_state",
                    "gesture": state.gesture.value,
                    "cursor_x": state.cursor_x,
                    "cursor_y": state.cursor_y,
                    "scroll_dy": state.scroll_dy,
                    "pinch_active": state.pinch_active,
                    "thumb_pinch_active": state.thumb_pinch_active,
                    "mode_switch": state.mode_switch,
                    "active_mode": state.active_mode
                }))
                return "SENT_TO_CLOUD"
            except Exception as e:
                logger.error(f"Failed to send to cloud agent: {e}")
        return mouse.update(state)

    async def _handle_vision_frame(responder, frame_bytes, vision, mouse):
        # AsyncVisionWorker handles the queue and process management
        result = await vision.process_frame(frame_bytes)
        if result:
            state, _ = result
            if state:
                status = dispatch_mouse(state, mouse)
                try:
                    await responder.send_json({"status": status, "type": "gesture"})
                except Exception:

                    pass

    async def _handle_ws_message(responder, msg, vision, mouse):
        cloud_ch = getattr(app.state, "cloud_agent_channel", None)
        if cloud_ch and cloud_ch.readyState == "open":
            try:
                cloud_ch.send(msg["text"])
                if responder:
                    await responder.send_json({"status": "SENT_TO_CLOUD"})
            except Exception:

                pass
            return

        try:
            data = json.loads(msg["text"])
            mtype = data.get("type")

            if (mtype in ("touch", "move")):
                mouse.handle_touch_move(float(data.get("dx", 0)), float(data.get("dy", 0)))
                # No response needed for high-frequency moves
            elif mtype == "click":
                res = mouse.handle_click(data.get("button", "left"))
                if responder:
                    await responder.send_json({"status": res})
            elif mtype in ("click_down", "click_up"):
                is_down = (mtype == "click_down")
                res = mouse.handle_click_state(data.get("button", "left"), is_down)
                if responder:
                    await responder.send_json({"status": res})
            elif mtype == "scroll":
                res = mouse.handle_touch_scroll(float(data.get("dy", 0)))
                if responder:
                    await responder.send_json({"status": res})
            elif mtype == "zoom":
                res = mouse.handle_touch_zoom(float(data.get("delta", 0)))
                if responder:
                    await responder.send_json({"status": res})
            elif mtype == "shortcut":
                res = mouse.handle_touch_shortcut(data.get("slot", ""))
                if responder:
                    await responder.send_json({"status": res})
            elif mtype == "key":
                key = data.get("key")
                if key:
                    res = mouse.handle_key(key)
                    if responder:
                        await responder.send_json({"status": res})
            elif mtype == "hotkey":
                keys = data.get("keys", [])
                if keys:
                    res = mouse.handle_hotkey(keys)
                    if responder:
                        await responder.send_json({"status": res})
            elif mtype == "camera_toggle":
                active = data.get("active", False)
                try:
                    if active:
                        if not app.state.vision.is_running:
                            app.state.vision.start()
                            logger.info("Camera started via WS/WebRTC")
                    else:
                        if app.state.vision.is_running:
                            app.state.vision.stop()
                            logger.info("Camera stopped via WS/WebRTC")
                    if responder:
                        await responder.send_json({"status": "CAMERA_TOGGLED", "active": app.state.vision.is_running})
                except Exception as e:
                    logger.error("Camera toggle error: %s", e)
        except Exception as e:
            logger.error("WS Message Error: %s", e)

    # Static Assets
    if MOBILE_DIST.exists():
        app.mount("/assets", StaticFiles(directory=str(MOBILE_DIST / "assets")), name="assets")

        @app.get("/")
        async def index():
            return FileResponse(MOBILE_DIST / "index.html")

        @app.get("/manifest.json")
        async def manifest(): return FileResponse(MOBILE_DIST / "manifest.json")
        @app.get("/sw.js")
        async def sw(): return FileResponse(MOBILE_DIST / "sw.js")
        @app.get("/icon-192.png")
        async def icon192(): return FileResponse(MOBILE_DIST / "icon-192.png")
        @app.get("/icon-512.png")
        async def icon512(): return FileResponse(MOBILE_DIST / "icon-512.png")

    @app.get("/mobile.html")
    async def mobile_page_alias():
        return FileResponse(MOBILE_DIST / "index.html")

    @app.get("/hub")
    async def hub_page():
        with open(HUB_HTML, "r", encoding="utf-8") as f:
            content = f.read()

        # Prioritize Custom Domain > Cloudflare > ngrok
        remote_url = os.getenv("HUB_URL") or getattr(app.state, "cloudflare_url", None) or getattr(
            app.state, "ngrok_url", None) or os.getenv("NGROK_URL")

        info = {
            "pin": tokens.current_pin,
            "lan_ip": detect_lan_ip(),
            "port": port,
            "remote_url": remote_url,
            "frontend_url": os.getenv("FRONTEND_URL")
        }
        # Fix: assign to window.infoData so fetchUpdates() can read it across the script
        injection = f"window.infoData = {json.dumps(info)};"
        injection += "\ndocument.addEventListener('DOMContentLoaded', () => {"
        injection += f"\n  document.getElementById('pin-display').textContent = '{tokens.current_pin}';"
        injection += "\n});"

        content = content.replace("/*INFO_INJECTION*/", injection)
        return HTMLResponse(content)

    @app.get("/lan-qr.png")
    async def qr_gen(request: Request, url: Optional[str] = None, pin: Optional[str] = None) -> StreamingResponse:
        frontend_base = os.getenv("FRONTEND_URL") or "https://app.thequinn.tech"

        # Use X-Forwarded-Host if behind a tunnel (Cloudflare, ngrok)
        host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
        # Remove port if present for hostname check
        hostname = host.split(":")[0] if ":" in host else host

        # Detect cloud presence (headers or non-local hostname)
        is_cloud_header = any(h in request.headers for h in [
                              "cf-ray", "cf-connecting-ip", "x-ngrok-file-config"])

        def is_private_ip(ip):
            if ip in ("localhost", "127.0.0.1", "::1"):
                return True
            if ip.startswith("192.168.") or ip.startswith("10."):
                return True
            if ip.startswith("172."):
                parts = ip.split(".")
                if len(parts) >= 2 and parts[1].isdigit():
                    sec = int(parts[1])
                    return 16 <= sec <= 31
            return False

        is_local = not is_cloud_header and (is_private_ip(hostname) or hostname.endswith(".local"))
        logger.info(f"QR Request - Host: {host}, is_local: {is_local}, is_cloud: {is_cloud_header}")

        # Get the active tunnel URL if it exists
        tunnel_url = os.getenv("HUB_URL") or getattr(app.state, 'cloudflare_url', None)

        if url:
            target = url
        else:
            # Determine the hub address (either the custom domain, Cloudflare tunnel or the local LAN IP)
            if tunnel_url:
                hub_address = tunnel_url
            elif not is_local:
                hub_address = host
            else:
                hub_address = detect_lan_ip() + f":{port}"

            # Clean up the hostname
            hub_hostname = hub_address.replace("https://", "").replace("http://", "").rstrip("/")

            # Always use the hosted frontend URL to ensure consistency
            target = f"{frontend_base.rstrip('/')}/?hub={hub_hostname}"

            logger.info(f"QR pointing to: {target}")

        if pin:
            target += ("&" if "?" in target else "?") + f"pin={pin}"

        logger.info(f"QR Final Target: {target}")
        qr = qrcode.make(target)
        buf = io.BytesIO()
        try:
            qr.save(buf, format="PNG")
        except TypeError:
            # Handle pure-python qrcode implementation which doesn't take 'format'
            qr.save(buf)
        buf.seek(0)
        return StreamingResponse(buf, media_type="image/png")

    @app.get("/download-qr.png")
    async def download_qr_gen() -> StreamingResponse:
        frontend_base = os.getenv("FRONTEND_URL") or "https://gesture-link-iota.vercel.app"
        target = f"{frontend_base.rstrip('/')}/download"

        logger.info(f"Download QR Target: {target}")
        qr = qrcode.make(target)
        buf = io.BytesIO()
        try:
            qr.save(buf, format="PNG")
        except TypeError:
            qr.save(buf)
        buf.seek(0)
        return StreamingResponse(buf, media_type="image/png")

    async def _rotate_pin_periodically():
        while True:
            await asyncio.sleep(1800)  # 30 minutes
            tokens.reset_pin()
            logger.info("Background PIN rotation triggered.")

    return app


def run():
    import multiprocessing
    if platform.system() == "Windows":
        multiprocessing.freeze_support()
        try:
            multiprocessing.set_start_method('spawn', force=True)
        except RuntimeError:
            pass

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    # Port 12: Kill existing processes and free up port before starting
    from src.core.utils import kill_process_on_port, kill_processes_by_name
    print(f"[*] Initializing Hub on port {args.port}...")
    kill_process_on_port(args.port)
    kill_processes_by_name(["cloudflared", "GestureLink_Hub"])

    project_root = Path(__file__).resolve().parent.parent.parent
    cert = resource_path("cert.pem")
    key = resource_path("key.pem")
    ssl = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)} if cert.exists() else {}

    app = build_app(args.host, args.port)

    # Apply filter to uvicorn access logs to prevent console spam
    logging.getLogger("uvicorn.access").addFilter(EndpointFilter())

    uvicorn.run(app, host=args.host, port=args.port, **ssl)


if __name__ == "__main__":
    run()
