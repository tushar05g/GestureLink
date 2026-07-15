import asyncio
import json
import logging
import os
import urllib.request
import httpx
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
import pyautogui
from src.core.config import CONFIG

FIREBASE_URL = "https://gesturelink-5db9c-default-rtdb.firebaseio.com"

# Keep track of active PC
_pc = None


def get_config_path():
    return os.path.join(os.path.expanduser("~"), ".gesturelink_agent.json")


def load_agent_config():
    path = get_config_path()
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


async def handle_webrtc_offer(offer_data, uid, agent_id, mouse):
    global _pc
    if _pc:
        await _pc.close()

    logging.info("Received WebRTC Offer from Hub.")

    pc = RTCPeerConnection(configuration=RTCConfiguration(
        iceServers=[
            RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
            RTCIceServer(urls=["stun:stun1.l.google.com:19302"])
        ]
    ))
    _pc = pc

    # Store accumulators
    pc.frac_x = 0.0
    pc.frac_y = 0.0

    @pc.on("datachannel")
    def on_datachannel(channel):
        logging.info(f"Data channel {channel.label} established with Hub")

        @channel.on("message")
        def on_message(message):
            if isinstance(message, str):
                try:
                    data = json.loads(message)
                    mtype = data.get("type")
                    if mtype in ("touch", "move"):
                        dx, dy = float(data.get("dx", 0)), float(data.get("dy", 0))
                        pc.frac_x += dx * CONFIG.gesture.trackpad_sensitivity
                        pc.frac_y += dy * CONFIG.gesture.trackpad_sensitivity
                        move_x, move_y = int(pc.frac_x), int(pc.frac_y)
                        pc.frac_x -= move_x
                        pc.frac_y -= move_y
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
                        pyautogui.scroll(-int(float(data.get("dy", 0)) * 20), _pause=False)
                    elif mtype == "zoom":
                        pass
                    elif mtype == "shortcut":
                        pass
                    elif mtype == "key":
                        key = data.get("key")
                        if key:
                            pyautogui.press(key, _pause=False)
                    elif mtype == "hotkey":
                        keys = data.get("keys", [])
                        if keys:
                            pyautogui.hotkey(*keys, _pause=False)
                    # New: Absolute positioning support from Hub VisionWorker
                    elif mtype == "absolute_move":
                        x, y = data.get("x"), data.get("y")
                        if x is not None and y is not None:
                            import ctypes
                            ctypes.windll.user32.SetCursorPos(int(x), int(y))
                    elif mtype == "raw_state":
                        from src.core.vision import GestureState, Gesture
                        state = GestureState()
                        state.gesture = Gesture(data.get("gesture"))
                        state.cursor_x = data.get("cursor_x", 0.5)
                        state.cursor_y = data.get("cursor_y", 0.5)
                        state.scroll_dy = data.get("scroll_dy", 0.0)
                        state.pinch_active = data.get("pinch_active", False)
                        state.thumb_pinch_active = data.get("thumb_pinch_active", False)
                        state.mode_switch = data.get("mode_switch", False)
                        state.active_mode = data.get("active_mode", 0)
                        mouse.update(state)
                except Exception as e:
                    logging.error(f"WebRTC message error: {e}")

    offer = RTCSessionDescription(sdp=offer_data["sdp"], type=offer_data["type"])
    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    # Send answer back via Firebase
    answer_data = {
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type
    }

    try:
        import urllib.request
        req = urllib.request.Request(
            f"{FIREBASE_URL}/users/{uid}/agents/{agent_id}/signaling/agent_answer.json",
            data=json.dumps(answer_data).encode('utf-8'),
            method='PUT'
        )
        urllib.request.urlopen(req, timeout=5)
        logging.info("Sent WebRTC Answer to Hub.")
    except Exception as e:
        logging.error(f"Failed to send answer: {e}")


async def cloud_listener_loop(mouse):
    config = load_agent_config()
    uid = config.get("uid")
    agent_id = config.get("agent_id")

    if not uid or not agent_id:
        logging.info("No cloud agent config found. Skipping cloud listener.")
        return

    url = f"{FIREBASE_URL}/users/{uid}/agents/{agent_id}/signaling.json"
    logging.info(f"Starting cloud listener for Agent {agent_id}")

    while True:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", url, headers={"Accept": "text/event-stream"}) as response:
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            try:
                                data = json.loads(line[6:])
                                if data and data.get("path") == "/":
                                    payload = data.get("data")
                                elif data and data.get("path") == "/hub_offer":
                                    payload = {"hub_offer": data.get("data")}
                                else:
                                    continue

                                if payload and "hub_offer" in payload and payload["hub_offer"]:
                                    offer = payload["hub_offer"]
                                    # Handle in background
                                    asyncio.create_task(handle_webrtc_offer(
                                        offer, uid, agent_id, mouse))

                                    # Delete the offer so we don't process it again
                                    req_del = urllib.request.Request(
                                        f"{FIREBASE_URL}/users/{uid}/agents/{agent_id}/signaling/hub_offer.json",
                                        method='DELETE'
                                    )
                                    urllib.request.urlopen(req_del, timeout=5)
                            except Exception as e:
                                pass
        except Exception as e:
            logging.error(f"Cloud listener error: {e}")
            await asyncio.sleep(5)
