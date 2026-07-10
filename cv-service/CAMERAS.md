# Cameras — how to add them and what is supported

Cameras are managed from the **`/admin`** panel (requires `ADMIN_TOKEN`) and stored in
`/data/cameras.json`. Changes go live within ~5 s (the worker hot-reloads the config).
The **active** camera is what everyone sees on the main page; the rest form a **failover
pool** (when the active stream dies, the system switches to the next live one — every
camera keeps **its own statistics** and its own crossing polygon).

## Camera parameters

| Field | Meaning |
|-------|---------|
| `id` | short identifier (e.g. `sch`), used in file/statistics names |
| `label` | publicly visible label (place + operator credit) |
| `url` | stream address (see "Source types") |
| `referer` | Referer header if the provider requires one |
| `poly` | crossing-zone polygon — 4–6 `[x,y]` points as **fractions 0..1** of width/height |
| `m_per_px_fullw` | scale: meters per pixel at full frame width (for speed estimates; ~0.05–0.12) |

## Source types (what works)

1. **HLS `.m3u8` (RECOMMENDED)** — a direct playlist link, e.g.
   `https://host/hls/<cam>/index.m3u8`. Stable and works from datacenter IPs. How to find
   the link: open the camera page -> DevTools -> Network -> filter `m3u8` -> copy the
   playlist URL (and check that `.ts` segments keep flowing). If the provider requires a
   Referer, put it in the `referer` field.
2. **MJPEG (`multipart/x-mixed-replace`)** — a direct IP-camera URL. Works out of the box.
3. **RTSP** — provide `rtsp://…`; OpenCV/FFmpeg usually handles it (cameras behind NAT
   need a public relay). Prefer HLS for stability.
4. **YouTube (`watch?v=…` or a channel `/live`)** — **WARNING:** YouTube blocks datacenter
   IPs (bot-check), so on a VPS it **usually does NOT work** without cookies/proxy. Fine
   for tests from a home connection. This is why production uses direct HLS.

**Rejected:** `.jpg` snapshots refreshed every N seconds (not enough frames for motion
analysis), panoramas where a pedestrian is <15 px tall, and `insecam`-style unsecured
third-party cameras.

## Choosing the `poly` zone and the scale

- `poly` outlines the **zebra stripes** plus a narrow approach strip where a car passes
  the pedestrian. List the points clockwise.
- `m_per_px_fullw`: measure something of known length (a PL lane is ~3.5 m wide, or the
  zebra length). Speeds are **indicative** (monocular) — for statistics, not for fines.
- After a camera is added, the AI calibrates the scene **once** (crossings incl. split
  halves, bike crossings, refuge islands, traffic lights, per-crossing event rules,
  static objects to ignore) and stores it in `/data/scenes/scene_<id>.json`. The admin
  can then correct every polygon and rule by hand in the `/admin` zone editor
  (drag points, add/delete zones, edit rules — hot-reloaded without a restart).

## Failover and reliability

- Try order: `active` -> rest of the list. A dead stream triggers an automatic switch to
  the next live camera, and back once it recovers.
- The `/admin` panel shows **which cameras keep dying** (down/failover/up counts + last
  failure) — use that to pick good backups.
- Events and statistics are **per camera**, so switching never mixes data from
  different crossings.
