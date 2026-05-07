"""
world.py — Cube grid, connected-component flood fill, undo stack.

Coordinate system
-----------------
  gx : grid column  (0 = left)
  gy : grid row     (0 = top)
  gz : depth layer  (0 = nearest / brightest, 6 = farthest / darkest)
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cube
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Cube:
    gx: int
    gy: int
    gz: int


# ---------------------------------------------------------------------------
# World
# ---------------------------------------------------------------------------

class CubeWorld:
    """All placed cubes + undo stack."""

    def __init__(self, max_undo: int = 20) -> None:
        self._cubes: set[Cube] = set()
        self._undo_stack: list[tuple[str, set[Cube], set[Cube]]] = []
        # Each undo entry: (description, cubes_before, cubes_after)
        self._max_undo = max_undo
        self.selected_group: list[Cube] = []   # cubes being dragged

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    @property
    def cubes(self) -> frozenset[Cube]:
        return frozenset(self._cubes)

    def has(self, gx: int, gy: int, gz: int) -> bool:
        return Cube(gx, gy, gz) in self._cubes

    def cube_at(self, gx: int, gy: int, gz: int) -> Optional[Cube]:
        c = Cube(gx, gy, gz)
        return c if c in self._cubes else None

    def nearest_at_xy(self, gx: int, gy: int) -> Optional[Cube]:
        """Return the nearest-layer cube at (gx, gy), or None."""
        candidates = [c for c in self._cubes if c.gx == gx and c.gy == gy]
        return min(candidates, key=lambda c: c.gz) if candidates else None

    # ------------------------------------------------------------------
    # Flood fill — connected component
    # ------------------------------------------------------------------

    def connected_group(self, start: Cube) -> list[Cube]:
        """
        BFS flood fill from start cube.
        Two cubes are connected if they are neighbours in any of the
        6 face-adjacent directions (±gx, ±gy, ±gz).
        """
        if start not in self._cubes:
            return []

        visited: set[Cube] = set()
        queue: deque[Cube] = deque([start])
        visited.add(start)

        while queue:
            cur = queue.popleft()
            for nb in self._neighbours(cur):
                if nb in self._cubes and nb not in visited:
                    visited.add(nb)
                    queue.append(nb)

        return list(visited)

    @staticmethod
    def _neighbours(c: Cube):
        for dx, dy, dz in [
            (1,0,0),(-1,0,0),
            (0,1,0),(0,-1,0),
            (0,0,1),(0,0,-1),
        ]:
            yield Cube(c.gx+dx, c.gy+dy, c.gz+dz)

    # ------------------------------------------------------------------
    # Mutations (all record undo)
    # ------------------------------------------------------------------

    def place(self, gx: int, gy: int, gz: int) -> bool:
        """Place a cube. Returns True if a new cube was added."""
        c = Cube(gx, gy, gz)
        if c in self._cubes:
            return False
        before = frozenset(self._cubes)
        self._cubes.add(c)
        self._record("place", before, frozenset(self._cubes))
        return True

    def place_many(self, cubes: list[Cube]) -> None:
        """Place multiple cubes at once (one undo entry)."""
        before = frozenset(self._cubes)
        for c in cubes:
            self._cubes.add(c)
        self._record("place_many", before, frozenset(self._cubes))

    def erase(self, gx: int, gy: int, gz: int) -> bool:
        """Erase a cube. Returns True if a cube was removed."""
        c = Cube(gx, gy, gz)
        if c not in self._cubes:
            return False
        before = frozenset(self._cubes)
        self._cubes.discard(c)
        self._record("erase", before, frozenset(self._cubes))
        return True

    def move_group(self, group: list[Cube], dgx: int, dgy: int, dgz: int) -> bool:
        """
        Move a group of cubes by (dgx, dgy, dgz).
        Returns False and does NOT move if any target cell is occupied
        by a cube NOT in the group (collision).
        """
        new_positions = [Cube(c.gx+dgx, c.gy+dgy, c.gz+dgz) for c in group]
        group_set = set(group)

        # Collision check
        for nc in new_positions:
            if nc in self._cubes and nc not in group_set:
                logger.debug("Move blocked — collision at %s", nc)
                return False

        before = frozenset(self._cubes)
        for c in group:
            self._cubes.discard(c)
        for nc in new_positions:
            self._cubes.add(nc)
        self._record("move", before, frozenset(self._cubes))
        return True

    def clear(self) -> None:
        before = frozenset(self._cubes)
        self._cubes.clear()
        self._record("clear", before, frozenset())

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------

    def undo(self) -> bool:
        if not self._undo_stack:
            return False
        _, before, _ = self._undo_stack.pop()
        self._cubes = set(before)
        logger.debug("Undo — %d cubes remaining", len(self._cubes))
        return True

    def _record(self, desc: str, before: frozenset, after: frozenset) -> None:
        self._undo_stack.append((desc, before, after))
        if len(self._undo_stack) > self._max_undo:
            self._undo_stack.pop(0)
