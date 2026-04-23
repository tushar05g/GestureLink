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
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import qrcode

from src.core.config import CONFIG
from src.core.controller import MouseController
from src.core.shortcuts import ShortcutManager
from src.core.vision import VisionProcessor
from src.hub.managers import SecurityManager, TokenManager, DeviceDiscovery, detect_lan_ip

load_dotenv()
logger = logging.getLogger("gesture_control.remote")

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
    vision = VisionProcessor(CONFIG)
    shortcuts = ShortcutManager()
    mouse = MouseController(CONFIG, shortcuts=shortcuts, responsive=True)
    
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
            return JSONResponse({"ok": False, "error": "timeout"}, status_code=408)

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "healthy", "version": "1.1.0"})

    @app.get("/api/ping")
    async def ping() -> JSONResponse:
        """Lightweight endpoint for network device discovery."""
        return JSONResponse({"ok": True, "hostname": socket.gethostname(), "ip": detect_lan_ip()})

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

    @app.get("/api/apps")
    async def get_apps() -> JSONResponse:
        # Platform-aware app list — Bug #9 fix
        is_windows = platform.system() == "Windows"
        apps = [
            {"name": "Browser",   "target": "chrome" if is_windows else "google-chrome"},
            {"name": "Terminal",  "target": "cmd" if is_windows else "gnome-terminal"},
            {"name": "Spotify",   "target": "spotify"},
            {"name": "VS Code",   "target": "code"},
            {"name": "File Explorer", "target": "explorer" if is_windows else "nautilus"},
        ]
        return JSONResponse({"apps": apps})

    @app.get("/api/shortcuts")
    async def get_shortcuts() -> JSONResponse:
        return JSONResponse({"shortcuts": shortcuts.get_all_shortcuts()})

    @app.post("/api/shortcuts")
    async def set_shortcuts(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        new_shortcuts = payload.get("shortcuts", {})
        shortcuts.update_shortcuts(new_shortcuts)
        return JSONResponse({"ok": True})

    @app.post("/api/pair")
    async def pair(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        if payload.get("pin") == tokens.current_pin:
            token = tokens.generate_token(client_ip="local")
            return JSONResponse({"ok": True, "token": token})
        return JSONResponse({"ok": False, "error": "Invalid PIN"}, status_code=401)

    @app.get("/api/security")
    async def get_security() -> JSONResponse:
        return JSONResponse({
            "trusted": list(security.trusted_ips),
            "blocked": list(security.blocked_ips),
            "pending": list(security.pending_approvals.keys())
        })

    @app.post("/api/security/action")
    async def security_action(payload: Annotated[dict, Body(...)]) -> JSONResponse:
        ip, action = payload.get("ip"), payload.get("action")
        if not ip: return JSONResponse({"ok": False}, status_code=400)
        if action == "trust":
            security.trusted_ips.add(ip)
            security.blocked_ips.discard(ip)
            if ip in security.pending_approvals: security.pending_approvals[ip].set()
        elif action == "block":
            security.blocked_ips.add(ip)
            security.trusted_ips.discard(ip)
            if ip in security.pending_approvals: security.pending_approvals[ip].set()
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
    async def websocket_endpoint(ws: WebSocket, token: Annotated[Optional[str], Query()] = None):
        if not tokens.validate_token(token):
            await ws.close(code=4003)
            return
        client_ip = ws.client.host if ws.client else "unknown"
        if not await security.request_consent(client_ip):
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            while True:
                msg = await ws.receive()
                if "bytes" in msg:
                    frame = vision.decode_frame(msg["bytes"])
                    if frame is not None:
                        state = await vision.process_frame(frame)
                        status = mouse.update(state)
                        await ws.send_json({"status": status})
                elif "text" in msg:
                    data = json.loads(msg["text"])
                    mtype = data.get("type")
                    # Bug #5: accept both 'touch' and 'move' for touchpad compatibility
                    if mtype in ("touch", "move"):
                        res = mouse.handle_touch_move(data.get("dx", 0), data.get("dy", 0))
                        await ws.send_json({"status": res})
                    elif mtype == "click":
                        res = mouse.handle_click(data.get("button", "left"))
                        await ws.send_json({"status": res})
                    elif mtype == "scroll":
                        res = mouse.handle_touch_scroll(data.get("dy", 0))
                        await ws.send_json({"status": res})
        except WebSocketDisconnect: pass
        except Exception as e: logger.error("WS Error: %s", e)

    # Static Assets
    if MOBILE_DIST.exists():
        app.mount("/assets", StaticFiles(directory=str(MOBILE_DIST / "assets")), name="assets")
        @app.get("/")
        async def index(): return FileResponse(MOBILE_DIST / "index.html")
        @app.get("/manifest.json")
        async def manifest(): return FileResponse(MOBILE_DIST / "manifest.json")
        @app.get("/sw.js")
        async def sw(): return FileResponse(MOBILE_DIST / "sw.js")
    
    @app.get("/hub")
    async def hub_page(): return FileResponse(HUB_HTML)

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

    @app.on_event("startup")
    async def startup():
        _load_settings()
        discovery.start()
        lan_ip = detect_lan_ip()
        proto = "https" if CERT_PEM.exists() else "http"
        print("\n" + "═"*50)
        print("🚀 GESTURELINK HUB IS LIVE")
        print(f"  • Local Dashboard:  {proto}://localhost:{port}/hub")
        print(f"  • Mobile Access:    {proto}://{lan_ip}:{port}")
        print(f"  • Pairing PIN:      {tokens.current_pin}")
        print("═"*50 + "\n")
        logger.info("Hub Started successfully.")

    @app.on_event("shutdown")
    def shutdown():
        discovery.stop()
        vision.close()

    return app

def run():
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
