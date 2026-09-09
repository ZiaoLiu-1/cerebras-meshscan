from __future__ import annotations

import argparse
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "viewer"))

from server import RequestError, create_server, parse_trace_request  # noqa: E402


class RequestValidationTests(unittest.TestCase):
    def test_accepts_empty_and_negative_values(self) -> None:
        self.assertEqual(parse_trace_request({"values": [], "pe_count": 4}), ([], 4))
        self.assertEqual(
            parse_trace_request({"values": [-3, 2], "pe_count": 2}),
            ([-3, 2], 2),
        )

    def test_rejects_bool_and_out_of_range_values(self) -> None:
        with self.assertRaises(RequestError):
            parse_trace_request({"values": [True], "pe_count": 1})
        with self.assertRaises(RequestError):
            parse_trace_request({"values": [2**31], "pe_count": 1})

    def test_rejects_unexpected_shape_and_pe_count(self) -> None:
        with self.assertRaises(RequestError):
            parse_trace_request({"values": [1], "pe_count": 17})
        with self.assertRaises(RequestError):
            parse_trace_request({"values": [1], "pe_count": 1, "extra": 2})


class ViewerHttpTests(unittest.TestCase):
    binary: Path
    server = None
    thread = None
    base_url = ""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server(cls.binary, 0)
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request_json(
        self, path: str, payload: dict[str, object] | None = None
    ) -> tuple[int, dict[str, object], dict[str, str]]:
        data = None
        headers = {}
        method = "GET"
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
            method = "POST"
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        body = json.loads(response.read())
        return response.status, body, dict(response.headers)

    def test_health_and_security_headers(self) -> None:
        status, payload, headers = self.request_json("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["model"], "cpu-logical-pe")
        self.assertTrue(payload["binary_ready"])
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Frame-Options"], "DENY")

    def test_known_trace_comes_from_cpp_oracle(self) -> None:
        status, payload, _ = self.request_json(
            "/api/trace",
            {"values": [3, -2, 5, 7, -4, 1], "pe_count": 4},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["source"], "meshscan-reference")
        trace = payload["trace"]
        self.assertEqual(trace["output"], [3, 1, 6, 13, 9, 10])
        self.assertEqual(
            [(pe["begin"], pe["end"]) for pe in trace["pes"]],
            [(0, 2), (2, 4), (4, 5), (5, 6)],
        )

    def test_empty_input_and_more_pes_than_values(self) -> None:
        status, payload, _ = self.request_json(
            "/api/trace", {"values": [], "pe_count": 4}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["trace"]["output"], [])

        status, payload, _ = self.request_json(
            "/api/trace", {"values": [1, 2], "pe_count": 4}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["trace"]["output"], [1, 3])
        self.assertEqual(payload["trace"]["pes"][3]["output"], [])

    def test_validation_and_overflow_errors(self) -> None:
        status, payload, _ = self.request_json(
            "/api/trace", {"values": [1], "pe_count": 17}
        )
        self.assertEqual(status, 400)
        self.assertIn("between 1 and 16", payload["error"])

        status, payload, _ = self.request_json(
            "/api/trace", {"values": [2**31 - 1, 1], "pe_count": 2}
        )
        self.assertEqual(status, 422)
        self.assertIn("signed 32-bit range", payload["error"])

    def test_static_page_has_boundary_and_no_remote_assets(self) -> None:
        response = urllib.request.urlopen(self.base_url + "/", timeout=3)
        page = response.read().decode("utf-8")
        self.assertIn("CPU logical model — not WSE simulator", page)
        self.assertNotIn("https://", page)
        self.assertNotIn("http://", page)
        self.assertIn('src="/app.js"', page)
        self.assertIn('href="/styles.css"', page)


def parse_binary_argument() -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--binary", type=Path, required=True)
    arguments, remaining = parser.parse_known_args()
    sys.argv[1:] = remaining
    return arguments.binary.resolve()


if __name__ == "__main__":
    ViewerHttpTests.binary = parse_binary_argument()
    if not ViewerHttpTests.binary.is_file():
        raise SystemExit(f"missing test binary: {ViewerHttpTests.binary}")
    unittest.main()
