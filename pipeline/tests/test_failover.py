"""THE failover fact-test (spec-verify §17):

Two live synthetic MJPEG sources of the SAME crossing. Mid-run the primary
is killed. Assertions:
- before the kill, frames come from primary; after, from backup;
- camera_health records down + failover;
- stats keep accruing AFTER the kill (counted from backup);
- coverage_bucket keeps accruing after the kill and the switch gap itself
  is NOT fabricated as observed time.

Prints a JSON timeline so the run leaves an auditable trace.
"""
import json
import time

from safecross.detect import BlobDetector
from safecross.ingest import FailoverPool, MjpegHttpSource
from safecross.runner import run
from safecross.storage import Store
from safecross.track import CentroidTracker
from safecross.zones import CrossingMetrics, LineCounter, PolygonZone, YieldMonitor

from tests.mjpeg_server import SyntheticMjpegServer
from tests.synthetic_scene import CROSSWALK, PED_LINE, VEH_LINE

RUN_SEC = 40.0
KILL_AFTER = 14.0


def _metrics():
    return CrossingMetrics(
        ped_line=LineCounter(*PED_LINE),
        veh_line=LineCounter(*VEH_LINE),
        crosswalk=PolygonZone(CROSSWALK),
        yield_mon=YieldMonitor(PolygonZone(CROSSWALK), stop_speed_px_s=15.0),
    )


def test_failover_primary_killed_stats_continue(tmp_path):
    epoch = time.time()
    primary = SyntheticMjpegServer(scene_epoch=epoch).start()
    backup = SyntheticMjpegServer(scene_epoch=epoch).start()

    store = Store(str(tmp_path / "stats.db"))
    health_log = []

    def health_cb(ts, sid, event, detail):
        health_log.append({"t": round(ts - epoch, 2), "source": sid, "event": event})
        store.health_event(ts, sid, event, detail)

    pool = FailoverPool(
        "demo-crossing",
        [MjpegHttpSource("primary", primary.url),
         MjpegHttpSource("backup", backup.url)],
        stall_timeout=3.0, holddown=30.0, health_cb=health_cb,
    )

    kill_state = {"killed_at": None}
    counter_events = []

    def on_frame(ts, frame, source_id, counters):
        if kill_state["killed_at"] is None and ts - epoch >= KILL_AFTER:
            primary.kill()
            kill_state["killed_at"] = ts
        for k, v in counters.items():
            if v:
                counter_events.append(
                    {"t": round(ts - epoch, 2), "source": source_id,
                     "metric": k, "value": v})

    frames = run(pool, BlobDetector(), CentroidTracker(), CentroidTracker(),
                 _metrics(), store, duration=RUN_SEC, on_frame=on_frame)
    backup.kill()

    killed_at = kill_state["killed_at"]
    assert killed_at is not None, "kill never triggered"
    assert frames > 50

    # 1) source switched after the kill
    pre_kill = [e for e in counter_events if e["t"] < killed_at - epoch]
    post_kill = [e for e in counter_events if e["t"] > killed_at - epoch + 6.0]
    assert pre_kill and all(e["source"] == "primary" for e in pre_kill)
    assert post_kill, "no counters accrued after primary death"
    assert all(e["source"] == "backup" for e in post_kill)

    # 2) health trail recorded the failure and the failover
    events = [(h["source"], h["event"]) for h in health_log]
    assert ("primary", "down") in events
    assert ("backup", "failover") in events

    # 3) meaningful safety metrics accrued on BOTH sides of the kill
    def total(evts, metric):
        return sum(e["value"] for e in evts if e["metric"] == metric)
    assert total(pre_kill, "ped_crossed") >= 1
    assert total(post_kill, "ped_crossed") >= 1
    assert total(post_kill, "veh_passed") >= 1

    # 4) coverage continues from backup; switch gap not fabricated
    rows = store.rates("demo-crossing", "ped_crossed")
    assert rows, "no coverage buckets at all"
    observed_total = sum(p.observed_sec for p in rows)
    assert observed_total > RUN_SEC * 0.5
    # observed time can never exceed wall-clock run time (anti-fabrication)
    assert observed_total <= RUN_SEC + 2.0

    # yield violations detected on the synthetic scene (one per 10s loop)
    violations = total(pre_kill + post_kill, "yield_violations")
    assert violations >= 1

    print("\nFAILOVER_TIMELINE " + json.dumps({
        "killed_at_s": round(killed_at - epoch, 2),
        "frames": frames,
        "health": health_log,
        "pre_kill_counts": {m: total(pre_kill, m) for m in
                            ("ped_crossed", "veh_passed", "yield_violations")},
        "post_kill_counts": {m: total(post_kill, m) for m in
                             ("ped_crossed", "veh_passed", "yield_violations")},
        "observed_sec_total": round(observed_total, 1),
    }))
    store.close()
