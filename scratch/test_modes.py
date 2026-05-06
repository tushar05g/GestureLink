from src.core.config import CONFIG
from src.core.modes import CanvasController, BuilderController

class DummyGestureState:
    def __init__(self):
        self.left_fist = False
        self.right_thumb_index_dist = 0.5
        self.right_index_pos = (0.5, 0.5)

def test_canvas():
    print("Testing Canvas...")
    cc = CanvasController(CONFIG)
    cc.update("PINCH", 0.5, 0.5)
    cc.update("IDLE", 0.5, 0.5)
    cc.update("SCROLL", 0.5, 0.5)
    cc.update("RIGHT_CLICK", 0.5, 0.5)
    print("Canvas OK. Paths:", len(cc.paths))

def test_builder():
    print("Testing Builder...")
    bc = BuilderController(CONFIG)
    gs = DummyGestureState()
    bc.update("POINTING", 0.5, 0.5, 640, 480, gs)
    for _ in range(CONFIG.cube.paint_hold_frames + 2):
        bc.update("PINCH", 0.5, 0.5, 640, 480, gs)
    bc.update("SCROLL", 0.5, 0.5, 640, 480, gs)
    bc.update("RIGHT_CLICK", 0.5, 0.5, 640, 480, gs)
    bc.handle_thumb_pinch_drag(0.5, 0.5, 640, 480, (0.5, 0.5), True)
    print("Builder OK.")

if __name__ == "__main__":
    test_canvas()
    test_builder()
