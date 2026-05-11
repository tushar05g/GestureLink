import sys
import os

# Fix for PyInstaller with console=False (prevent NoneType has no attribute 'isatty' in uvicorn)
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import json
import socket
import logging
import asyncio
import pyautogui
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
import uvicorn
from zeroconf import IPVersion, ServiceInfo, Zeroconf
import argparse
import multiprocessing
import platform
from src.core.vision_worker import AsyncVisionWorker
import cv2
import numpy as np
from src.core.config import CONFIG
from src.core.vision import VisionProcessor
from src.core.controller import MouseController

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="GestureLink Micro-Agent")
pyautogui.FAILSAFE = False

SECRET_TOKEN = ""
camera_active = False
camera_task = None
vision = None
mouse = None

def _detect_lan_ips() -> list[str]:
    """Return real LAN IPs, excluding VMware/Hyper-V virtual adapters.
    
    Strategy: Use the route-based getsockname() trick as primary source —
    it picks the ACTUAL outbound interface (Wi-Fi / Ethernet), never VMnet.
    gethostbyname_ex() is avoided because it returns ALL adapters including
    VMware VMnet8, Hyper-V vEthernet, etc.
    """
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    
    if not found:
        # Fallback: enumerate adapters but skip obvious virtual ranges
        VIRTUAL_PREFIXES = ("192.168.56.", "192.168.234.", "192.168.100.")
        try:
            ips = socket.gethostbyname_ex(socket.gethostname())[2]
            for ip in ips:
                if not ip.startswith("127.") and not any(ip.startswith(p) for p in VIRTUAL_PREFIXES):
                    found.append(ip)
        except Exception:
            pass
    
    return found if found else ["127.0.0.1"]

def _detect_lan_ip() -> str:
    """Return a single best-guess LAN IP."""
    ips = _detect_lan_ips()
    return ips[0] if ips else "127.0.0.1"

@app.get("/api/ping")
async def ping():
    return {"ok": True, "hostname": socket.gethostname(), "ip": _detect_lan_ip()}

@app.get("/api/agent/info")
async def agent_info():
    """Returns agent status info for the Hub dashboard."""
    return {
        "hostname": socket.gethostname(),
        "ip": _detect_lan_ip(),
        "port": 8001,
        "camera_active": camera_active,
    }

