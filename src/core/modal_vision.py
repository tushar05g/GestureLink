"""
modal_vision.py — Modal cloud deployment for MediaPipe hand tracking.

Architecture
------------
Local machine:
  - Captures webcam frames
  - Sends compressed JPEG to Modal worker
  - Receives landmark JSON back (~1KB vs ~100KB frame)
  - Runs OpenGL rendering + mouse control locally

Modal worker (GPU/CPU):
  - Runs MediaPipe HandLandmarker
  - Returns 21 landmarks per hand as JSON
  - Much faster than local CPU inference

Usage
-----
  # Deploy once:
  modal deploy src/modal_vision.py

  # Use in vision.py:
  Set USE_MODAL=true in .env
"""
from __future__ import annotations

import base64
import logging
import os

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Modal app definition
# ---------------------------------------------------------------------------
try:
    import modal

    image = (
        modal.Image.debian_slim(python_version="3.11")
        .pip_install(
            "mediapipe>=0.10.14",
            "numpy",
            "opencv-python-headless",
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

        @modal.method()
        def detect(self, jpeg_bytes: bytes) -> dict:
            """
            Detect hand landmarks from a JPEG frame.
            Returns dict with landmark data for up to 2 hands.
            """
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

    # ---------------------------------------------------------------------------
    # Local client proxy
    # ---------------------------------------------------------------------------

    class ModalVisionClient:
        """
        Wraps the Modal remote VisionWorker.
        Mimics the interface of local MediaPipe detection.
        """
        def __init__(self):
            self._worker = modal.Cls.from_name(
                "gesture-control-vision", "VisionWorker"
            )()
            logger.info("ModalVisionClient connected.")

        async def detect(self, frame_bgr) -> dict:
            import cv2
            import asyncio
            _, buf = cv2.imencode(".jpg", frame_bgr,
                                  [cv2.IMWRITE_JPEG_QUALITY, 80])
            try:
                # Use to_thread to safely call the remote Modal function
                return await asyncio.wait_for(
                    asyncio.to_thread(self._worker.detect.remote, buf.tobytes()), 
                    timeout=5.0
                )
            except Exception as e:
                logger.warning("Modal detect timeout or error: %s", e)
                return {"hands": []}

    _MODAL_AVAILABLE = True

except ImportError:
    _MODAL_AVAILABLE = False
    logger.info("Modal not installed — using local MediaPipe.")


# ---------------------------------------------------------------------------
# Factory — returns Modal client or None
# ---------------------------------------------------------------------------

def get_modal_client():
    """
    Returns a ModalVisionClient if Modal is configured, else None.
    Set USE_MODAL=true in your .env to enable.
    """
    use_modal = os.environ.get("USE_MODAL", "false").lower() == "true"
    if not use_modal:
        return None
    if not _MODAL_AVAILABLE:
        logger.warning("USE_MODAL=true but modal package not installed.")
        return None
    try:
        client = ModalVisionClient()
        return client
    except Exception as e:
        logger.warning("Modal connection failed (%s) — falling back to local.", e)
        return None
