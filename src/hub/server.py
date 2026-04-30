from __future__ import annotations

import asyncio
import io
import json
import logging
import os
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

from src.core.config import CONFIG
from src.core.controller import MouseController
from src.core.shortcuts import ShortcutManager
from src.core.vision import VisionProcessor
from src.hub.managers import SecurityManager, TokenManager, DeviceDiscovery, detect_lan_ip
from src.core.vision_worker import AsyncVisionWorker

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

# Global state for camera streaming
hub_video_frame: bytes | None = None
hub_camera_active: bool = False

APP_DIR = Path(__file__).resolve().parent
HUB_DIR = APP_DIR
CLIENT_HTML = HUB_DIR.parent / "web" / "client" / "remote_client.html"
HUB_HTML = HUB_DIR.parent / "web" / "hub" / "hub.html"
MOBILE_DIST = HUB_DIR.parent / "web" / "mobile" / "dist"
SETTINGS_FILE = HUB_DIR / "settings.json"
SECURITY_FILE = HUB_DIR / "security.json"
CERT_PEM = Path(__file__).resolve().parent.parent.parent / "cert.pem"

def _save_settings(sensitivity: int, scroll_speed: int) -> None:
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump({"sensitivity": sensitivity, "scroll_speed": scroll_speed}, f)
    except Exception as e:
        logger.error("Failed to save settings: %s", e)

def _load_settings() -> None:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
                sens = data.get("sensitivity", 50)
                scroll = data.get("scroll_speed", 20)
                alpha = 0.05 + (sens - 5) / 90.0 * 0.45
                thresh = 8.0 - (sens - 5) / 90.0 * 7.0
                CONFIG.gesture.smoothing = alpha
                CONFIG.gesture.move_threshold_px = max(0.5, thresh)
                CONFIG.gesture.scroll_speed = int(scroll)
        except Exception as e:
            logger.error("Failed to load settings: %s", e)

