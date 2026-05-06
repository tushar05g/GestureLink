"""
world.py — Cube grid, flood-fill connected component, undo stack.
"""
from __future__ import annotations
import logging
from collections import deque
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Cube:
    gx: int
    gy: int
    gz: int


class CubeWorld:
    def __init__(self, max_undo: int = 20) -> None:
        self._cubes: set[Cube] = set()
        self._undo_stack: list[frozenset] = []
        self._max_undo = max_undo
        self.selected_group: list[Cube] = []

    @property
    def cubes(self) -> frozenset[Cube]:
        return frozenset(self._cubes)

    def has(self, gx, gy, gz) -> bool:
        return Cube(gx, gy, gz) in self._cubes

    def nearest_at_xy(self, gx: int, gy: int) -> Optional[Cube]:
        hits = [c for c in self._cubes if c.gx == gx and c.gy == gy]
        return min(hits, key=lambda c: c.gz) if hits else None

    def connected_group(self, start: Cube) -> list[Cube]:
        if start not in self._cubes:
            return []
        visited, queue = {start}, deque([start])
        while queue:
            cur = queue.popleft()
            for nb in self._neighbours(cur):
                if nb in self._cubes and nb not in visited:
                    visited.add(nb)
                    queue.append(nb)
        return list(visited)

    @staticmethod
    def _neighbours(c: Cube):
        for dx,dy,dz in [(1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)]:
            yield Cube(c.gx+dx, c.gy+dy, c.gz+dz)

    def place(self, gx, gy, gz) -> bool:
        c = Cube(gx, gy, gz)
        if c in self._cubes: return False
        self._save()
        self._cubes.add(c)
        return True

    def erase(self, gx, gy, gz) -> bool:
        c = Cube(gx, gy, gz)
        if c not in self._cubes: return False
        self._save()
        self._cubes.discard(c)
        return True

    def move_group(self, group: list[Cube], dgx, dgy, dgz) -> bool:
        new_pos   = [Cube(c.gx+dgx, c.gy+dgy, c.gz+dgz) for c in group]
        group_set = set(group)
        for nc in new_pos:
            if nc in self._cubes and nc not in group_set:
                return False
        self._save()
        for c in group:     self._cubes.discard(c)
        for nc in new_pos:  self._cubes.add(nc)
        return True

    def undo(self) -> bool:
        if not self._undo_stack: return False
        self._cubes = set(self._undo_stack.pop())
        return True

    def _save(self):
        self._undo_stack.append(frozenset(self._cubes))
        if len(self._undo_stack) > self._max_undo:
            self._undo_stack.pop(0)
