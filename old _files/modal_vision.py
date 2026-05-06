"""
modal_vision.py — Optional Modal cloud inference for hand tracking.
Set USE_MODAL=true in .env to enable.
"""
from __future__ import annotations
import logging, os
logger = logging.getLogger(__name__)

try:
    import modal

    image = (
        modal.Image.debian_slim(python_version="3.11")
        .pip_install("mediapipe==0.10.14", "numpy", "opencv-python-headless")
    )
    app = modal.App("gesture-control-vision", image=image)

    @app.cls(cpu=2, memory=1024)
    class VisionWorker:
        @modal.enter()
        def setup(self):
            import mediapipe as mp, urllib.request, os
            model_url  = ("https://storage.googleapis.com/mediapipe-models/"
                          "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task")
            model_path = "/tmp/hand_landmarker.task"
            if not os.path.exists(model_path):
                urllib.request.urlretrieve(model_url, model_path)
            opts = mp.tasks.vision.HandLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=model_path),
                running_mode=mp.tasks.vision.RunningMode.IMAGE,
                num_hands=1,
                min_hand_detection_confidence=0.6,
                min_hand_presence_confidence=0.6,
                min_tracking_confidence=0.5,
            )
            self._lm  = mp.tasks.vision.HandLandmarker.create_from_options(opts)
            self._img = mp.Image
            self._fmt = mp.ImageFormat.SRGB

        @modal.method()
        def detect(self, jpeg_bytes: bytes) -> dict:
            import cv2, numpy as np
            arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
            bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            res = self._lm.detect(self._img(image_format=self._fmt, data=rgb))
            if not res.hand_landmarks:
                return {"hands": []}
            lms = [{"x":l.x,"y":l.y,"z":l.z} for l in res.hand_landmarks[0]]
            return {"hands": [{"landmarks": lms}]}

    class ModalVisionClient:
        def __init__(self):
            self._w = modal.Cls.lookup("gesture-control-vision","VisionWorker")()
            logger.info("ModalVisionClient connected.")
        def detect(self, frame_bgr) -> dict:
            import cv2
            _, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
            return self._w.detect.remote(buf.tobytes())

    _MODAL_AVAILABLE = True

except ImportError:
    _MODAL_AVAILABLE = False


def get_modal_client():
    if os.environ.get("USE_MODAL","false").lower() != "true":
        return None
    if not _MODAL_AVAILABLE:
        logger.warning("USE_MODAL=true but modal not installed.")
        return None
    try:
        return ModalVisionClient()
    except Exception as e:
        logger.warning("Modal connection failed (%s) — using local.", e)
        return None
