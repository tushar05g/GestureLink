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

AGENT_CODE = """
import json
import socket
import pyautogui
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn
import logging

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="GestureLink Micro-Agent")
pyautogui.FAILSAFE = False

def _detect_lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"

@app.get("/api/ping")
async def ping():
    return {"ok": True, "hostname": socket.gethostname(), "ip": _detect_lan_ip()}

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    logging.info("Connected to controller")
    try:
        while True:
            msg = await ws.receive()
            if "text" in msg:
                try:
                    data = json.loads(msg["text"])
                    mtype = data.get("type")
                    if mtype == "touch":
                        dx, dy = float(data.get("dx", 0)), float(data.get("dy", 0))
                        pyautogui.moveRel(int(dx * 1.5), int(dy * 1.5), _pause=False)
                    elif mtype == "click":
                        pyautogui.click(button=data.get("button", "left"), _pause=False)
                    elif mtype == "scroll":
                        dy = float(data.get("dy", 0))
                        pyautogui.scroll(int(dy * -2), _pause=False)
                except Exception as e:
                    logging.warning(f"Error processing command: {e}")
    except WebSocketDisconnect:
        logging.info("Controller disconnected")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
"""

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

    @app.get("/join")
    async def join_script(request: Request):
        """Serves a bootstrap script based on the requester's OS."""
        user_agent = request.headers.get("user-agent", "").lower()
        
        win_script = (
            "Write-Host '🛠️ Installing GestureLink Micro-Agent...' -ForegroundColor Cyan\n"
            "$DirName = \"$env:USERPROFILE\\.gesturelink-agent\"\n"
            "if (!(Test-Path $DirName)) { New-Item -ItemType Directory -Path $DirName | Out-Null }\n"
            "Set-Location $DirName\n"
            "if (!(Get-Command python -ErrorAction SilentlyContinue)) { Write-Host '❌ Error: Python not found.' -ForegroundColor Red; exit }\n"
            "if (!(Test-Path \".venv\")) { Write-Host '📦 Creating virtual environment...'; python -m venv .venv; & .venv\\Scripts\\pip install fastapi uvicorn pyautogui websockets }\n"
            "@\"\n"
        ) + AGENT_CODE.strip() + (
            "\n\"@ | Out-File -FilePath agent.py -Encoding utf8\n"
            "Write-Host '📄 Registering background task...' -ForegroundColor Green\n"
            "$Action = New-ScheduledTaskAction -Execute \"$DirName\\.venv\\Scripts\\python.exe\" -Argument \"agent.py\" -WorkingDirectory \"$DirName\"\n"
            "$Trigger = New-ScheduledTaskTrigger -AtLogOn\n"
            "$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 365) -Priority 1\n"
            "$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive\n"
            "Register-ScheduledTask -TaskName 'GestureLinkAgent' -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Description 'GestureLink Micro-Agent' -Force\n"
            "Write-Host '🚀 Launching Micro-Agent Backend...' -ForegroundColor Cyan\n"
            "Start-Process -FilePath \"$DirName\\.venv\\Scripts\\python.exe\" -ArgumentList \"agent.py\" -WorkingDirectory \"$DirName\" -WindowStyle Hidden\n"
            "Start-Sleep -Seconds 3\n"
            "if (Test-NetConnection -ComputerName localhost -Port 8000 -InformationLevel Quiet) {\n"
            "    Write-Host '✅ Backend is RUNNING!' -ForegroundColor Green\n"
            "} else {\n"
            "    Write-Host '⚠️ Backend failed to start automatically. Try running it manually with: cd $DirName; .\\.venv\\Scripts\\python.exe agent.py' -ForegroundColor Yellow\n"
            "}\n"
            "Write-Host '🛡️ Do you want to open Port 8000 in the Windows Firewall? (Y/N)' -ForegroundColor Yellow\n"
            "$response = Read-Host\n"
            "if ($response -match '^[yY]') {\n"
            "    if (!(Get-NetFirewallRule -DisplayName 'GestureLinkAgent' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName 'GestureLinkAgent' -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow | Out-Null }\n"
            "    Write-Host 'Firewall rule added.' -ForegroundColor Green\n"
            "}\n"
            "$IP = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notlike '*Loopback*' -and $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1).IPAddress\n"
            "Write-Host '✅ Micro-Agent is now ACTIVE! Add IP:' $IP 'to your phone.' -ForegroundColor Green\n"
        )
        
        lin_script = (
            "#!/bin/bash\n"
            "echo '🛠️ Installing GestureLink Micro-Agent...'\n"
            "DIR_NAME=\"$HOME/.gesturelink-agent\"\n"
            "mkdir -p \"$DIR_NAME\" && cd \"$DIR_NAME\"\n"
            "if ! command -v python3 &> /dev/null; then echo '❌ Error: python3 not found.'; exit 1; fi\n"
            "if [ ! -d \".venv\" ]; then\n"
            "    echo '📦 Creating virtual environment...'\n"
            "    python3 -m venv .venv\n"
            "    source .venv/bin/activate\n"
            "    pip install fastapi uvicorn pyautogui websockets\n"
            "fi\n"
            "cat << 'EOFAGENT' > agent.py\n"
        ) + AGENT_CODE.strip() + (
            "\nEOFAGENT\n"
            "echo '📄 Creating service configuration...'\n"
            "cat << EOFSERVICE | sudo tee /etc/systemd/system/gesturelink-agent.service\n"
            "[Unit]\n"
            "Description=GestureLink Micro-Agent\n"
            "After=network.target\n"
            "[Service]\n"
            "Type=simple\n"
            "User=$USER\n"
            "Environment=DISPLAY=:0\n"
            "WorkingDirectory=$DIR_NAME\n"
            "ExecStart=$DIR_NAME/.venv/bin/python agent.py\n"
            "Restart=always\n"
            "RestartSec=5\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
            "EOFSERVICE\n"
            "sudo systemctl daemon-reload\n"
            "sudo systemctl enable gesturelink-agent\n"
            "sudo systemctl restart gesturelink-agent\n"
            "LAN_IP=$(hostname -I | awk '{print $1}')\n"
            "echo \"✅ Micro-Agent is now ACTIVE! Add IP: $LAN_IP to your phone.\"\n"
        )
        
        if "windows" in user_agent or "powershell" in user_agent:
            return StreamingResponse(io.BytesIO(win_script.encode()), media_type="text/plain")
        else:
            return StreamingResponse(io.BytesIO(lin_script.encode()), media_type="text/x-shellscript")

    @app.get("/join-win")
    async def join_windows():
        """Explicit endpoint for Windows installer."""
        win_script = (
            "Write-Host '🛠️ Installing GestureLink Micro-Agent...' -ForegroundColor Cyan\n"
            "$DirName = \"$env:USERPROFILE\\.gesturelink-agent\"\n"
            "if (!(Test-Path $DirName)) { New-Item -ItemType Directory -Path $DirName | Out-Null }\n"
            "Set-Location $DirName\n"
            "if (!(Get-Command python -ErrorAction SilentlyContinue)) { Write-Host '❌ Error: Python not found.' -ForegroundColor Red; exit }\n"
            "if (!(Test-Path \".venv\")) { Write-Host '📦 Creating virtual environment...'; python -m venv .venv; & .venv\\Scripts\\pip install fastapi uvicorn pyautogui websockets }\n"
            "@\"\n"
        ) + AGENT_CODE.strip() + (
            "\n\"@ | Out-File -FilePath agent.py -Encoding utf8\n"
            "Write-Host '📄 Registering background task...' -ForegroundColor Green\n"
            "$Action = New-ScheduledTaskAction -Execute \"$DirName\\.venv\\Scripts\\python.exe\" -Argument \"agent.py\" -WorkingDirectory \"$DirName\"\n"
            "$Trigger = New-ScheduledTaskTrigger -AtLogOn\n"
            "$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 365) -Priority 1\n"
            "Register-ScheduledTask -TaskName 'GestureLinkAgent' -Action $Action -Trigger $Trigger -Settings $Settings -Description 'GestureLink Micro-Agent' -Force\n"
            "Start-ScheduledTask -TaskName 'GestureLinkAgent'\n"
            "$IP = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notlike '*Loopback*' -and $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1).IPAddress\n"
            "Write-Host '✅ Micro-Agent is now ACTIVE! Add IP:' $IP 'to your phone.' -ForegroundColor Green\n"
        )
        return StreamingResponse(io.BytesIO(win_script.encode()), media_type="text/plain")

    @app.get("/api/ping")
    async def ping() -> JSONResponse:
        """Endpoint to allow clients to verify server presence."""
        return JSONResponse({"ok": True, "hostname": socket.gethostname()})

    @app.websocket("/relay")
    async def relay_ws(ws: WebSocket, target: str):
        """Relays websocket messages to a target PC to bypass mixed-content HTTPS restrictions."""
        await ws.accept()
        target_ws_url = f"ws://{target}:8000/ws"
        try:
            import websockets
            import asyncio
            async with websockets.connect(target_ws_url) as remote_ws:
                async def forward_to_remote():
                    try:
                        while True:
                            msg = await ws.receive()
                            if "text" in msg:
                                await remote_ws.send(msg["text"])
                            elif "bytes" in msg:
                                await remote_ws.send(msg["bytes"])
                    except Exception:
                        pass

                async def forward_to_client():
                    try:
                        while True:
                            msg = await remote_ws.recv()
                            if isinstance(msg, bytes):
                                await ws.send_bytes(msg)
                            else:
                                await ws.send_text(msg)
                    except Exception:
                        pass

                await asyncio.gather(forward_to_remote(), forward_to_client())
        except Exception as e:
            await ws.send_json({"error": f"Relay failed: {e}"})
            await ws.close()

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(CLIENT_HTML)

    @app.get("/uninstall")
    async def uninstall_script(request: Request):
        """Serves an uninstallation script based on the requester's OS."""
        user_agent = request.headers.get("user-agent", "").lower()
        
        win_script = (
            "Write-Host '🧹 Uninstalling GestureLink Micro-Agent...' -ForegroundColor Yellow\n"
            "Set-Location $env:USERPROFILE\n"
            "Stop-ScheduledTask -TaskName 'GestureLinkAgent' -ErrorAction SilentlyContinue\n"
            "Unregister-ScheduledTask -TaskName 'GestureLinkAgent' -Confirm:$false -ErrorAction SilentlyContinue\n"
            "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'gesturelink-agent' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }\n"
            "$DirName = \"$env:USERPROFILE\\.gesturelink-agent\"\n"
            "if (Test-Path $DirName) { Remove-Item -Path $DirName -Recurse -Force }\n"
            "Write-Host '✅ Micro-Agent successfully removed!' -ForegroundColor Green\n"
        )
        
        lin_script = (
            "#!/bin/bash\n"
            "echo '🧹 Uninstalling GestureLink Micro-Agent...'\n"
            "sudo systemctl stop gesturelink-agent 2>/dev/null || true\n"
            "sudo systemctl disable gesturelink-agent 2>/dev/null || true\n"
            "if [ -f /etc/systemd/system/gesturelink-agent.service ]; then\n"
            "    sudo rm /etc/systemd/system/gesturelink-agent.service\n"
            "    sudo systemctl daemon-reload\n"
            "fi\n"
            "rm -rf \"$HOME/.gesturelink-agent\"\n"
            "echo '✅ Micro-Agent successfully removed!'\n"
        )

        if "windows" in user_agent or "powershell" in user_agent:
            return StreamingResponse(io.BytesIO(win_script.encode()), media_type="text/plain")
        else:
            return StreamingResponse(io.BytesIO(lin_script.encode()), media_type="text/x-shellscript")

    @app.get("/uninstall-win")
    async def uninstall_windows():
        """Explicit endpoint for Windows uninstaller."""
        win_script = (
            "Write-Host '🧹 Uninstalling GestureLink Micro-Agent...' -ForegroundColor Yellow\n"
            "Set-Location $env:USERPROFILE\n"
            "Stop-ScheduledTask -TaskName 'GestureLinkAgent' -ErrorAction SilentlyContinue\n"
            "Unregister-ScheduledTask -TaskName 'GestureLinkAgent' -Confirm:$false -ErrorAction SilentlyContinue\n"
            "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'gesturelink-agent' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }\n"
            "$DirName = \"$env:USERPROFILE\\.gesturelink-agent\"\n"
            "if (Test-Path $DirName) { Remove-Item -Path $DirName -Recurse -Force }\n"
            "Write-Host '✅ Micro-Agent successfully removed!' -ForegroundColor Green\n"
        )
        return StreamingResponse(io.BytesIO(win_script.encode()), media_type="text/plain")

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

        async def handle_vision(data):
            frame = vision.decode_frame(data)
            if frame is not None:
                state = await vision.process_frame(frame, builder_mode=False)
                status = mouse.update(state)
                await ws.send_json({"status": status})

        try:
            while True:
                msg = await ws.receive()
                
                if "bytes" in msg:
                    # Vision frame: spawn task to avoid blocking the loop
                    # If already processing a frame, we drop this one to prevent lag
                    asyncio.create_task(handle_vision(msg["bytes"]))
                
                elif "text" in msg:
                    try:
                        data = json.loads(msg["text"])
                        mtype = data.get("type")
                        if mtype == "touch":
                            status = mouse.handle_touch_move(data.get("dx", 0), data.get("dy", 0))
                            await ws.send_json({"status": status})
                        elif mtype == "click":
                            status = mouse.handle_click(data.get("button", "left"))
                            await ws.send_json({"status": status})
                        elif mtype == "scroll":
                            status = mouse.handle_touch_scroll(data.get("dy", 0))
                            await ws.send_json({"status": status})
                    except Exception as e:
                        logger.warning("Failed to process text message: %s", e)
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
