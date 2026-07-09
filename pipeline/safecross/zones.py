"""Zone topology metrics — reliable without calibration (spec-cv §4).

- Line crossing counts (pedestrians / vehicles) — side-change of track anchor
  across a directed segment.
- Crosswalk occupancy (polygon).
- "Driver failed to yield" — topological event: a vehicle transits the
  conflict polygon without stopping while a pedestrian occupies the crosswalk.

All state is per-track and ephemeral (RAM only). No frames, no identities.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


def _side(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


class LineCounter:
    """Counts track anchor points crossing segment a->b (both directions)."""

    def __init__(self, a: tuple[float, float], b: tuple[float, float]):
        self.a, self.b = a, b
        self._last_side: dict[int, float] = {}
        self.count_ab = 0  # crossed from negative to positive side
        self.count_ba = 0

    @property
    def total(self) -> int:
        return self.count_ab + self.count_ba

    def update(self, track_id: int, point: tuple[float, float]) -> bool:
        s = _side(point, self.a, self.b)
        prev = self._last_side.get(track_id)
        self._last_side[track_id] = s
        if prev is None or s == 0 or prev == 0:
            return False
        if prev < 0 < s:
            self.count_ab += 1
            return True
        if prev > 0 > s:
            self.count_ba += 1
            return True
        return False

    def drop(self, track_id: int) -> None:
        self._last_side.pop(track_id, None)


class PolygonZone:
    def __init__(self, polygon: list[tuple[float, float]]):
        self.poly = np.array(polygon, dtype=np.float32)

    def contains(self, point: tuple[float, float]) -> bool:
        # ray casting
        x, y = point
        n = len(self.poly)
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = self.poly[i]
            xj, yj = self.poly[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        return inside


@dataclass
class _VehState:
    inside: bool = False
    stopped_while_ped: bool = False
    ped_seen_during_transit: bool = False
    last_pos: tuple[float, float] | None = None
    counted: bool = False


class YieldMonitor:
    """Flags 'vehicle did not yield': transit of conflict zone, never dropping
    below stop_speed (px/s displacement proxy), while a pedestrian occupies
    the crosswalk polygon. Aggregate counter only."""

    def __init__(self, conflict_zone: PolygonZone, stop_speed_px_s: float = 15.0):
        self.zone = conflict_zone
        self.stop_speed = stop_speed_px_s
        self._veh: dict[int, _VehState] = {}
        self.violations = 0

    def update(self, ts_dt: float, veh_tracks: dict[int, tuple[float, float]],
               ped_in_crosswalk: bool) -> None:
        seen = set()
        for tid, pos in veh_tracks.items():
            seen.add(tid)
            st = self._veh.setdefault(tid, _VehState())
            inside = self.zone.contains(pos)
            if inside:
                if ped_in_crosswalk:
                    st.ped_seen_during_transit = True
                    if st.last_pos is not None and ts_dt > 0:
                        d = ((pos[0] - st.last_pos[0]) ** 2 + (pos[1] - st.last_pos[1]) ** 2) ** 0.5
                        if d / ts_dt < self.stop_speed:
                            st.stopped_while_ped = True
            elif st.inside and not st.counted:
                # exited the conflict zone -> judge the transit
                if st.ped_seen_during_transit and not st.stopped_while_ped:
                    self.violations += 1
                st.counted = True
            st.inside = inside
            st.last_pos = pos
        # forget vehicles whose tracks vanished (ephemeral state)
        for tid in [t for t in self._veh if t not in seen]:
            st = self._veh.pop(tid)
            if st.inside and not st.counted and st.ped_seen_during_transit \
                    and not st.stopped_while_ped:
                self.violations += 1


@dataclass
class CrossingMetrics:
    """Per-frame update -> aggregate counters for one crossing."""
    ped_line: LineCounter
    veh_line: LineCounter
    crosswalk: PolygonZone
    yield_mon: YieldMonitor
    occupancy_sec: float = 0.0
    _last_ts: float | None = field(default=None, repr=False)

    def update(self, ts: float,
               ped_tracks: dict[int, tuple[float, float]],
               veh_tracks: dict[int, tuple[float, float]]) -> dict[str, float]:
        dt = 0.0 if self._last_ts is None else max(0.0, ts - self._last_ts)
        self._last_ts = ts

        ped_before, veh_before = self.ped_line.total, self.veh_line.total
        for tid, pos in ped_tracks.items():
            self.ped_line.update(tid, pos)
        for tid, pos in veh_tracks.items():
            self.veh_line.update(tid, pos)

        ped_in = any(self.crosswalk.contains(p) for p in ped_tracks.values())
        if ped_in:
            self.occupancy_sec += dt

        v_before = self.yield_mon.violations
        self.yield_mon.update(dt, veh_tracks, ped_in)

        return {
            "ped_crossed": self.ped_line.total - ped_before,
            "veh_passed": self.veh_line.total - veh_before,
            "occupancy_sec": dt if ped_in else 0.0,
            "yield_violations": self.yield_mon.violations - v_before,
        }
