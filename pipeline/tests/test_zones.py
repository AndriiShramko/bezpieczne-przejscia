"""Topology metrics: line counts, occupancy, yield-violation semantics."""
from safecross.zones import CrossingMetrics, LineCounter, PolygonZone, YieldMonitor

CROSSWALK = [(200.0, 140.0), (440.0, 140.0), (440.0, 220.0), (200.0, 220.0)]


def test_line_counts_each_track_once_per_crossing():
    lc = LineCounter((200, 180), (440, 180))
    for y in (100, 150, 179, 181, 220, 300):
        lc.update(1, (300, y))
    assert lc.total == 1


def test_line_counts_direction_separately():
    lc = LineCounter((200, 180), (440, 180))
    lc.update(1, (300, 100)); lc.update(1, (300, 200))
    lc.update(2, (310, 250)); lc.update(2, (310, 120))
    assert lc.count_ab + lc.count_ba == 2
    assert lc.count_ab >= 1 and lc.count_ba >= 1


def test_yield_violation_when_vehicle_never_stops():
    ym = YieldMonitor(PolygonZone(CROSSWALK), stop_speed_px_s=15.0)
    # vehicle transits zone at ~100 px/s while pedestrian occupies crosswalk
    xs = [150, 250, 350, 450, 550]
    for i, x in enumerate(xs):
        ym.update(1.0, {7: (float(x), 180.0)}, ped_in_crosswalk=True)
    assert ym.violations == 1


def test_no_violation_when_vehicle_stops_for_pedestrian():
    ym = YieldMonitor(PolygonZone(CROSSWALK), stop_speed_px_s=15.0)
    # vehicle enters zone then halts (displacement ~0) while pedestrian present
    seq = [150, 250, 252, 253, 254, 350, 550]
    for x in seq:
        ym.update(1.0, {7: (float(x), 180.0)}, ped_in_crosswalk=True)
    assert ym.violations == 0


def test_no_violation_when_no_pedestrian():
    ym = YieldMonitor(PolygonZone(CROSSWALK), stop_speed_px_s=15.0)
    for x in (150, 250, 350, 450, 550):
        ym.update(1.0, {7: (float(x), 180.0)}, ped_in_crosswalk=False)
    assert ym.violations == 0


def test_crossing_metrics_integration():
    m = CrossingMetrics(
        ped_line=LineCounter((200, 180), (440, 180)),
        veh_line=LineCounter((320, 120), (320, 240)),
        crosswalk=PolygonZone(CROSSWALK),
        yield_mon=YieldMonitor(PolygonZone(CROSSWALK), stop_speed_px_s=15.0),
    )
    t = 0.0
    totals = {"ped_crossed": 0.0, "veh_passed": 0.0, "occupancy_sec": 0.0,
              "yield_violations": 0.0}
    # pedestrian walks down through crosswalk; vehicle drives through at speed
    ped_y = [100, 140, 160, 179, 181, 200, 240, 300]
    veh_x = [100, 180, 260, 340, 420, 500, 580, 660]
    for py, vx in zip(ped_y, veh_x):
        t += 0.5
        c = m.update(t, {1: (310.0, float(py))}, {50: (float(vx), 180.0)})
        for k, v in c.items():
            totals[k] += v
    assert totals["ped_crossed"] == 1
    assert totals["veh_passed"] == 1
    assert totals["occupancy_sec"] > 0
    assert totals["yield_violations"] == 1
