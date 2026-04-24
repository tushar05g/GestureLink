import json
import socket
import logging
import asyncio
import pyautogui
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
import uvicorn
from zeroconf import IPVersion, ServiceInfo, Zeroconf
import argparse

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="GestureLink Micro-Agent")
pyautogui.FAILSAFE = False

SECRET_TOKEN = ""

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

@app.get("/api/apps")
async def get_apps():
    from src.core.shortcuts import ShortcutManager
    sm = ShortcutManager()
    return {"apps": sm.get_available_apps()}

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
    
    try:
        while True:
            msg = await ws.receive()
            if "text" in msg:
                try:
                    data = json.loads(msg["text"])
                    mtype = data.get("type")
                    if mtype in ("touch", "move"):
                        dx, dy = float(data.get("dx", 0)), float(data.get("dy", 0))
                        
                        # Accumulate the fractional movements
                        frac_x += dx * 1.5
                        frac_y += dy * 1.5
                        
                        move_x = int(frac_x)
                        move_y = int(frac_y)
                        
                        # Only deduct what was actually moved
                        frac_x -= move_x
                        frac_y -= move_y
                        
                        if move_x != 0 or move_y != 0:
                            pyautogui.moveRel(move_x, move_y, _pause=False)
                    elif mtype == "click":
                        pyautogui.click(button=data.get("button", "left"), _pause=False)
                    elif mtype in ("click_down", "click_up"):
                        is_down = (mtype == "click_down")
                        if is_down:
                            pyautogui.mouseDown(button=data.get("button", "left"), _pause=False)
                        else:
                            pyautogui.mouseUp(button=data.get("button", "left"), _pause=False)
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
                        from src.core.shortcuts import ShortcutManager
                        sm = ShortcutManager()
                        logic_slot = {
                            "touch_3_finger": "three_fingers",
                            "touch_4_finger": "four_fingers"
                        }.get(slot, slot)
                        sm.trigger(logic_slot)
                except Exception as e:
                    logging.warning(f"Error processing command: {e}")
    except WebSocketDisconnect:
        logging.info("Controller disconnected")

@app.on_event("startup")
async def startup():
    # Broadcast presence in a non-blocking way
    def register():
        try:
            ip = _detect_lan_ip()
            hostname = socket.gethostname()
            _zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
            info = ServiceInfo(
                "_gesturelink._tcp.local.",
                f"GestureLink-Agent-{hostname}._gesturelink._tcp.local.",
                addresses=[socket.inet_aton(ip)],
                port=8001, # Default to 8001 to avoid Hub conflict
                properties={"type": "agent", "version": "1.0.0"},
                server=f"{hostname}.local.",
            )
            _zeroconf.register_service(info)
            app.state.zc = _zeroconf
            app.state.zc_info = info
            logging.info(f"Agent broadcasting on {ip}:8001")
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
