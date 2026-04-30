import multiprocessing
import logging
import asyncio
import cv2
from src.core.vision import VisionProcessor, GestureState

logger = logging.getLogger("gesture_control.vision_worker")

def vision_worker_process(input_queue, output_queue, config):
    """
    Separate process for vision inference to avoid blocking the main server loop.
    """
    # Initialize processor in the child process (MediaPipe is NOT picklable)
    vision = VisionProcessor(config)
    
    logger.info("Vision Worker Process started.")
    
    try:
        while True:
            item = input_queue.get()
            if item is None: break
            
            frame_input, builder_mode = item
            if isinstance(frame_input, bytes):
                frame = vision.decode_frame(frame_input)
            else:
                frame = frame_input
            
            if frame is not None:
                state = vision.process_frame_sync(frame, builder_mode)
                
                # Draw visual feedback for the dashboard
                annotated_frame = vision.draw_landmarks(frame, state)
                _, jpeg = cv2.imencode('.jpg', annotated_frame)
                
                output_queue.put((state, jpeg.tobytes()))
    except Exception as e:
        logger.error(f"Vision Worker Error: {e}")
    finally:
        vision.close()
        logger.info("Vision Worker Process stopped.")

class AsyncVisionWorker:
    """
    Manager for the vision process in the FastAPI server.
    """
    def __init__(self, config):
        self.config = config
        self.input_queue = multiprocessing.Queue(maxsize=2) # Keep it small to avoid lag
        self.output_queue = multiprocessing.Queue()
        self.process = None

    def start(self):
        self.process = multiprocessing.Process(
            target=vision_worker_process,
            args=(self.input_queue, self.output_queue, self.config),
            daemon=True
        )
        self.process.start()

    def stop(self):
        if self.process:
            self.input_queue.put(None)
            self.process.join(timeout=2)
            self.process.terminate()

    async def process_frame(self, frame_bytes, builder_mode=False):
        try:
            if self.input_queue.full(): self.input_queue.get_nowait()
            self.input_queue.put_nowait((frame_bytes, builder_mode))
        except: pass
        
        latest_res = None
        while not self.output_queue.empty():
            latest_res = self.output_queue.get_nowait()
        
        return latest_res # Returns (state, annotated_bytes)
