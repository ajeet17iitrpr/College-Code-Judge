from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


FRONTEND_DIR = Path(__file__).resolve().parent
STATIC_DIR = FRONTEND_DIR / "static"
BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
FRONTEND_HOST = os.getenv("FRONTEND_HOST", "0.0.0.0")
FRONTEND_PORT = int(os.getenv("FRONTEND_PORT", "8080"))
MAX_PROXY_BODY = int(os.getenv("FRONTEND_MAX_PROXY_BODY", str(2 * 1024 * 1024)))


class FrontendHandler(BaseHTTPRequestHandler):
    server_version = "CollegeJudgeFrontend"
    sys_version = ""

    def do_GET(self) -> None:
        self._dispatch()

    def do_HEAD(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_PATCH(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def do_OPTIONS(self) -> None:
        self._dispatch()

    def log_message(self, format_string: str, *args: object) -> None:
        # Do not log request bodies, cookies, or credentials.
        print(f"[{self.log_date_time_string()}] {self.client_address[0]} {format_string % args}")


    def _site_allowed(self) -> tuple[bool, str]:
        request = urllib.request.Request(
            f"{BACKEND_URL}/api/access/check",
            headers={
                "X-Forwarded-For": self.client_address[0],
                "X-Forwarded-Proto": "http",
                "User-Agent": self.headers.get("User-Agent", "")[:512],
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return bool(payload.get("allowed", True)), str(payload.get("client_ip", self.client_address[0]))
        except Exception:
            # Do not accidentally lock the lab out only because the backend check endpoint is temporarily unavailable.
            return True, self.client_address[0]

    def _send_access_denied(self, client_ip: str) -> None:
        body = f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Access denied</title><style>body{{font-family:system-ui;background:#101827;color:#eef3ff;display:grid;place-items:center;min-height:100vh;margin:0}}main{{max-width:620px;padding:32px;background:#151d2c;border:1px solid #2b3850;border-radius:16px}}code{{color:#9ab1ff}}</style></head><body><main><h1>Access denied</h1><p>This computer's IP address is not allowed to access the programming portal.</p><p>Detected IP: <code>{client_ip}</code></p><p>Contact the lab administrator if this computer should be permitted.</p></main></body></html>""".encode("utf-8")
        self.send_response(403)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._send_security_headers(api_response=False)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _dispatch(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api" or path.startswith("/api/"):
            self._proxy_api()
            return

        if self.command not in {"GET", "HEAD"}:
            self._send_json(405, {"detail": "Method not allowed."})
            return

        if path == "/" or path == "/index.html":
            allowed, client = self._site_allowed()
            if not allowed:
                self._send_access_denied(client)
                return
            self._serve_file(STATIC_DIR / "index.html", no_store=True)
            return

        if path.startswith("/static/"):
            relative = path.removeprefix("/static/")
            candidate = (STATIC_DIR / relative).resolve()
            if not candidate.is_relative_to(STATIC_DIR.resolve()):
                self._send_json(404, {"detail": "Not found."})
                return
            self._serve_file(candidate, no_store=False)
            return

        # The application uses hash-based routes. Returning index.html here also
        # keeps copied/bookmarked paths usable if a reverse proxy rewrites URLs.
        allowed, client = self._site_allowed()
        if not allowed:
            self._send_access_denied(client)
            return
        self._serve_file(STATIC_DIR / "index.html", no_store=True)

    def _proxy_api(self) -> None:
        content_length = self.headers.get("Content-Length")
        body_length = int(content_length) if content_length else 0
        if body_length > MAX_PROXY_BODY:
            self._send_json(413, {"detail": "Request body is too large."})
            return

        body = self.rfile.read(body_length) if body_length else None
        target = f"{BACKEND_URL}{self.path}"

        forwarded_headers: dict[str, str] = {}
        blocked_request_headers = {
            "host",
            "connection",
            "content-length",
            "transfer-encoding",
            "upgrade",
            "proxy-connection",
        }
        for name, value in self.headers.items():
            if name.lower() not in blocked_request_headers:
                forwarded_headers[name] = value

        forwarded_headers["X-Forwarded-For"] = self.client_address[0]
        forwarded_headers["X-Forwarded-Proto"] = "http"
        forwarded_headers["X-Forwarded-Host"] = self.headers.get("Host", "")

        request = urllib.request.Request(
            target,
            data=body,
            headers=forwarded_headers,
            method=self.command,
        )

        try:
            upstream = urllib.request.urlopen(request, timeout=60)
        except urllib.error.HTTPError as exc:
            upstream = exc
        except (urllib.error.URLError, TimeoutError):
            self._send_json(
                502,
                {
                    "detail": "Backend service is unavailable."
                },
            )
            return

        response_body = upstream.read()
        self.send_response(upstream.status)

        blocked_response_headers = {
            "connection",
            "content-length",
            "transfer-encoding",
            "content-security-policy",
            "server",
        }
        for name, value in upstream.headers.raw_items():
            if name.lower() not in blocked_response_headers:
                self.send_header(name, value)

        self._send_security_headers(api_response=True)
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response_body)

    def _serve_file(self, path: Path, *, no_store: bool) -> None:
        if not path.is_file():
            self._send_json(404, {"detail": "Not found."})
            return

        data = path.read_bytes()
        content_type, _ = mimetypes.guess_type(path.name)
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self._send_security_headers(api_response=False)
        if no_store:
            self.send_header("Cache-Control", "no-store")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._send_security_headers(api_response=True)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_security_headers(self, *, api_response: bool) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        if not api_response:
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self'; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                "font-src 'self'; "
                "worker-src 'self' blob:; "
                "object-src 'none'; "
                "base-uri 'none'; "
                "form-action 'self'; "
                "frame-ancestors 'none'",
            )


def main() -> None:
    if not (STATIC_DIR / "index.html").is_file():
        raise SystemExit(f"Missing frontend file: {STATIC_DIR / 'index.html'}")

    server = ThreadingHTTPServer((FRONTEND_HOST, FRONTEND_PORT), FrontendHandler)
    print(f"Frontend: http://{FRONTEND_HOST}:{FRONTEND_PORT}")
    print(f"Backend proxy: {BACKEND_URL}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping frontend server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
