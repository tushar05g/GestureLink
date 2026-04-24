import asyncio
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

app = FastAPI()

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    msg = await ws.receive()
    print("KEYS:", msg.keys())
    print("VALUES:", msg)
    await ws.close()

client = TestClient(app)
with client.websocket_connect("/ws") as websocket:
    websocket.send_bytes(b"hello")
