from __future__ import annotations

import asyncio
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
from typing import Dict, Optional, Any, Annotated

from dotenv import load_dotenv
from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import qrcode
import socket
import websockets
# from pyngrok import ngrok, conf

# Heavy imports deferred to avoid module double-load errors in subprocesses
# from src.core.controller import MouseController
# from src.core.vision import VisionProcessor
# from src.core.vision_worker import AsyncVisionWorker
from src.core.utils import resource_path

from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.mediastreams import MediaStreamError
from av import VideoFrame

# Checklist:
# | 1 | Hub starts without any error. | ✅ Passed | No startup errors. |
# | 2 | QR code and pin showing on hub UI. | ✅ Passed | Dynamic IP detection working. |
# | 4 | Hub-mobile connection is perfect. | ✅ Passed | User confirmed successful control. |
# | 5 | Instant cursor control shift to mobile. | ✅ Passed | Verified by user. |
# | 8 | Hub camera working for gestures. | 🛠 Testing | Fixed loop logic. |
# | 11 | Mobile UI button for Hub camera. | ✅ Passed | New "Remote Intelligence" card. |
# | 14 | Multiprocessing for Vision. | ✅ Passed | Implemented and verified. |
# | 13 | Entire flow working without lag. | ⏳ Testing | Throttled and optimized. |

load_dotenv()
logger = logging.getLogger("gesture_control.remote")

class EndpointFilter(logging.Filter):
    """Silences aggressive polling logs for specific endpoints."""
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        # Silence heartbeat endpoints
        silence = [
            "/api/security/pending",
            "/api/hub/camera/status",
            "/api/connected-clients",
            "/api/discovered"
        ]
        return not any(path in msg for path in silence)


# Global state for camera streaming
hub_video_frame: bytes | None = None
hub_camera_active: bool = False
logger = logging.getLogger("gesture_control.remote")



APP_DIR = Path(__file__).resolve().parent
HUB_DIR = APP_DIR

