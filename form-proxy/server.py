"""Lead-form proxy: static POST /api/lead -> Telegram sendMessage.

- Bot token/chat id come ONLY from env (TG_BOT_TOKEN, TG_CHAT_ID) — never
  from client JS, never in git.
- Honeypot field 'website': silently accepted and dropped.
- In-RAM rate limit per IP. No PII is ever logged (status codes only).
- Optional dev mode: SERVE_STATIC=<dir> also serves the site locally.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from collections import defaultdict, deque
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 16 * 1024
RATE_N, RATE_WINDOW = 5, 3600.0
SEGMENT_TYPE = {
    "samorząd": "gov", "government": "gov",
    "firma": "company", "company": "company",
    "projekt ue / nauka": "research", "eu project / research": "research",
}

_hits: dict[str, deque] = defaultdict(deque)


def _allowed(ip: str) -> bool:
    q = _hits[ip]
    now = time.time()
    while q and now - q[0] > RATE_WINDOW:
        q.popleft()
    if len(q) >= RATE_N:
        return False
    q.append(now)
    return True


def send_telegram(text: str) -> bool:
    token = os.environ["TG_BOT_TOKEN"]
    chat = os.environ["TG_CHAT_ID"]
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat, "text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r).get("ok", False)


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):  # no PII in logs; keep method+status only
        print(f"{time.strftime('%H:%M:%S')} {self.command} {self.path.split('?')[0]}",
              flush=True)

    def do_POST(self):
        if self.path != "/api/lead":
            return self._json(404, {"ok": False})
        ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0]
        if not _allowed(ip):
            return self._json(429, {"ok": False, "error": "rate-limited"})
        try:
            n = min(int(self.headers.get("Content-Length", 0)), MAX_BODY)
            data = json.loads(self.rfile.read(n))
        except Exception:
            return self._json(400, {"ok": False, "error": "bad json"})
        if data.get("website"):          # honeypot -> pretend success
            return self._json(200, {"ok": True})
        name = str(data.get("name", "")).strip()[:200]
        email = str(data.get("email", "")).strip()[:200]
        msg = str(data.get("message", "")).strip()[:2000]
        if not (name and email and msg and data.get("consent")):
            return self._json(400, {"ok": False, "error": "missing fields"})
        seg = str(data.get("segment", "")).strip()
        lead_type = SEGMENT_TYPE.get(seg.lower(), "other")
        text = (f"[LEAD][site=patrol][type={lead_type}]\n"
                f"Name: {name}\n"
                f"Org: {str(data.get('organization', '')).strip()[:200]}\n"
                f"Role: {str(data.get('role', '')).strip()[:200]}\n"
                f"Segment: {seg[:100]}\n"
                f"Email: {email}\n"
                f"Lang: {str(data.get('lang', ''))[:5]}\n"
                f"Message:\n{msg}")
        try:
            ok = send_telegram(text)
        except Exception:
            ok = False
        return self._json(200 if ok else 502, {"ok": ok})

    def _json(self, code: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if os.environ.get("SERVE_STATIC"):
            return super().do_GET()
        if self.path.startswith("/healthz"):
            return self._json(200, {"ok": True})
        return self._json(404, {"ok": False})


def main():
    port = int(os.environ.get("PORT", "8087"))
    static = os.environ.get("SERVE_STATIC")
    if static:
        os.chdir(static)
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"form-proxy on :{port} static={bool(static)}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
