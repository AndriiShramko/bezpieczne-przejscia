# Method

1. **Ingest with failover.** A priority pool of sources watches ONE crossing.
   Health checks detect stalls; the pool auto-switches to a backup view of the
   same crossing and auto-returns (with hold-down against flapping).
2. **In-RAM analysis.** An Apache-2.0 COCO detector (YOLOX class) finds
   persons/vehicles. Faces (top of person box) and plate strips are pixelated
   immediately. Frames are never persisted.
3. **Ephemeral tracking.** RAM-only track IDs feed zone topology.
4. **Zone topology (no calibration needed):** line-crossing counters,
   crosswalk occupancy, and "failed to yield" = vehicle transits the conflict
   zone without stopping while a pedestrian occupies the crosswalk.
5. **Aggregate storage:** per-minute counters (stats_bucket), actually
   observed seconds (coverage_bucket), camera health events. Rates are
   normalized to observed time at query; gaps render as no-data.

Degradations are explicit: panoramic framing -> counting only; PET/TTC in
seconds -> only on a metrically calibrated camera; head-down % -> sampled
proxy with a Wilson confidence interval.
