from dataclasses import dataclass
from typing import List, Tuple, Set

@dataclass(frozen=True)
class Cube:
    gx: int
    gy: int
    gz: int

class CubeWorld:
    def __init__(self, max_undo=50):
        self.cubes: Set[Cube] = set()
        self.history: List[Set[Cube]] = []

    def place(self, x: int, y: int, z: int):
        self.history.append(set(self.cubes))
        if len(self.history) > 50:
            self.history.pop(0)
        self.cubes.add(Cube(x, y, z))

    def erase(self, x: int, y: int, z: int):
        target = Cube(x, y, z)
        if target in self.cubes:
            self.history.append(set(self.cubes))
            if len(self.history) > 50:
                self.history.pop(0)
            self.cubes.remove(target)

    def undo(self):
        if self.history:
            self.cubes = self.history.pop()
            return True
        return False

    def clear(self):
        self.history.append(set(self.cubes))
        self.cubes.clear()