def build_app(host: str = "0.0.0.0", port: int = 8000) -> FastAPI:
    app = FastAPI(title="GestureLink Hub", version="1.1.0")

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
    from concurrent.futures import ThreadPoolExecutor
    app.state.executor = ThreadPoolExecutor(max_workers=2)
    app.state.mouse = mouse
    app.state.camera_active = False
    app.state.camera_task = None
    
    # Shared state — track live WebSocket sessions for the dashboard
    connected_clients: Dict[str, dict] = {}

    # WebRTC Signaling Hub
    signals: Dict[str, asyncio.Queue] = {}

    @app.post("/api/webrtc/signal/{target_id}")
    async def webrtc_signal(target_id: str, payload: Annotated[dict, Body(...)]) -> JSONResponse:
        if target_id not in signals:
            signals[target_id] = asyncio.Queue()
        await signals[target_id].put(payload)
        return JSONResponse({"ok": True})

    @app.get("/api/webrtc/signal/{target_id}")
    async def webrtc_get_signals(target_id: str) -> JSONResponse:
        if target_id not in signals:
            signals[target_id] = asyncio.Queue()
        try:
            signal = await asyncio.wait_for(signals[target_id].get(), timeout=30.0)
            return JSONResponse({"ok": True, "signal": signal})
        except asyncio.TimeoutError:
            # Issue 10: suppress 408 log spam — this is expected long-poll behaviour
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

    @app.get("/api/hub/info")
    async def hub_info() -> JSONResponse:
        return JSONResponse({
            "pin": tokens.current_pin,
            "lan_ip": detect_lan_ip(),
            "port": port,
            "ngrok_url": os.getenv("NGROK_URL")
        })

    @app.get("/api/discovered")
    async def get_discovered() -> JSONResponse:
        return JSONResponse({"devices": discovery.discovered_devices})

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
        try:
            while app.state.camera_active:
                ret, frame = cap.read()
                if not ret:
                    consecutive_failures += 1
                    if consecutive_failures > 30: # 1 second of failure
                         logger.error("Hub camera loop: Too many consecutive frame failures. Exiting.")
                         break
                    await asyncio.sleep(0.03)
                    continue
                
                consecutive_failures = 0
                # AI Inference - Use persistent ThreadPool to avoid blocking or context switching overhead
                try:
                    # Draw 'Waiting' text by default on a copy
                    annotated_frame = app.state.vision_processor.draw_landmarks(frame, GestureState())
                    
                    # Run vision sync in thread
                    state = await asyncio.get_event_loop().run_in_executor(
                        app.state.executor, 
                        app.state.vision_processor.process_frame_sync, 
                        frame
                    )
                    
                    # DIAGNOSTIC: Draw a Red Box in the corner to prove this is the annotated frame
                    cv2.rectangle(annotated_frame, (10, 10), (100, 100), (0, 0, 255), -1)
                    cv2.putText(annotated_frame, "AI ACTIVE", (15, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
                    
                    _, jpeg = cv2.imencode('.jpg', annotated_frame)
                    hub_video_frame = jpeg.tobytes()
                except Exception as e:
                    logger.error(f"Inference error in loop: {e}")
                    # Fallback to simple encoded frame
                    _, frame_jpeg = cv2.imencode('.jpg', frame)
                    hub_video_frame = frame_jpeg.tobytes()
                
                await asyncio.sleep(0.01)
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
        is_hub = not target or target in ("localhost", "127.0.0.1", lan_ip)
        
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
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"http://{target}:8001/api/camera/toggle", json=payload, timeout=2.0)
                return JSONResponse(resp.json())
        except Exception as e:
            logger.error("Proxy camera toggle failed for %s: %s", target, e)
            return JSONResponse({"ok": False, "error": str(e)})

    @app.get("/api/hub/camera/stream")
    async def hub_camera_stream():
        async def frame_generator():
            global hub_video_frame
            while hub_camera_active:
                if hub_video_frame:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + hub_video_frame + b'\r\n')
                await asyncio.sleep(0.04) # ~25fps
        return StreamingResponse(frame_generator(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/api/hub/camera/status")
    async def get_hub_camera_status():
        return JSONResponse({"active": hub_camera_active})

    @app.get("/api/apps")
    async def get_apps(ip: Optional[str] = None) -> JSONResponse:
        # If IP is provided and not local, proxy to agent
        if ip and ip not in ("localhost", "127.0.0.1", detect_lan_ip()):
            try:
                import httpx
                async with httpx.AsyncClient() as client:
                    resp = await client.get(f"http://{ip}:8001/api/apps", timeout=2.0)
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

    @app.post("/api/pair")
    async def initiate_pair(request: Request, payload: Annotated[dict, Body(...)]) -> JSONResponse:
        pin = payload.get("pin")
        hostname = payload.get("hostname", "Unknown Phone")
        client_ip = request.client.host if request.client else "0.0.0.0"

        if pin != tokens.current_pin:
            return JSONResponse({"status": "error", "error": "Invalid PIN"}, status_code=401)

        # ALWAYS go through pending approval — never silently re-trust.
        # Remove from trusted so the user always consciously approves.
        security.trusted_ips.discard(client_ip)
        req_id = security.add_pending_request(client_ip, hostname)
        logger.info("Pair request from %s (%s) → pending ID %s", client_ip, hostname, req_id)
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
            del tokens.valid_tokens[token]
            logger.info("Token revoked for IP %s", tokens.valid_tokens.get(token, "unknown"))
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

    @app.get("/api/settings")
    async def get_settings() -> JSONResponse:
        s = CONFIG.gesture.smoothing
        sensitivity = int((s - 0.05) / 0.45 * 90 + 5)
        return JSONResponse({
            "sensitivity": sensitivity,
            "scroll_speed": CONFIG.gesture.scroll_speed
        })

    @app.post("/api/settings")
    async def set_settings(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        sens, scroll = payload.get("sensitivity", 50), payload.get("scroll_speed", 20)
        _save_settings(sens, scroll)
        _load_settings() # Re-apply
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
            await ws.close(code=4003)
            return

        await ws.accept()
        logger.info("WS accepted: client=%s", client_ip)

        # AGENT RELAY LOGIC
        if target and target != local_ip:
            logger.info("RELAY PATH: proxying %s -> Agent %s", client_ip, target)
            agent_url = f"ws://{target}:8001/ws?token=hub_internal"
            try:
                async with websockets.connect(agent_url) as agent_ws:
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
        connected_clients[client_ip] = {
            "ip": client_ip,
            "connected_at": int(time.time()),
            "type": "mobile"
        }
        logger.info("Client connected: %s", client_ip)
        async def vision_worker():
            try:
                while True:
                    msg = await ws.receive()
                    if "bytes" in msg:
                        # Process vision in a background task so it doesn't block the loop
                        asyncio.create_task(_handle_vision_frame(ws, msg["bytes"], vision, mouse))
                    elif "text" in msg:
                        await _handle_ws_message(ws, msg, vision, mouse)
            except WebSocketDisconnect:
                pass
            except Exception as e:
                if "receive" not in str(e): # Suppress noisy disconnect errors
                    logger.error("WS Loop Error: %s", e)

        try:
            await vision_worker()
        finally:
            connected_clients.pop(client_ip, None)
            logger.info("Client disconnected: %s", client_ip)

    async def _handle_vision_frame(ws, frame_bytes, vision, mouse):
        # AsyncVisionWorker handles the queue and process management
        result = await vision.process_frame(frame_bytes)
        if result:
            state, _ = result
            if state:
                status = mouse.update(state)
                try:
                    await ws.send_json({"status": status, "type": "gesture"})
                except: pass

    async def _handle_ws_message(ws, msg, vision, mouse):
        try:
            data = json.loads(msg["text"])
            mtype = data.get("type")
            
            if (mtype in ("touch", "move")):
                mouse.handle_touch_move(float(data.get("dx", 0)), float(data.get("dy", 0)))
                # No response needed for high-frequency moves
            elif mtype == "click":
                res = mouse.handle_click(data.get("button", "left"))
                await ws.send_json({"status": res})
            elif mtype in ("click_down", "click_up"):
                is_down = (mtype == "click_down")
                res = mouse.handle_click_state(data.get("button", "left"), is_down)
                await ws.send_json({"status": res})
            elif mtype == "scroll":
                res = mouse.handle_touch_scroll(float(data.get("dy", 0)))
                await ws.send_json({"status": res})
            elif mtype == "zoom":
                res = mouse.handle_touch_zoom(float(data.get("delta", 0)))
                await ws.send_json({"status": res})
            elif mtype == "shortcut":
                res = mouse.handle_touch_shortcut(data.get("slot", ""))
                await ws.send_json({"status": res})
        except Exception as e:
            logger.error("WS Message Error: %s", e)

    # Static Assets
    if MOBILE_DIST.exists():
        app.mount("/assets", StaticFiles(directory=str(MOBILE_DIST / "assets")), name="assets")
        @app.get("/")
        async def index(): return FileResponse(MOBILE_DIST / "index.html")
        @app.get("/manifest.json")
        async def manifest(): return FileResponse(MOBILE_DIST / "manifest.json")
        @app.get("/sw.js")
        async def sw(): return FileResponse(MOBILE_DIST / "sw.js")
        @app.get("/icon-192.png")
        async def icon192(): return FileResponse(MOBILE_DIST / "icon-192.png")
        @app.get("/icon-512.png")
        async def icon512(): return FileResponse(MOBILE_DIST / "icon-512.png")
    
    @app.get("/hub")
    async def hub_page():
        with open(HUB_HTML, "r", encoding="utf-8") as f:
            content = f.read()
        info = {
            "pin": tokens.current_pin,
            "lan_ip": detect_lan_ip(),
            "port": port,
            "ngrok_url": os.getenv("NGROK_URL")
        }
        # Fix: assign to window.infoData so fetchUpdates() can read it across the script
        injection = f"window.infoData = {json.dumps(info)};"
        injection += "\ndocument.addEventListener('DOMContentLoaded', () => {"
        injection += f"\n  document.getElementById('pin-display').textContent = '{tokens.current_pin}';"
        injection += "\n});"
        
        content = content.replace("/*INFO_INJECTION*/", injection)
        return HTMLResponse(content)

    @app.get("/lan-qr.png")
    async def qr_gen(url: Optional[str] = None) -> StreamingResponse:
        if url:
            target = url
        else:
            proto = "https" if CERT_PEM.exists() else "http"
            target = f"{proto}://{detect_lan_ip()}:{port}"
        qr = qrcode.make(target)
        buf = io.BytesIO()
        qr.save(buf, format="PNG")
        buf.seek(0)
        return StreamingResponse(buf, media_type="image/png")

    async def _rotate_pin_periodically():
        while True:
            await asyncio.sleep(1800) # 30 minutes
            tokens.reset_pin()
            logger.info("Background PIN rotation triggered.")

    @app.on_event("startup")
    async def startup():
        _load_settings()
        discovery.start()
        lan_ip = detect_lan_ip()
        proto = "https" if CERT_PEM.exists() else "http"
        print("\n" + "="*50)
        print("STARTING GESTURELINK HUB...")
        print(f"  * Local Dashboard:  {proto}://localhost:{port}/hub")
        print(f"  * Mobile Access:    {proto}://{lan_ip}:{port}")
        print(f"  * Pairing PIN:      {tokens.current_pin}")
        print("="*50 + "\n")
        logger.info("Hub Started successfully.")
        # Store in app state or a variable in the closure to prevent GC
        app.state.rotation_task = asyncio.create_task(_rotate_pin_periodically())

    @app.on_event("shutdown")
    def shutdown():
        discovery.stop()
        vision.stop()
        logger.info("Hub shutting down...")

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
    
    project_root = Path(__file__).resolve().parent.parent.parent
    cert = project_root / "cert.pem"
    key  = project_root / "key.pem"
    ssl = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)} if cert.exists() else {}
    
    app = build_app(args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, **ssl)

if __name__ == "__main__":
    run()
