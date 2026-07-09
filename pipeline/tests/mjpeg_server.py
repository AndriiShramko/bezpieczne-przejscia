"""In-RAM MJPEG HTTP test server streaming the synthetic scene.

Frames are encoded with cv2.imencode in memory; nothing touches disk.
"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

from tests import synthetic_scene

BOUNDARY = b"--syntheticframe"


class _Handler(BaseHTTPRequestHandler):
    fps = 8.0

    def log_message(self, *args):  # silence
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type",
                         "multipart/x-mixed-replace; boundary=syntheticframe")
        self.end_headers()
        epoch = self.server.scene_epoch  # type: ignore[attr-defined]
        try:
            while not self.server.stopping:  # type: ignore[attr-defined]
                frame = synthetic_scene.render(time.time() - epoch)
                ok, jpg = cv2.imencode(".jpg", frame)
                if not ok:
                    continue
                self.wfile.write(BOUNDARY + b"\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n\r\n")
                self.wfile.write(jpg.tobytes())
                self.wfile.write(b"\r\n")
                time.sleep(1.0 / self.fps)
        except (BrokenPipeError, ConnectionError, OSError):
            pass


class SyntheticMjpegServer:
    """Start/stop wrapper. kill() simulates a camera going down."""

    def __init__(self, scene_epoch: float | None = None):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.stopping = False  # type: ignore[attr-defined]
        self.httpd.scene_epoch = scene_epoch or time.time()  # type: ignore[attr-defined]
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/stream.mjpg"

    def start(self) -> "SyntheticMjpegServer":
        self._thread.start()
        return self

    def kill(self) -> None:
        self.httpd.stopping = True  # type: ignore[attr-defined]
        self.httpd.shutdown()
        self.httpd.server_close()
