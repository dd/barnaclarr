"""Authenticated webhook ingestion with a durable retry queue and one worker."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import hmac
import json
import logging
import re
import threading

from .engine import Engine, event_app, event_subject
from .sonarr import Radarr, Sonarr
from .state import State

LOG = logging.getLogger(__name__)


def handler_class(config):
    token = config.token()
    if len(token) < 32:
        raise ValueError("Webhook token must contain at least 32 characters")

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, fmt, *args):
            # Access log, mostly health checks every 30 s. Meaningful events are
            # logged below; never log request headers or tokens.
            LOG.debug("http %s %s", self.path, args[1] if len(args) > 1 else "")

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
            app = {"/webhook/sonarr": "sonarr", "/webhook/radarr": "radarr"}.get(self.path)
            if app is None:
                self.respond(404, {"error": "not found"})
                return
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
                LOG.warning("[%s] webhook rejected: missing or wrong Authorization header", app)
                self.respond(401, {"error": "unauthorized"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024 * 1024:
                    LOG.warning("[%s] webhook rejected: body of %s bytes", app, length)
                    self.respond(413, {"error": "body must be between 1 byte and 1 MiB"})
                    return
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict) or not isinstance(payload.get("eventType"), str):
                    raise ValueError("Invalid event")
                if payload["eventType"] == "Test":
                    LOG.info("[%s] webhook Test: ok", app)
                    self.respond(200, {"status": "ok"})
                    return
                if payload["eventType"] not in {"Download", "Rename"}:
                    LOG.info("[%s] webhook %s ignored", app, payload["eventType"])
                    self.respond(200, {"status": "ignored"})
                    return
                subject = "movie" if app == "radarr" else "series"
                if not isinstance(payload.get(subject), dict) or not isinstance(
                    payload[subject].get("id"), int
                ):
                    raise ValueError(f"Missing {subject} id")
                payload["_app"] = app
                state = State(config.database)
                try:
                    job_id = state.enqueue(payload)
                finally:
                    state.close()
                LOG.info("[%s] job=%s queued: %s", app, job_id, event_subject(payload))
                self.respond(202, {"job_id": job_id})
            except (ValueError, KeyError, UnicodeDecodeError):
                LOG.warning("[%s] webhook rejected: not a valid event", app)
                self.respond(400, {"error": "invalid JSON event"})

    return Handler


def describe_error(exc: Exception) -> str:
    """Type and message for logs and the jobs table, with URL credentials masked.

    Messages carry paths and HTTP statuses; the API key travels in a header and
    never appears in them, but a Sonarr/Radarr URL may embed user:password.
    """
    message = re.sub(r"(\w+://)[^/@\s]+@", r"\1***@", str(exc))
    return f"{type(exc).__name__}: {message}"[:1000] if message else type(exc).__name__


def worker(config, stop: threading.Event):
    state = State(config.database)
    try:
        state.recover()
        engine = Engine(
            config,
            Sonarr(config) if config.sonarr_url else None,
            state,
            Radarr(config) if config.radarr_url else None,
        )
        while not stop.is_set():
            job = state.next_job()
            if job is None:
                stop.wait(1)
                continue
            payload = json.loads(job["payload"])
            label = f"[{event_app(payload)}] job={job['id']}"
            try:
                engine.process(payload, label)
            except Exception as exc:
                # This is the job isolation boundary: preserve failed work and retry.
                error = describe_error(exc)
                LOG.error("%s attempt=%s failed: %s", label, job["attempts"] + 1, error)
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
