"""Black-box contract tests: no imports from the application and no inference mocks."""

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, Queue
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import UUID, uuid4

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OPENER = build_opener(ProxyHandler({}))
WAIT_SECONDS = 180 if os.environ.get("RUN_YOLO_TESTS") == "1" else 30


def http_json(url, payload=None, timeout=5):
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        response = OPENER.open(request, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        body = response.read()
        return response.status, json.loads(body) if body else None, response.headers


def wait_until(predicate, timeout=WAIT_SECONDS):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"Condition not met within {timeout} seconds")


class FixtureState:
    def __init__(self):
        self.callbacks = Queue()
        self.gates = {}
        self.failures = {}
        self.lock = threading.Lock()
        self.images = {}
        with Image.new("RGB", (640, 160), "white") as image:
            ImageDraw.Draw(image).text(
                (25, 40), "HELLO WORLD 123", fill="black", font=ImageFont.load_default(size=40)
            )
            stream = io.BytesIO()
            image.save(stream, format="PNG")
            self.images["text"] = stream.getvalue()
        with Image.new("RGB", (320, 320), "white") as image:
            stream = io.BytesIO()
            image.save(stream, format="PNG")
            self.images["blank"] = stream.getvalue()

    def release_all(self):
        with self.lock:
            for gate in self.gates.values():
                gate.set()


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, content=b"", content_type="application/octet-stream"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        state = self.server.fixture
        if self.path.startswith("/hold/"):
            token = self.path.rsplit("/", 1)[-1]
            if not state.gates[token].wait(15):
                self.reply(504)
                return
            self.reply(404)
        elif self.path == "/image/text":
            self.reply(200, state.images["text"], "image/png")
        elif self.path == "/image/blank":
            self.reply(200, state.images["blank"], "image/png")
        elif self.path == "/image/corrupt":
            self.reply(200, b"this is not an image", "image/png")
        elif self.path == "/image/oversize":
            self.reply(200, b"x" * (1024 * 1024 + 1), "image/png")
        elif self.path == "/image/redirect":
            self.send_response(302)
            self.send_header("Location", "/image/text")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.reply(404)

    def do_POST(self):
        state = self.server.fixture
        token = self.path.rsplit("/", 1)[-1]
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with state.lock:
            remaining = state.failures.get(token, 0)
            state.failures[token] = max(0, remaining - 1)
        state.callbacks.put((token, body, self.headers.get("Idempotency-Key")))
        self.reply(503 if remaining else 200, b"{}", "application/json")


class APIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = FixtureState()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.server.daemon_threads = True
        cls.server.fixture = cls.fixture
        cls.addClassCleanup(cls.server.server_close)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.server.shutdown)
        cls.fixture_url = f"http://127.0.0.1:{cls.server.server_port}"

        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        cls.api_url = f"http://127.0.0.1:{port}"
        cls.log = cls.enterClassContext(tempfile.TemporaryFile(mode="w+b"))
        env = os.environ.copy()
        env.update(
            INFERENCE_MAX_CONCURRENT_REQUESTS="10",
            INFERENCE_MAX_IMAGE_BYTES=str(1024 * 1024),
            INFERENCE_DOWNLOAD_TIMEOUT="20",
            INFERENCE_CALLBACK_TIMEOUT="3",
            INFERENCE_CALLBACK_ATTEMPTS="3",
            INFERENCE_TESSERACT_LANGUAGE="eng",
            INFERENCE_TESSERACT_TIMEOUT="10",
        )
        cls.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "src.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workers",
                "1",
            ],
            cwd=ROOT,
            env=env,
            stdout=cls.log,
            stderr=subprocess.STDOUT,
        )
        cls.addClassCleanup(cls.stop_application)

        def ready():
            if cls.process.poll() is not None:
                raise RuntimeError("Application exited before startup")
            try:
                return http_json(cls.api_url + "/health")[0] == 200
            except OSError:
                return False

        try:
            wait_until(ready, timeout=30)
        except (AssertionError, RuntimeError):
            cls.log.seek(0)
            raise RuntimeError(cls.log.read().decode(errors="replace")) from None

    @classmethod
    def stop_application(cls):
        cls.fixture.release_all()
        if cls.process.poll() is None:
            cls.process.terminate()
            try:
                cls.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.process.kill()
                cls.process.wait(timeout=5)

    def setUp(self):
        wait_until(lambda: http_json(self.api_url + "/health")[1]["active_jobs"] == 0)
        while True:
            try:
                self.fixture.callbacks.get_nowait()
            except Empty:
                break

    def tearDown(self):
        self.fixture.release_all()
        wait_until(lambda: http_json(self.api_url + "/health")[1]["active_jobs"] == 0)

    def request_body(self, image="/image/text", method="pytesseract"):
        token = str(uuid4())
        return token, {
            "image_url": self.fixture_url + image,
            "method": method,
            "callback_url": self.fixture_url + "/callback/" + token,
        }

    def submit(self, body):
        code, response, _ = http_json(self.api_url + "/infer", body)
        self.assertEqual(code, 202, response)
        self.assertEqual(response["status"], "accepted")
        UUID(response["job_id"])
        return response["job_id"]

    def receive(self, token):
        received_token, payload, key = self.fixture.callbacks.get(timeout=WAIT_SECONDS)
        self.assertEqual(received_token, token)
        return payload, key

    def assert_failure_callback(self, image):
        token, body = self.request_body(image)
        job_id = self.submit(body)
        payload, key = self.receive(token)
        self.assertEqual(payload["job_id"], job_id)
        self.assertEqual(payload["method"], "pytesseract")
        self.assertEqual(payload["status"], "failed")
        self.assertIsNone(payload["result"])
        self.assertIsInstance(payload["error"], str)
        self.assertTrue(payload["error"].strip())
        self.assertEqual(key, job_id)

    def test_health_reports_idle_capacity(self):
        code, body, _ = http_json(self.api_url + "/health")
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["active_jobs"], 0)
        self.assertEqual(body["max_concurrent_requests"], 10)

    def test_required_fields_are_validated(self):
        for field in ("image_url", "method", "callback_url"):
            with self.subTest(field=field):
                _, body = self.request_body()
                del body[field]
                self.assertEqual(http_json(self.api_url + "/infer", body)[0], 422)

    def test_invalid_methods_are_rejected(self):
        for method in ("unknown", "YOLOV8", "", None, 42):
            with self.subTest(method=method):
                _, body = self.request_body(method=method)
                self.assertEqual(http_json(self.api_url + "/infer", body)[0], 422)

    def test_urls_require_http_or_https(self):
        for field in ("image_url", "callback_url"):
            for value in ("not-a-url", "file:///tmp/image.png", "ftp://example.com/a", None):
                with self.subTest(field=field, value=value):
                    _, body = self.request_body()
                    body[field] = value
                    self.assertEqual(http_json(self.api_url + "/infer", body)[0], 422)

    def test_unknown_fields_are_rejected(self):
        _, body = self.request_body()
        body["unexpected"] = True
        self.assertEqual(http_json(self.api_url + "/infer", body)[0], 422)

    def test_acceptance_does_not_wait_for_image_download(self):
        token, body = self.request_body()
        gate = threading.Event()
        self.fixture.gates[token] = gate
        body["image_url"] = self.fixture_url + "/hold/" + token
        self.submit(body)  # HTTP client timeout fails a synchronous implementation.
        self.assertFalse(gate.is_set())
        self.assertEqual(http_json(self.api_url + "/health")[1]["active_jobs"], 1)
        gate.set()
        self.receive(token)

    def test_job_ids_are_unique_and_correlate_with_callbacks(self):
        jobs = {}
        for _ in range(3):
            token, body = self.request_body("/missing")
            jobs[token] = self.submit(body)
        self.assertEqual(len(set(jobs.values())), 3)
        for _ in range(3):
            token, payload, key = self.fixture.callbacks.get(timeout=WAIT_SECONDS)
            self.assertEqual(payload["job_id"], jobs.pop(token))
            self.assertEqual(key, payload["job_id"])
        self.assertFalse(jobs)

    def test_real_ocr_recognizes_known_text(self):
        token, body = self.request_body()
        job_id = self.submit(body)
        payload, key = self.receive(token)
        self.assertEqual(payload["status"], "completed", payload)
        self.assertEqual(payload["job_id"], job_id)
        self.assertEqual(key, job_id)
        self.assertIsNone(payload["error"])
        self.assertEqual(payload["method"], "pytesseract")
        self.assertEqual(payload["result"]["type"], "pytesseract")
        recognized = " ".join(payload["result"]["text"].split()).upper()
        self.assertIn("HELLO WORLD 123", recognized)

    def test_missing_image_produces_failure_callback(self):
        self.assert_failure_callback("/missing")

    def test_corrupt_image_produces_failure_callback(self):
        self.assert_failure_callback("/image/corrupt")

    def test_oversized_image_produces_failure_callback(self):
        self.assert_failure_callback("/image/oversize")

    def test_redirect_is_not_followed(self):
        self.assert_failure_callback("/image/redirect")

    def test_failed_callbacks_retry_same_payload_and_idempotency_key(self):
        token, body = self.request_body("/missing")
        self.fixture.failures[token] = 2
        job_id = self.submit(body)
        attempts = [self.receive(token) for _ in range(3)]
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(attempts[1], attempts[2])
        self.assertEqual(attempts[0][0]["job_id"], job_id)
        self.assertEqual(attempts[0][1], job_id)
        wait_until(lambda: http_json(self.api_url + "/health")[1]["active_jobs"] == 0)
        self.assertTrue(self.fixture.callbacks.empty(), "Unexpected extra callback attempt")

    def test_callback_exhaustion_releases_capacity(self):
        token, body = self.request_body("/missing")
        self.fixture.failures[token] = 100
        self.submit(body)
        for _ in range(3):
            self.receive(token)
        wait_until(lambda: http_json(self.api_url + "/health")[1]["active_jobs"] == 0)
        self.assertTrue(self.fixture.callbacks.empty(), "More than three attempts were made")

    def test_ten_active_jobs_reject_eleventh_and_capacity_recovers(self):
        jobs = {}
        for _ in range(10):
            token, body = self.request_body()
            self.fixture.gates[token] = threading.Event()
            body["image_url"] = self.fixture_url + "/hold/" + token
            jobs[token] = self.submit(body)
        self.assertEqual(http_json(self.api_url + "/health")[1]["active_jobs"], 10)
        _, rejected = self.request_body("/missing")
        code, _, headers = http_json(self.api_url + "/infer", rejected)
        self.assertEqual(code, 429)
        self.assertGreaterEqual(int(headers["Retry-After"]), 1)
        self.fixture.release_all()
        for _ in range(10):
            token, payload, _ = self.fixture.callbacks.get(timeout=WAIT_SECONDS)
            self.assertEqual(payload["job_id"], jobs.pop(token))
        wait_until(lambda: http_json(self.api_url + "/health")[1]["active_jobs"] == 0)
        token, body = self.request_body("/missing")
        self.submit(body)
        self.receive(token)

    @unittest.skipUnless(
        os.environ.get("RUN_YOLO_TESTS") == "1", "Enable with utils.py test --yolo"
    )
    def test_real_yolo_returns_valid_detections(self):
        token, body = self.request_body("/image/blank", method="yolov8")
        job_id = self.submit(body)
        payload, key = self.receive(token)
        self.assertEqual(payload["status"], "completed", payload)
        self.assertEqual(payload["job_id"], job_id)
        self.assertEqual(key, job_id)
        self.assertEqual(payload["method"], "yolov8")
        self.assertIsNone(payload["error"])
        result = payload["result"]
        self.assertEqual(result["type"], "yolov8")
        self.assertIsInstance(result["detections"], list)
        for detection in result["detections"]:
            self.assertIsInstance(detection["class_id"], int)
            self.assertIsInstance(detection["label"], str)
            self.assertGreaterEqual(detection["confidence"], 0)
            self.assertLessEqual(detection["confidence"], 1)
            x1, y1, x2, y2 = detection["bbox"]
            self.assertTrue(0 <= x1 <= x2 <= 320)
            self.assertTrue(0 <= y1 <= y2 <= 320)


if __name__ == "__main__":
    unittest.main()
