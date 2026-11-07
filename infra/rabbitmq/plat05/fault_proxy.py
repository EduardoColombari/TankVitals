"""Internal-only Influx HTTP fault injector for isolated PLAT-06 tests."""

from __future__ import annotations

import http.client
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


UPSTREAM = urlsplit(os.getenv("INFLUX_UPSTREAM", "http://influxdb:8086"))
_lock = threading.Lock()
_fail_next = 0
_injected_failures = 0


class Handler(BaseHTTPRequestHandler):
    server_version = "PLAT06TestProxy"

    def log_message(self, _format: str, *_args: object) -> None:
        # Request paths and headers can contain sensitive test data.
        return

    def do_GET(self) -> None:
        global _fail_next, _injected_failures
        if self.path.startswith("/__test__/fail-next"):
            try:
                count = int(parse_qs(urlsplit(self.path).query).get("count", ["0"])[0])
            except ValueError:
                self.send_error(400)
                return
            if not 1 <= count <= 3:
                self.send_error(400)
                return
            with _lock:
                _fail_next = count
                _injected_failures = 0
            self.send_response(204)
            self.end_headers()
            return
        if self.path == "/__test__/status":
            with _lock:
                body = f"{_injected_failures}".encode("ascii")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._forward()

    def do_POST(self) -> None:
        global _fail_next, _injected_failures
        if self.path.startswith("/api/v2/write"):
            with _lock:
                if _fail_next:
                    _fail_next -= 1
                    _injected_failures += 1
                    fail = True
                else:
                    fail = False
            if fail:
                # A controlled one-shot client error avoids the Influx SDK's
                # own transport retry loop, exercising the Writer retry path.
                self.send_response(400)
                self.send_header("X-PLAT06-Test-Fault", "injected")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        self._forward()

    def do_OPTIONS(self) -> None:
        self._forward()

    def _forward(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        connection = http.client.HTTPConnection(UPSTREAM.hostname, UPSTREAM.port or 80, timeout=30)
        try:
            headers = {
                key: value for key, value in self.headers.items()
                if key.lower() not in {"host", "connection", "transfer-encoding"}
            }
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            response_body = response.read()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in {"transfer-encoding", "connection", "content-length"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            if response_body:
                self.wfile.write(response_body)
        except (OSError, http.client.HTTPException):
            self.send_error(502)
        finally:
            connection.close()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.getenv("FAULT_PROXY_PORT", "8087"))), Handler).serve_forever()
