"""Loopback-only HTTP server for the MeshScan CPU trace viewer."""

from __future__ import annotations

import argparse
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VIEWER_ROOT = Path(__file__).resolve().parent
TOOLS_ROOT = PROJECT_ROOT / "tools"
sys.path.insert(0, str(TOOLS_ROOT))

from oracle_bridge import INT32_MAX, INT32_MIN, OracleError, run_oracle  # noqa: E402

MAX_BODY_BYTES = 64 * 1024
MAX_VALUES = 256
MAX_VISIBLE_PES = 16
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


class RequestError(ValueError):
    """A safe client-facing request validation error."""


def parse_trace_request(payload: Any) -> tuple[list[int], int]:
    if not isinstance(payload, dict):
        raise RequestError("Request body must be a JSON object.")
    if set(payload) != {"values", "pe_count"}:
        raise RequestError("Request needs exactly values and pe_count.")

    values = payload["values"]
    pe_count = payload["pe_count"]
    if not isinstance(values, list):
        raise RequestError("Values must be a JSON array of int32 numbers.")
    if len(values) > MAX_VALUES:
        raise RequestError(f"Values may contain at most {MAX_VALUES} items.")

    checked_values: list[int] = []
    for index, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, int):
            raise RequestError(f"Value {index + 1} must be an integer.")
        if value < INT32_MIN or value > INT32_MAX:
            raise RequestError(f"Value {index + 1} falls outside int32 range.")
        checked_values.append(value)

    if isinstance(pe_count, bool) or not isinstance(pe_count, int):
        raise RequestError("pe_count must be an integer from 1 to 16.")
    if pe_count < 1 or pe_count > MAX_VISIBLE_PES:
        raise RequestError("pe_count must be between 1 and 16 for this viewer.")
    return checked_values, pe_count


def make_handler(binary: Path) -> type[BaseHTTPRequestHandler]:
    class ViewerHandler(BaseHTTPRequestHandler):
        server_version = "MeshScanViewer/1.0"

        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
                "form-action 'self'",
            )
            super().end_headers()

        def log_message(self, format_string: str, *args: object) -> None:
            sys.stderr.write(
                f"[{self.log_date_time_string()}] {format_string % args}\n"
            )

        def _send_bytes(
            self, status: HTTPStatus, body: bytes, content_type: str
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self._send_bytes(status, body, "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/health":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "status": "ready",
                        "model": "cpu-logical-pe",
                        "binary_ready": binary.is_file(),
                    },
                )
                return

            static_entry = STATIC_FILES.get(self.path)
            if static_entry is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Route not found."})
                return
            filename, content_type = static_entry
            body = (VIEWER_ROOT / filename).read_bytes()
            self._send_bytes(HTTPStatus.OK, body, content_type)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/trace":
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Route not found."})
                return

            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "Content-Length must be an integer."},
                )
                return
            if content_length <= 0 or content_length > MAX_BODY_BYTES:
                self._send_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"error": "Request body must be between 1 byte and 64 KiB."},
                )
                return

            try:
                payload = json.loads(self.rfile.read(content_length))
                values, pe_count = parse_trace_request(payload)
                trace = run_oracle(binary, values, pe_count)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "Request body must be valid UTF-8 JSON."},
                )
                return
            except RequestError as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            except OracleError as error:
                self._send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(error)})
                return
            except OSError:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "The local C++ oracle could not be started."},
                )
                return

            self._send_json(
                HTTPStatus.OK,
                {"trace": trace, "source": "meshscan-reference"},
            )

    return ViewerHandler


def create_server(binary: Path, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(binary))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--binary",
        type=Path,
        default=PROJECT_ROOT / "build" / "meshscan-reference",
    )
    arguments = parser.parse_args()
    binary = arguments.binary.resolve()
    if not binary.is_file():
        parser.error(f"missing C++ oracle: {binary}; run 'make reference' first")
    if arguments.port < 0 or arguments.port > 65535:
        parser.error("--port must be between 0 and 65535")

    server = create_server(binary, arguments.port)
    host, port = server.server_address
    print(f"MeshScan Trace Desk: http://{host}:{port}", flush=True)
    print("CPU logical model — not WSE simulator", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
