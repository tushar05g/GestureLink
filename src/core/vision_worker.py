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
            if item is None: # Shutdown signal
                break
            
            frame_bytes, builder_mode = item
            frame = vision.decode_frame(frame_bytes)
            
            if frame is not None:
                # We can't await here because this is a synchronous Process.
                # vision.process_frame is async, so we need to run it in a loop
                loop = asyncio.new_event_loop()
                state = loop.run_until_complete(vision.process_frame(frame, builder_mode))
                output_queue.put(state)
                loop.close()
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
        # Non-blocking put
        try:
            if self.input_queue.full():
                self.input_queue.get_nowait() # Drop oldest frame
            self.input_queue.put_nowait((frame_bytes, builder_mode))
        except: pass
        
        # Get latest result if available
        results = []
        while not self.output_queue.empty():
            results.append(self.output_queue.get_nowait())
        
        return results[-1] if results else None
