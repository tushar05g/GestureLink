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

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = Query(None)):
    if SECRET_TOKEN and token != SECRET_TOKEN:
        await ws.close(code=4003)
        return
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

@app.on_event("startup")
async def startup():
    # Broadcast presence
    ip = _detect_lan_ip()
    hostname = socket.gethostname()
    _zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
    info = ServiceInfo(
        "_gesturelink._tcp.local.",
        f"GestureLink-Agent-{hostname}._gesturelink._tcp.local.",  # Bug #8 fix
        addresses=[socket.inet_aton(ip)],
        port=8000,
        properties={"type": "agent", "version": "1.0.0"},
        server=f"{hostname}.local.",
    )
    _zeroconf.register_service(info)
    app.state.zc = _zeroconf
    app.state.zc_info = info
    logging.info(f"Agent broadcasting on {ip}:8000")

@app.on_event("shutdown")
async def shutdown():
    if hasattr(app.state, "zc"):
        app.state.zc.unregister_service(app.state.zc_info)
        app.state.zc.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--secret", type=str, default="")
    args = parser.parse_args()
    
    SECRET_TOKEN = args.secret
    uvicorn.run(app, host="0.0.0.0", port=args.port)