@app.post("/api/security/fix-firewall")
async def fix_firewall():
    """Adds Windows Firewall rules to allow inbound traffic on port 8001.
    Requires the Agent to be running as Administrator (which it does via uac_admin=True in the .spec).
    """
    if platform.platform().lower().startswith("win"):
        pass  # Always try on Windows
    else:
        return {"ok": False, "error": "Only supported on Windows"}

    import asyncio
    cmd = 'netsh advfirewall firewall add rule name="GestureLink Agent" dir=in action=allow protocol=TCP localport=8001'
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode().strip() or stdout.decode().strip()
            if "elevation" in err.lower() or "administrator" in err.lower() or "access" in err.lower():
                return {"ok": False, "error": "Access Denied. Please restart GestureLink Agent as Administrator."}
            return {"ok": False, "error": err}
        return {"ok": True, "message": "Firewall rule added for port 8001!"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/api/apps")
async def get_apps():
    from src.core.shortcuts import ShortcutManager
    sm = ShortcutManager()
    return {"apps": sm.get_available_apps()}

@app.get("/api/settings")
async def get_settings():
    return {
        "sensitivity": int(CONFIG.gesture.trackpad_sensitivity * 33.3), # Map 1.5 to ~50
        "trackpad_sensitivity": CONFIG.gesture.trackpad_sensitivity
    }

@app.post("/api/settings")
async def set_settings(payload: dict):
    # If "sensitivity" (0-100) is provided, map it to a multiplier (0.5 to 3.0)
    if "sensitivity" in payload:
        val = float(payload["sensitivity"])
        CONFIG.gesture.trackpad_sensitivity = 0.5 + (val / 100.0) * 2.5
    elif "trackpad_sensitivity" in payload:
        CONFIG.gesture.trackpad_sensitivity = float(payload["trackpad_sensitivity"])
    
    logging.info(f"Agent sensitivity updated to: {CONFIG.gesture.trackpad_sensitivity}")
    return {"ok": True, "trackpad_sensitivity": CONFIG.gesture.trackpad_sensitivity}

async def _camera_loop():
    global camera_active, vision, mouse
    cap = cv2.VideoCapture(0)
    while camera_active:
        ret, frame = cap.read()
        if not ret:
            await asyncio.sleep(0.01)
            continue
        
        # Mirror the frame so cursor movement matches hand direction.
        # Without this, lm[8].x increases left-to-right in the RAW frame,
        # but the physical hand appears mirrored → cursor moves opposite.
        # The Hub does the same flip in _hub_camera_loop().
        frame = cv2.flip(frame, 1)
        
        # Process frame via AsyncVisionWorker
        # Returns tuple: (GestureState, annotated_bytes) or None
        result = await vision.process_frame(frame)
        if result:
            state, _ = result
            mouse.update(state)
        
        # Small sleep to yield to event loop
        await asyncio.sleep(0.01)
    
    cap.release()

@app.post("/api/camera/toggle")
async def toggle_camera(payload: dict):
    global camera_active, camera_task, vision, mouse
    active = payload.get("active", False)
    
    if active and not camera_active:
        camera_active = True
        if not vision:
            vision = AsyncVisionWorker(CONFIG)
            vision.start()
        if not mouse:
            mouse = MouseController(CONFIG)
        camera_task = asyncio.create_task(_camera_loop())
        logging.info("Agent camera turned ON")
    elif not active and camera_active:
        camera_active = False
        if camera_task:
            await camera_task
            camera_task = None
        logging.info("Agent camera turned OFF")
    
    return {"ok": True, "active": camera_active}

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = Query(None)):
    # Hub-to-Agent relay uses "hub_internal"
    if SECRET_TOKEN:
        if token != SECRET_TOKEN and token != "hub_internal":
            await ws.close(code=4003)
            return
    elif token != "hub_internal":
        # If no secret set, we still expect hub_internal for relay
        # or we could allow anything, but hub_internal is safer
        pass
    
    await ws.accept()
    logging.info("Connected to controller")
    
    # Accumulators for sub-pixel trackpad movements
    frac_x = 0.0
    frac_y = 0.0
    
    global mouse
    if mouse is None:
        mouse = MouseController(CONFIG, responsive=True)
    
    try:
        while True:
            msg = await ws.receive()
            if "text" in msg:
                try:
                    data = json.loads(msg["text"])
                    mtype = data.get("type")
                    if mtype in ("touch", "move"):
                        dx, dy = float(data.get("dx", 0)), float(data.get("dy", 0))
                        frac_x += dx * CONFIG.gesture.trackpad_sensitivity
                        frac_y += dy * CONFIG.gesture.trackpad_sensitivity
                        move_x, move_y = int(frac_x), int(frac_y)
                        frac_x -= move_x
                        frac_y -= move_y
                        if move_x != 0 or move_y != 0:
                            pyautogui.moveRel(move_x, move_y, _pause=False)
                    elif mtype == "click":
                        pyautogui.click(button=data.get("button", "left"), _pause=False)
                    elif mtype in ("click_down", "click_up"):
                        is_down = (mtype == "click_down")
                        if is_down: pyautogui.mouseDown(button=data.get("button", "left"), _pause=False)
                        else: pyautogui.mouseUp(button=data.get("button", "left"), _pause=False)
                    elif mtype == "scroll":
                        dy = float(data.get("dy", 0))
                        pyautogui.scroll(int(dy * -2), _pause=False)
                    elif mtype == "zoom":
                        delta = float(data.get("delta", 0))
                        zoom_dir = 1 if delta > 0 else -1
                        pyautogui.keyDown('ctrl')
                        pyautogui.scroll(zoom_dir * 10, _pause=False)
                        pyautogui.keyUp('ctrl')
                    elif mtype == "shortcut":
                        slot = data.get("slot", "")
                        mouse.handle_touch_shortcut(slot)
                    elif mtype == "key":
                        key = data.get("key")
                        if key: mouse.handle_key(key)
                    elif mtype == "hotkey":
                        keys = data.get("keys", [])
                        if keys: mouse.handle_hotkey(keys)
                except Exception as e:
                    logging.warning(f"Error processing command: {e}")
    except WebSocketDisconnect:
        logging.info("Controller disconnected")

@app.on_event("startup")
async def startup():
    # Broadcast presence on all available LAN interfaces
    def register():
        try:
            ips = _detect_lan_ips()
            hostname = socket.gethostname()
            _zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
            info = ServiceInfo(
                "_gesturelink._tcp.local.",
                f"GestureLink-Agent-{hostname}._gesturelink._tcp.local.",
                addresses=[socket.inet_aton(ip) for ip in ips],
                port=8001, # Default to 8001 to avoid Hub conflict
                properties={"type": "agent", "version": "1.0.0"},
                server=f"{hostname}.local.",
            )
            _zeroconf.register_service(info)
            app.state.zc = _zeroconf
            app.state.zc_info = info
            logging.info(f"Agent broadcasting on {ips}:8001")
        except Exception as e:
            logging.warning(f"Zeroconf failed: {e}")

    asyncio.get_event_loop().run_in_executor(None, register)

@app.on_event("shutdown")
async def shutdown():
    if hasattr(app.state, "zc"):
        loop = asyncio.get_event_loop()
        def unregister():
            try:
                app.state.zc.unregister_service(app.state.zc_info)
                app.state.zc.close()
            except Exception as e:
                logging.warning(f"Zeroconf shutdown error: {e}")
        await loop.run_in_executor(None, unregister)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--secret", type=str, default="")
    args = parser.parse_args()
    
    SECRET_TOKEN = args.secret
    uvicorn.run(app, host="0.0.0.0", port=args.port)
