"""
modal_vision.py — Modal cloud deployment for MediaPipe hand tracking via Web API.

Architecture
------------
Local machine:
  - Captures webcam frames
  - Sends compressed JPEG via standard HTTP POST to Modal Web API
  - Receives landmark JSON back (~1KB vs ~100KB frame)
  - No `modal` package or CLI login required!

Modal worker (GPU/CPU):
  - Exposes a secure FastAPI endpoint (@modal.asgi_app)
  - Runs MediaPipe HandLandmarker
  - Returns 21 landmarks per hand as JSON

Usage
-----
  # Deploy once (Developer Only):
  modal deploy src/core/modal_vision.py

  # Use in vision.py (End User):
  Set USE_MODAL=true in .env
  Set MODAL_API_URL=https://... in .env
"""
from __future__ import annotations

import base64
import logging
import os

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Modal app definition (Ignored for End Users)
# ---------------------------------------------------------------------------
try:
    import modal

    image = (
        modal.Image.debian_slim(python_version="3.11")
        .pip_install(
            "mediapipe>=0.10.14",
            "numpy",
            "opencv-python-headless",
            "fastapi"
        )
        .run_commands("apt-get update && apt-get install -y libgl1-mesa-glx")
    )

    app = modal.App("gesture-control-vision", image=image)

    @app.cls(gpu="T4", cpu=4, memory=1024)
    class VisionWorker:
        @modal.enter()
        def setup(self):
            import mediapipe as mp
            import urllib.request, os

            model_url = (
                "https://storage.googleapis.com/mediapipe-models/"
                "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
            )
            model_path = "/tmp/hand_landmarker.task"
            if not os.path.exists(model_path):
                urllib.request.urlretrieve(model_url, model_path)

            options = mp.tasks.vision.HandLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=model_path),
                running_mode=mp.tasks.vision.RunningMode.IMAGE,
                num_hands=2,
                min_hand_detection_confidence=0.65,
                min_hand_presence_confidence=0.65,
                min_tracking_confidence=0.6,
            )
            self._landmarker   = mp.tasks.vision.HandLandmarker.create_from_options(options)
            self._mp_image_cls = mp.Image
            self._mp_format    = mp.ImageFormat.SRGB

        def _detect_logic(self, jpeg_bytes: bytes) -> dict:
            """Core MediaPipe logic"""
            import cv2
            import numpy as np

            arr       = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            frame_bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

            mp_image = self._mp_image_cls(
                image_format=self._mp_format, data=frame_rgb
            )
            result = self._landmarker.detect(mp_image)

            hands_data = []
            for i, hand in enumerate(result.hand_landmarks):
                handedness = "Unknown"
                if result.handedness and i < len(result.handedness):
                    handedness = result.handedness[i][0].category_name
                lm_list = [{"x": lm.x, "y": lm.y, "z": lm.z} for lm in hand]
                hands_data.append({
                    "handedness": handedness,
                    "landmarks": lm_list,
                })

            return {"hands": hands_data}

        @modal.asgi_app()
        def web_api(self):
            import fastapi
            
            web_app = fastapi.FastAPI()
            
            @web_app.post("/detect")
            async def detect_endpoint(request: fastapi.Request):
                # Verify API Key
                api_key = request.headers.get("X-API-Key")
                if api_key != "gesturelink_vision_api_secret_key":
                    return fastapi.Response(status_code=403, content="Invalid API Key")
                
                body = await request.body()
                return self._detect_logic(body)
                
            return web_app

except ImportError:
    logger.info("Modal backend package not installed — this is normal for End Users.")


# ---------------------------------------------------------------------------
# Local client proxy (Runs on End User's Machine)
# ---------------------------------------------------------------------------

class ModalVisionClient:
    """
    Wraps the remote Web API.
    Uses standard httpx so it works flawlessly on any end-user machine without Modal login.
    """
    def __init__(self):
        import httpx
        self.api_url = os.environ.get("MODAL_API_URL")
        if not self.api_url:
            raise ValueError("MODAL_API_URL not set in .env")
            
        # Ensure we hit the correct /detect endpoint
        self.api_url = self.api_url.rstrip("/") + "/detect"
        self.api_key = os.environ.get("MODAL_API_KEY", "gesturelink_vision_api_secret_key")
        
        # Async HTTP client
        self.client = httpx.AsyncClient()
        self._busy = False
        logger.info(f"ModalVisionClient connected to Web API: {self.api_url}")

    async def detect(self, frame_bgr) -> dict:
        import cv2
        import asyncio
        
        if self._busy:
            # Fallback if we are already waiting for a previous frame
            return {"hands": []}
            
        self._busy = True
        try:
            _, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
            
            res = await self.client.post(
                self.api_url, 
                content=buf.tobytes(),
                headers={"X-API-Key": self.api_key},
                timeout=0.5
            )
            if res.status_code == 200:
                return res.json()
            else:
                logger.warning(f"Modal API Error: {res.status_code}")
                return {"hands": []}
                
        except asyncio.TimeoutError:
            logger.warning("Modal taking >500ms, using local MediaPipe fallback.")
            return {"hands": []}
        except Exception as e:
            logger.warning(f"Modal detect error: {e}")
            return {"hands": []}
        finally:
            self._busy = False

# ---------------------------------------------------------------------------
# Factory — returns Modal client or None
# ---------------------------------------------------------------------------

def get_modal_client():
    """
    Returns a ModalVisionClient if enabled in .env, else None.
    """
    use_modal = os.environ.get("USE_MODAL", "false").lower() == "true"
    if not use_modal:
        return None
        
    try:
        client = ModalVisionClient()
        return client
    except Exception as e:
        logger.warning(f"Modal configuration failed ({e}) — falling back to local MediaPipe.")
        return None
