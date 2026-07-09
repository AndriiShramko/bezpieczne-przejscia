"""safecross — privacy-first aggregate pedestrian-crossing safety pipeline.

Privacy invariants (enforced by design + tests):
- Frames live ONLY in RAM; nothing under this package ever writes an image to disk.
- Faces/plates are blurred at ingest, before any downstream use.
- Track IDs are ephemeral (RAM only); no face embeddings exist anywhere.
- Persisted data = aggregate counters only: stats_bucket + coverage_bucket + camera_health.
"""

__version__ = "0.1.0"
