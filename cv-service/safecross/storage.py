"""Aggregate-only storage: stats_bucket + coverage_bucket + camera_health.

Nothing else is ever persisted. Rates are computed at query time and
normalized by observed_sec; buckets with insufficient coverage are
reported as no-data (None), never as zeros.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

BUCKET_SEC = 60
# Below this fraction of a bucket actually observed, the bucket is no-data.
MIN_COVERAGE_FRAC = 0.25

SCHEMA = """
CREATE TABLE IF NOT EXISTS stats_bucket (
    crossing_id TEXT NOT NULL,
    bucket_utc  TEXT NOT NULL,   -- ISO minute, e.g. 2026-07-09T14:03Z
    metric      TEXT NOT NULL,   -- ped_crossed | veh_passed | yield_violations |
                                 -- occupancy_sec | head_down_n | head_sample_n | conflicts
    value       REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (crossing_id, bucket_utc, metric)
);
CREATE TABLE IF NOT EXISTS coverage_bucket (
    crossing_id  TEXT NOT NULL,
    bucket_utc   TEXT NOT NULL,
    observed_sec REAL NOT NULL DEFAULT 0,  -- against fabricated zeros
    active_source TEXT,
    PRIMARY KEY (crossing_id, bucket_utc)
);
CREATE TABLE IF NOT EXISTS camera_health (
    ts_utc    TEXT NOT NULL,
    source_id TEXT NOT NULL,
    event     TEXT NOT NULL,   -- up | stall | down | failover | recovered
    detail    TEXT
);
"""


def bucket_key(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%MZ")


@dataclass
class RatePoint:
    bucket_utc: str
    observed_sec: float
    value: float | None       # raw counter, None => no-data
    rate_per_hour: float | None  # normalized, None => no-data


class Store:
    def __init__(self, db_path: str):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def add_stat(self, crossing_id: str, ts: float, metric: str, value: float) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO stats_bucket(crossing_id, bucket_utc, metric, value)
                   VALUES (?,?,?,?)
                   ON CONFLICT(crossing_id, bucket_utc, metric)
                   DO UPDATE SET value = value + excluded.value""",
                (crossing_id, bucket_key(ts), metric, value),
            )
            self._conn.commit()

    def add_coverage(self, crossing_id: str, ts: float, observed_sec: float,
                     active_source: str) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO coverage_bucket(crossing_id, bucket_utc, observed_sec, active_source)
                   VALUES (?,?,?,?)
                   ON CONFLICT(crossing_id, bucket_utc)
                   DO UPDATE SET observed_sec = observed_sec + excluded.observed_sec,
                                 active_source = excluded.active_source""",
                (crossing_id, bucket_key(ts), observed_sec, active_source),
            )
            self._conn.commit()

    def health_event(self, ts: float, source_id: str, event: str, detail: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO camera_health(ts_utc, source_id, event, detail) VALUES (?,?,?,?)",
                (datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(), source_id, event, detail),
            )
            self._conn.commit()

    # ---- queries (rates normalized on observed_sec; gaps => no-data) ----

    def rates(self, crossing_id: str, metric: str) -> list[RatePoint]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT c.bucket_utc, c.observed_sec, s.value
                   FROM coverage_bucket c
                   LEFT JOIN stats_bucket s
                     ON s.crossing_id = c.crossing_id
                    AND s.bucket_utc = c.bucket_utc AND s.metric = ?
                   WHERE c.crossing_id = ?
                   ORDER BY c.bucket_utc""",
                (metric, crossing_id),
            ).fetchall()
        out: list[RatePoint] = []
        for bucket, observed, value in rows:
            if observed < BUCKET_SEC * MIN_COVERAGE_FRAC:
                out.append(RatePoint(bucket, observed, None, None))  # no-data, NOT zero
            else:
                v = value or 0.0
                out.append(RatePoint(bucket, observed, v, v * 3600.0 / observed))
        return out

    def health(self, limit: int = 100) -> list[tuple]:
        with self._lock:
            return self._conn.execute(
                "SELECT ts_utc, source_id, event, detail FROM camera_health "
                "ORDER BY ts_utc DESC LIMIT ?", (limit,)
            ).fetchall()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