# Use resource_path() so these resolve correctly inside a PyInstaller .exe
HUB_HTML     = resource_path("src/web/hub/hub.html")
MOBILE_DIST  = resource_path("src/web/mobile/dist")
SETTINGS_FILE = HUB_DIR / "settings.json"
SECURITY_FILE = HUB_DIR / "security.json"
CERT_PEM = resource_path("cert.pem")
KEY_PEM  = resource_path("key.pem")

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
    from src.core.vision import VisionProcessor, Gesture
    from src.hub.managers import SecurityManager, TokenManager, DeviceDiscovery, detect_lan_ip
    from src.core.vision_worker import AsyncVisionWorker
    from src.core.modes import CanvasController, BuilderController

    def _open_dashboard():
        import webbrowser, subprocess, os, time
        # Give the tunnel 3 seconds to establish
        time.sleep(3.0) 
        
        proto = "https" if CERT_PEM.exists() else "http"
        local_url = f"{proto}://localhost:{port}/hub"
        
        # Prioritize remote URL (Cloudflare > ngrok > Deployment > Localhost)
        remote_url = (
            getattr(app.state, "cloudflare_url", None) or 
            getattr(app.state, "ngrok_url", None) or 
            os.getenv("NGROK_URL")
        )
        target_url = remote_url if remote_url else local_url
        
        # Ensure we always open the /hub dashboard path on the PC
        if not target_url.endswith("/hub"):
            target_url = target_url.rstrip("/") + "/hub"
        
        # If we are falling back to localhost, show a debug popup
        if target_url == local_url:
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, "App is running on local host (No active tunnel detected)", "GestureLink Debug", 0x40)
            except: pass

        # Attempt to use Chrome/Edge in App Mode for a "Clean" window
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
                except: pass
                
        # Fallback to default browser
        webbrowser.open(target_url)

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
                    app.state.friendly_name = json.load(f).get("friendly_name", app.state.friendly_name)
            except: pass
            
        # --- WebRTC SIGNALING LISTENER (For Remote/Tunnel) ---
        async def _signaling_listener():
            await asyncio.sleep(2) # Wait for tunnel to stabilize
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
                            
                            pc = RTCPeerConnection(configuration={
                                "iceServers": [
                                    {"urls": ["stun:stun.l.google.com:19302"]},
                                    {"urls": ["stun:stun1.l.google.com:19302"]},
                                    {"urls": ["stun:stun2.l.google.com:19302"]},
                                    {
                                        "urls": ["turn:numb.viagenie.ca"],
                                        "username": "webrtc@example.com",
                                        "credential": "webrtcpassword"
                                    }
                                ]
                            })
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
                            logger.info("<<< Sent Remote Answer to '%s'. Handshake complete.", reply_target)
                    
                    await asyncio.sleep(0.2)
                except Exception as e:
                    logger.error(f"Signaling listener error: {e}")
                    await asyncio.sleep(2)
        
        asyncio.create_task(_signaling_listener())

        # --- CLOUDFLARE TUNNEL (Preferred) ---
        def _run_cf():
            try:
                import re, subprocess, threading, os
                from src.core.utils import resource_path
                # Use bundled cloudflared.exe via resource_path
                cmd = str(resource_path("cloudflared.exe"))
                if not os.path.exists(cmd):
                    # Fallback to system path or common Windows locations
                    cmd = "cloudflared"
                    if os.name == 'nt':
                        common_paths = [
                            os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "cloudflared", "cloudflared.exe"),
                            os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "cloudflared", "cloudflared.exe"),
                        ]
                        for p in common_paths:
                            if os.path.exists(p):
                                cmd = p
                                break

                # Custom Domain support: Use Token if provided in .env
                cf_token = os.getenv("CLOUDFLARE_TOKEN")
                if cf_token:
                    print(f"  * Starting Persistent Tunnel with Token...")
                    tunnel_args = [cmd, "tunnel", "--no-autoupdate", "run", "--token", cf_token]
                    # Prioritize HUB_URL from .env if it exists
                    app.state.cloudflare_url = os.getenv("HUB_URL")
                else:
                    # Use HTTPS if certificates are found, otherwise fallback to HTTP
                    from src.core.utils import resource_path
                    local_proto = "https" if resource_path("cert.pem").exists() else "http"
                    
                    print(f"  * Attempting Quick Tunnel: {local_proto}://127.0.0.1:{port}")
                    tunnel_args = [cmd, "tunnel", "--url", f"{local_proto}://127.0.0.1:{port}"]
                    # Allow self-signed cert for local tunnel connection
                    tunnel_args.extend(["--no-tls-verify", "--origin-server-name", "localhost"])
                    
                    print(f"  * Command: {' '.join(tunnel_args)}")

                # Wait for server to be fully ready before starting tunnel to avoid 502 Bad Gateway
                time.sleep(2)

                proc = subprocess.Popen(
                    tunnel_args,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                )
                app.state.cf_proc = proc
                
                # Monitor logs for the random .trycloudflare.com URL
                for line in iter(proc.stdout.readline, ""):
                    # Look for the URL regardless of whether we have a token (for verification)
                    match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
                    if match:
                        detected_url = match.group(0)
                        if not app.state.cloudflare_url:
                            app.state.cloudflare_url = detected_url
                        logger.info("Cloudflare Tunnel active: %s", detected_url)
                        # Don't break; keep logging in the background
            except Exception as e:
                print(f"  ! Cloudflare Error: {e}")
                logger.error("Cloudflare Tunnel failed: %s", e)

        import threading
        threading.Thread(target=_run_cf, daemon=True).start()

        discovery.start()
        # Wait a bit for Cloudflare (if it's starting)
        for _ in range(50): # Wait up to 5 seconds
            if getattr(app.state, "cloudflare_url", None): break
            await asyncio.sleep(0.1)

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
            
        if hasattr(app.state, "cf_proc"):
            logger.info("Closing Cloudflare tunnel...")
            app.state.cf_proc.terminate()
            
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
    vision_worker = AsyncVisionWorker(CONFIG) # For remote mobile streams
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
    app.state.active_mode = 0 # 0=Cursor, 1=Canvas, 2=Builder
    app.state.canvas = CanvasController(CONFIG)
    app.state.builder = BuilderController(CONFIG)
    
    # Shared state — track live WebSocket sessions for the dashboard
    connected_clients: Dict[str, dict] = {}
    active_hub_dashboards = 0

    # WebRTC Signaling Hub with Timestamp tracking for cleanup
    signals: Dict[str, dict] = {} # {id: {"q": Queue, "last_poll": timestamp}}

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
        return JSONResponse({"ok": True, "hostname": socket.gethostname(), "ip": detect_lan_ip()})

    @app.post("/api/validate-token")
    async def validate_token_endpoint(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        """Let mobile verify its stored token is still valid after a server restart."""
        token = payload.get("token")
        valid = tokens.validate_token(token)
        return JSONResponse({"valid": valid})

    @app.get("/api/connected-clients")
    async def get_connected_clients() -> JSONResponse:
        return JSONResponse({"clients": list(connected_clients.values())})

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
            except: pass
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
                except: cpu = 5 # Placeholder if wmic fails
            
            return JSONResponse({
                "cpu": cpu,
                "storage_total": usage.total,
                "storage_free": usage.free,
                "storage_percent": round((usage.used / usage.total) * 100, 1)
            })
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    async def _get_network_profile() -> str:
        if platform.system() != "Windows": return "Unknown"
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

    async def _hub_camera_loop():
        global hub_video_frame, hub_camera_active
        import cv2
        
        indices = [0, 1, 2] # Try multiple common camera IDs
        cap = None
        
        for idx in indices:
            logger.info(f"Hub camera loop: Trying index {idx}...")
            cap = cv2.VideoCapture(idx)
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
            logger.error("Hub camera loop: Could not find a working camera after trying indices 0, 1, 2.")
            app.state.camera_active = False
            hub_camera_active = False
            return

        hub_camera_active = True
        consecutive_failures = 0
        loop = asyncio.get_event_loop()
        try:
            while app.state.camera_active:
                ret, frame = cap.read()
                if not ret:
                    consecutive_failures += 1
                    if consecutive_failures > 30:  # ~1 second of failure
                        logger.error("Hub camera loop: Too many consecutive frame failures. Exiting.")
                        break
                    await asyncio.sleep(0.1) # 100ms polling for faster signaling
                    continue

                consecutive_failures = 0
                # Flip at the start so AI and Display are ALWAYS in sync
                frame = cv2.flip(frame, 1)
                
                try:
                    is_special_mode = app.state.active_mode != 0
                    state = await loop.run_in_executor(
                        None, vision_processor.process_frame_sync, frame, is_special_mode
                    )
                    state.active_mode = app.state.active_mode

                    # --- Handle Mode Switching ---
                    if state.mode_switch:
                        app.state.active_mode = (app.state.active_mode + 1) % 3
                        logger.info(f"Mode switched! Active: {app.state.active_mode}")

                    # --- Mode Logic ---
                    if app.state.active_mode == 1: # CANVAS
                        app.state.canvas.update(state.gesture.value, state.cursor_x, state.cursor_y)
                        state.canvas_paths = app.state.canvas.paths
                    elif app.state.active_mode == 2: # BUILDER
                        if state.gesture == Gesture.THUMB_PINCH:
                             app.state.builder.handle_thumb_pinch_drag(
                                 state.cursor_x, state.cursor_y, 640, 480, # Base dims
                                 (state.cursor_x, state.cursor_y), True
                             )
                        else:
                            app.state.builder.update(
                                state.gesture.value, state.cursor_x, state.cursor_y, 640, 480, state
                            )
                        state.builder_ghost = app.state.builder.ghost
                        state.builder_world = app.state.builder.world
                    elif app.state.active_mode == 0 and state.gesture != Gesture.IDLE:
                        mouse.update(state)

                    # --- Annotate and Encode ---
                    annotated_frame = vision_processor.draw_landmarks(frame, state)
                    _, jpeg = cv2.imencode('.jpg', annotated_frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    hub_video_frame = jpeg.tobytes()
                except Exception as e:
                    logger.error(f"Hub loop error: {e}")
                    _, frame_jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    hub_video_frame = frame_jpeg.tobytes()
                await asyncio.sleep(0.01) # Reduced to ms for faster response
        except Exception as e:
            logger.error(f"Hub camera loop crashed: {e}")
        finally:
            if cap: cap.release()
            app.state.camera_active = False
            hub_camera_active = False
            hub_video_frame = None
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

        is_hub = (
            not target
            or target in ("localhost", "127.0.0.1", lan_ip)
            or (ngrok_host and target == ngrok_host)
            or (cloudflare_host and target == cloudflare_host)
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
                    except: pass

        # Add Video Track
        pc.addTrack(HubVideoStreamTrack())

    class HubVideoStreamTrack(VideoStreamTrack):
        def __init__(self):
            super().__init__()
            self._frame_count = 0

        async def recv(self):
            global hub_video_frame
            if not hub_video_frame:
                # Wait for a frame or return a black/placeholder frame to keep the stream alive
                await asyncio.sleep(0.1)
                # In a real scenario, we might want to throw a specific error or send a blank frame
                # For now, just wait.
                return await self.recv()
            
            # Convert bytes to VideoFrame
            from PIL import Image
            import numpy as np
            
            img = Image.open(io.BytesIO(hub_video_frame)).convert("RGB")
            frame = VideoFrame.from_image(img)
            frame.pts = self._frame_count
            frame.time_base = 1 / 30
            self._frame_count += 1
            
            # Throttle to ~30fps to save bandwidth
            await asyncio.sleep(1/30)
            return frame

    @app.post("/api/webrtc/offer")
    async def webrtc_offer(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        offer = RTCSessionDescription(sdp=payload["sdp"], type=payload["type"])
        
        # Add STUN + TURN servers for hotspot/double-NAT traversal
        pc = RTCPeerConnection(configuration={
            "iceServers": [
                # STUN servers (single-NAT traversal)
                {"urls": ["stun:stun.l.google.com:19302"]},
                {"urls": ["stun:stun1.l.google.com:19302"]},
                {"urls": ["stun:stun2.l.google.com:19302"]},
                # TURN servers (double-NAT fallback for hotspots)
                {
                    "urls": ["turn:numb.viagenie.ca"],
                    "username": "webrtc@example.com",
                    "credential": "webrtcpassword"
                }
            ]
        })
        setup_pc(pc)

        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        return JSONResponse({
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type
        })

    @app.get("/api/hub/camera/stream")
    async def hub_camera_stream():
        async def frame_generator():
            global hub_video_frame
            last_frame = None
            while hub_camera_active:
                if hub_video_frame and hub_video_frame is not last_frame:
                    last_frame = hub_video_frame
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + hub_video_frame + b'\r\n')
                await asyncio.sleep(0.01)  # Faster stream updates
        return StreamingResponse(frame_generator(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/api/hub/camera/status")
    async def get_hub_camera_status():
        return JSONResponse({"active": hub_camera_active})

    @app.get("/api/hub/mode")
    async def get_hub_mode():
        return JSONResponse({"mode": app.state.active_mode})

    @app.post("/api/hub/mode")
    async def set_hub_mode(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        mode = payload.get("mode", 0)
        app.state.active_mode = mode % 3
        logger.info(f"Hub mode set to {app.state.active_mode} via API")
        return JSONResponse({"ok": True, "mode": app.state.active_mode})

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
            logger.warning(f"Invalid PIN from {client_ip}. Expected {tokens.current_pin}, got {pin}")
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
        import os, signal
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
        if not ip: return JSONResponse({"ok": False}, status_code=400)
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
        _load_settings() # Re-apply local
        
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
            await asyncio.sleep(0.1) # Debounced rejection
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

        is_local = (
            target is None
            or target == local_ip
            or target == "localhost"
            or (ngrok_host and target == ngrok_host)
            or (cloudflare_host and target == cloudflare_host)
        )

        if not is_local:
            logger.info("RELAY PATH: proxying %s -> Agent %s", client_ip, target)
            agent_url = f"wss://{target}:8001/ws?token=hub_internal"
            # Create a permissive SSL context that accepts self-signed certificates.
            # ssl=False would disable SSL entirely (wrong — Agent requires WSS).
            # ssl=True would validate the cert (fails for self-signed).
            import ssl as _ssl
            ssl_ctx = _ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = _ssl.CERT_NONE
            try:
                async with websockets.connect(agent_url, ssl=ssl_ctx) as agent_ws:
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
        import time, os
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
                        asyncio.create_task(_handle_vision_frame(ws, msg["bytes"], vision_worker, mouse))
                    elif "text" in msg:
                        await _handle_ws_message(ws, msg, vision_worker, mouse)
            except WebSocketDisconnect:
                pass
            except Exception as e:
                if "receive" not in str(e): # Suppress noisy disconnect errors
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
                        pass
                asyncio.create_task(check_shutdown())

    async def _handle_vision_frame(responder, frame_bytes, vision, mouse):
        # AsyncVisionWorker handles the queue and process management
        result = await vision.process_frame(frame_bytes)
        if result:
            state, _ = result
            if state:
                status = mouse.update(state)
                try:
                    await responder.send_json({"status": status, "type": "gesture"})
                except: pass

    async def _handle_ws_message(responder, msg, vision, mouse):
        try:
            data = json.loads(msg["text"])
            mtype = data.get("type")
            
            if (mtype in ("touch", "move")):
                mouse.handle_touch_move(float(data.get("dx", 0)), float(data.get("dy", 0)))
                # No response needed for high-frequency moves
            elif mtype == "click":
                res = mouse.handle_click(data.get("button", "left"))
                if responder: await responder.send_json({"status": res})
            elif mtype in ("click_down", "click_up"):
                is_down = (mtype == "click_down")
                res = mouse.handle_click_state(data.get("button", "left"), is_down)
                if responder: await responder.send_json({"status": res})
            elif mtype == "scroll":
                res = mouse.handle_touch_scroll(float(data.get("dy", 0)))
                if responder: await responder.send_json({"status": res})
            elif mtype == "zoom":
                res = mouse.handle_touch_zoom(float(data.get("delta", 0)))
                if responder: await responder.send_json({"status": res})
            elif mtype == "shortcut":
                res = mouse.handle_touch_shortcut(data.get("slot", ""))
                if responder: await responder.send_json({"status": res})
            elif mtype == "key":
                key = data.get("key")
                if key:
                    res = mouse.handle_key(key)
                    if responder: await responder.send_json({"status": res})
            elif mtype == "hotkey":
                keys = data.get("keys", [])
                if keys:
                    res = mouse.handle_hotkey(keys)
                    if responder: await responder.send_json({"status": res})
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
            
        # Prioritize Cloudflare > ngrok
        remote_url = getattr(app.state, "cloudflare_url", None) or getattr(app.state, "ngrok_url", None) or os.getenv("NGROK_URL")
        
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
        frontend_base = os.getenv("FRONTEND_URL")
        
        # Use X-Forwarded-Host if behind a tunnel (Cloudflare, ngrok)
        host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
        # Remove port if present for hostname check
        hostname = host.split(":")[0] if ":" in host else host
        
        # Detect cloud presence (headers or non-local hostname)
        is_cloud_header = any(h in request.headers for h in ["cf-ray", "cf-connecting-ip", "x-ngrok-file-config"])
        
        def is_private_ip(ip):
            if ip in ("localhost", "127.0.0.1", "::1"): return True
            if ip.startswith("192.168.") or ip.startswith("10."): return True
            if ip.startswith("172."):
                parts = ip.split(".")
                if len(parts) >= 2 and parts[1].isdigit():
                    sec = int(parts[1])
                    return 16 <= sec <= 31
            return False

        is_local = not is_cloud_header and (is_private_ip(hostname) or hostname.endswith(".local"))
        logger.info(f"QR Request - Host: {host}, is_local: {is_local}, is_cloud: {is_cloud_header}")
        
        if url:
            target = url
        elif not is_local and frontend_base:
            # HUB is cloud/domain deployed (e.g. Cloudflare, ngrok, custom domain)
            target = f"{frontend_base.rstrip('/')}/?hub={host}"
        else:
            # HUB is running on localhost or LAN -> show direct IP link
            # We now use http for local connections to avoid SSL trust issues on hotspots
            proto = "http"
            lan_ip = detect_lan_ip()
            target = f"{proto}://{lan_ip}:{port}"
            
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

    async def _rotate_pin_periodically():
        while True:
            await asyncio.sleep(1800) # 30 minutes
            tokens.reset_pin()
            logger.info("Background PIN rotation triggered.")

    return app

def run():
    import multiprocessing
    if platform.system() == "Windows":
        multiprocessing.freeze_support()
        try:
            multiprocessing.set_start_method('spawn', force=True)
        except RuntimeError: pass

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
    key  = resource_path("key.pem")
    ssl = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)} if cert.exists() else {}
    
    app = build_app(args.host, args.port)
    
    # Apply filter to uvicorn access logs to prevent console spam
    logging.getLogger("uvicorn.access").addFilter(EndpointFilter())
    
    uvicorn.run(app, host=args.host, port=args.port, **ssl)

if __name__ == "__main__":
    run()
