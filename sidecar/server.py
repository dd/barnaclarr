"""Authenticated webhook ingestion with a durable retry queue and one worker."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import hmac
import json
import logging
import threading

from .engine import Engine
from .sonarr import Sonarr
from .state import State

LOG = logging.getLogger(__name__)


def handler_class(config):
    token = config.webhook_token_file.read_text().strip()
    if len(token) < 32:
        raise ValueError("Webhook token must contain at least 32 characters")

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, fmt, *args):
            # Default logging includes URLs; do not log request headers or tokens.
            LOG.info("http %s", args[1] if len(args) > 1 else "request")

        def respond(self, status, data):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.respond(
                200 if self.path == "/health" else 404,
                {"status": "ok"} if self.path == "/health" else {"error": "not found"},
            )

        def do_POST(self):
            if self.path != "/webhook/sonarr":
                self.respond(404, {"error": "not found"})
                return
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
                self.respond(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024 * 1024:
                    self.respond(413, {"error": "body must be between 1 byte and 1 MiB"})
                    return
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict) or not isinstance(payload.get("eventType"), str):
                    raise ValueError("Invalid event")
                if payload["eventType"] == "Test":
                    self.respond(200, {"status": "ok"})
                    return
                if payload["eventType"] not in {"Download", "Rename"}:
                    self.respond(200, {"status": "ignored"})
                    return
                if not isinstance(payload.get("series"), dict) or not isinstance(
                    payload["series"].get("id"), int
                ):
                    raise ValueError("Missing series id")
                state = State(config.database)
                try:
                    job_id = state.enqueue(payload)
                finally:
                    state.close()
                self.respond(202, {"job_id": job_id})
            except (ValueError, KeyError, UnicodeDecodeError):
                self.respond(400, {"error": "invalid JSON event"})

    return Handler


def worker(config, stop: threading.Event):
    state = State(config.database)
    try:
        state.recover()
        engine = Engine(config, Sonarr(config), state)
        while not stop.is_set():
            job = state.next_job()
            if job is None:
                stop.wait(1)
                continue
            try:
                engine.process(json.loads(job["payload"]))
            except Exception as exc:
                # This is the job isolation boundary: preserve failed work and retry.
                # Avoid exception strings containing credential-bearing request URLs.
                error = type(exc).__name__
                LOG.error("job=%s failure=%s; inspect/replay event for diagnosis", job["id"], error)
                state.finish(job, error)
            else:
                state.finish(job)
    finally:
        state.close()


def serve(config):
    stop = threading.Event()
    thread = threading.Thread(target=worker, args=(config, stop), daemon=True)
    server = HTTPServer(("0.0.0.0", config.port), handler_class(config))
    thread.start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        stop.set()
        server.server_close()
        thread.join(timeout=35)
