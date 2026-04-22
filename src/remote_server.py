"""
remote_server.py — FastAPI + WebSocket backend for mobile camera streaming.

The browser client captures camera frames and sends JPEG bytes over WebSocket.
The PC runs gesture inference and executes local mouse/shortcut actions.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import socket
from pathlib import Path
from dotenv import load_dotenv

load_dotenv() # Load USE_MODAL and other env vars
logger = logging.getLogger("gesture_control.remote")

import cv2
import numpy as np
from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
import uvicorn
import qrcode

from src.config import CONFIG
from src.controller import MouseController
from src.shortcuts import ShortcutManager
from src.vision import VisionProcessor

logger = logging.getLogger("gesture_control.remote")

APP_DIR = Path(__file__).resolve().parent
CLIENT_HTML = APP_DIR / "web" / "remote_client.html"
SETTINGS_FILE = APP_DIR / "settings.json"

_TRUSTED_IPS: set[str] = set()


def _save_settings(sensitivity: int, scroll_speed: int):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump({"sensitivity": sensitivity, "scroll_speed": scroll_speed}, f)
    except Exception as e:
        logger.error("Failed to save settings: %s", e)


def _load_settings():
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r") as f:
                data = json.load(f)
                sens = data.get("sensitivity", 50)
                scroll = data.get("scroll_speed", 20)
                # Apply to CONFIG
                alpha = 0.05 + (sens - 5) / 90.0 * 0.45
                thresh = 8.0 - (sens - 5) / 90.0 * 7.0
                CONFIG.gesture.smoothing = alpha
                CONFIG.gesture.move_threshold_px = max(0.5, thresh)
                CONFIG.gesture.scroll_speed = int(scroll)
                logger.info("Loaded settings: sens=%d, scroll=%d", sens, scroll)
        except Exception as e:
            logger.error("Failed to load settings: %s", e)


async def _request_consent(ip: str) -> bool:
    """Auto-approve remote control connections for remote usage."""
    logger.info("Auto-approving connection from %s", ip)
    return True


def _detect_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            return ip
    except OSError:
        return "127.0.0.1"


def build_app(host: str = "0.0.0.0", port: int = 8000) -> FastAPI:
    app = FastAPI(title="GestureLink Remote Backend", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    vision = VisionProcessor(CONFIG)
    shortcuts = ShortcutManager()
    mouse = MouseController(CONFIG, shortcuts=shortcuts, responsive=True)
    secret = os.environ.get("GESTURELINK_TOKEN", "").strip()

    lan_host = _detect_lan_ip() if host in ("0.0.0.0", "::") else host
    lan_url = f"http://{lan_host}:{port}"

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.get("/api/shortcuts")
    async def get_shortcuts() -> JSONResponse:
        return JSONResponse({"shortcuts": shortcuts.get_bindings()})

    @app.get("/api/apps")
    async def get_apps() -> JSONResponse:
        return JSONResponse({"apps": shortcuts.list_discovered_apps(limit=300)})

    @app.post("/api/shortcuts")
    async def set_shortcuts(payload: dict = Body(default_factory=dict)) -> JSONResponse:
        raw_shortcuts = payload.get("shortcuts", {}) if isinstance(payload, dict) else {}
        if not isinstance(raw_shortcuts, dict):
            return JSONResponse({"ok": False, "error": "Invalid payload"}, status_code=400)
        shortcuts.set_bindings(raw_shortcuts)
        return JSONResponse({"ok": True, "shortcuts": shortcuts.get_bindings()})

    @app.get("/api/settings")
    async def get_settings() -> JSONResponse:
        # Map smoothing (0.05-0.5) back to sensitivity (5-95)
        s = CONFIG.gesture.smoothing
        sensitivity = int((s - 0.05) / 0.45 * 90 + 5)
        return JSONResponse({
            "sensitivity": sensitivity,
            "scroll_speed": CONFIG.gesture.scroll_speed
        })

    @app.post("/api/settings")
    async def set_settings(payload: dict = Body(...)) -> JSONResponse:
        sens = payload.get("sensitivity", 50)
        scroll_spd = payload.get("scroll_speed", 20)
        # Map sensitivity (5-95) to smoothing (0.05-0.5)
        # and move_threshold_px (8.0 down to 1.0)
        alpha = 0.05 + (sens - 5) / 90.0 * 0.45
        thresh = 8.0 - (sens - 5) / 90.0 * 7.0
        CONFIG.gesture.smoothing = alpha
        CONFIG.gesture.move_threshold_px = max(0.5, thresh)
        CONFIG.gesture.scroll_speed = int(scroll_spd)
        _save_settings(sens, scroll_spd)
        logger.info("Updated settings: sens=%d, scroll=%d", sens, scroll_spd)
        return JSONResponse({"ok": True})

    @app.get("/lan-info")
    async def lan_info() -> JSONResponse:
        return JSONResponse(
            {
                "lan_url": lan_url,
                "qr_endpoint": f"{lan_url}/lan-qr.png",
            }
        )

    @app.get("/lan-qr.png")
    async def lan_qr_png() -> StreamingResponse:
        qr = qrcode.QRCode(version=1, box_size=10, border=2)
        qr.add_data(lan_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return StreamingResponse(buf, media_type="image/png")

    @app.get("/api/ping")
    async def ping() -> JSONResponse:
        return JSONResponse({"ok": True, "hostname": socket.gethostname(), "ip": _detect_lan_ip()})

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(CLIENT_HTML)

    @app.get("/join")
    async def join_script(request: Request) -> StreamingResponse:
        """Serves a bootstrap script based on the requester's OS."""
        user_agent = request.headers.get("user-agent", "").lower()
        
        if "windows" in user_agent or "powershell" in user_agent:
            # Serve PowerShell script
            script = f"""
Write-Host "--- GestureLink Windows Join ---" -ForegroundColor Cyan
$url = "http://{request.base_url.host}:{request.base_url.port}/join"
# In a real scenario, we would download the zip here.
# For now, we assume the folder is synced or we provide a download command.
Write-Host "Starting GestureLink Bootstrap..." -ForegroundColor Cyan
python -m src.bootstrap_server --host 0.0.0.0 --port 8765
"""
            media_type = "text/plain" # PowerShell iex likes text
        else:
            # Serve Bash script
            script = f"""#!/bin/bash
echo "--- GestureLink Linux Join ---"
echo "Setting up GestureLink Agent..."
PYTHONPATH=. python3 -m src.bootstrap_server --host 0.0.0.0 --port 8765
"""
            media_type = "text/x-shellscript"

        return StreamingResponse(io.BytesIO(script.encode()), media_type=media_type)

    @app.get("/uninstall")
    async def uninstall_script() -> StreamingResponse:
        """Serves a bash script that removes the GestureLink service from the target PC."""
        script = f"""#!/bin/bash
echo "--- GestureLink Uninstaller ---"
SERVICE_FILE="/etc/systemd/system/gesturelink.service"
sudo systemctl stop gesturelink || true
sudo systemctl disable gesturelink || true
if [ -f "$SERVICE_FILE" ]; then
    sudo rm "$SERVICE_FILE"
    sudo systemctl daemon-reload
fi
echo "GestureLink successfully uninstalled."
"""
        return StreamingResponse(io.BytesIO(script.encode()), media_type="text/x-shellscript")

    async def _authenticate_ws(ws: WebSocket, secret: str | None) -> bool:
        if not secret:
            return True
        try:
            intro = await asyncio.wait_for(ws.receive_json(), timeout=10)
            token = str(intro.get("token", "")).strip() if isinstance(intro, dict) else ""
            if token == secret:
                return True
            await ws.close(code=1008, reason="Invalid auth token")
        except Exception:
            await ws.close(code=1008, reason="Missing auth handshake")
        return False

    @app.websocket("/ws")
    async def ws_frames(ws: WebSocket) -> None:
        await ws.accept()
        if not await _authenticate_ws(ws, secret):
            return

        # --- Connection Consent ---
        client_ip = ws.client.host if ws.client else "unknown"
        if client_ip != "127.0.0.1" and client_ip not in _TRUSTED_IPS:
            logger.info("Incoming connection request from %s. Waiting for user approval...", client_ip)
            if not await _request_consent(client_ip):
                logger.warning("Connection from %s REJECTED by user.", client_ip)
                await ws.send_json({"error": "Connection rejected by target PC"})
                await ws.close(code=1008)
                return
            _TRUSTED_IPS.add(client_ip)
            logger.info("Connection from %s ACCEPTED.", client_ip)

        try:
            while True:
                data = await ws.receive_bytes()
                logger.info("Received frame bytes: %d", len(data))
                frame = vision.decode_frame(data)
                if frame is not None:
                    state = await vision.process_frame(frame, builder_mode=False)
                    status = mouse.update(state)
                    await ws.send_json({
                        "status": status
                    })
        except WebSocketDisconnect:
            logger.info("Remote client disconnected")
        except Exception as exc:
            import traceback
            logger.error("Remote session error trace:\n%s", traceback.format_exc())
            logger.warning("Remote session error: %s", exc)
            try:
                await ws.send_json({"error": str(exc)})
            except Exception:
                pass

    @app.on_event("shutdown")
    def _cleanup() -> None:
        vision.close()

    @app.on_event("startup")
    async def startup() -> None:
        _load_settings()

    return app


def run_remote_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    )
    logger.info("Starting remote server on http://%s:%d", host, port)
    uvicorn.run(
        build_app(host=host, port=port),
        host=host,
        port=port,
        log_level="info",
        ws="wsproto",
    )

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    run_remote_server(host=args.host, port=args.port)
