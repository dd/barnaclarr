from io import BytesIO
import json

import pytest

from sidecar.config import Config
from sidecar.server import describe_error, handler_class
from sidecar.state import State


class FakeSocket:
    """Exercise HTTP parsing in memory, without opening sockets or making requests."""

    def __init__(self, content):
        self.input = BytesIO(content)
        self.output = b""

    def makefile(self, mode, *args):
        return self.input

    def sendall(self, data):
        self.output += data

    def settimeout(self, timeout):
        pass


def request(tmp_path, payload, authorization="Bearer " + "x" * 32, length=None):
    token = tmp_path / "token"
    token.write_text("x" * 32)
    config = Config(
        [tmp_path / "downloads"],
        [tmp_path / "library"],
        tmp_path / "fonts",
        tmp_path / "db",
        webhook_token_file=token,
    )
    body = json.dumps(payload).encode()
    headers = f"POST /webhook/sonarr HTTP/1.0\r\nAuthorization: {authorization}\r\nContent-Length: {length if length is not None else len(body)}\r\n\r\n".encode()
    sock = FakeSocket(headers + body)
    handler_class(config)(sock, ("127.0.0.1", 0), None)
    return config, sock.output


def test_authentication_before_queue(tmp_path):
    """Unauthorized events receive 401 and never create the database."""
    config, output = request(tmp_path, {"eventType": "Test"}, authorization="wrong")
    assert b"401" in output
    assert not config.database.exists()


def test_test_event_no_database(tmp_path):
    """Sonarr's Test webhook succeeds without paths or filesystem mutation."""
    config, output = request(tmp_path, {"eventType": "Test"})
    assert b"200" in output
    assert not config.database.exists()


def test_event_persisted_before_accepted(tmp_path):
    """202 means the import event has already been committed to SQLite."""
    config, output = request(tmp_path, {"eventType": "Rename", "series": {"id": 1}})
    assert b"202" in output
    state = State(config.database)
    assert json.loads(state.next_job()["payload"])["series"]["id"] == 1
    state.close()


@pytest.mark.parametrize(
    "payload,length,status",
    [
        ([], None, b"400"),
        ({"eventType": "Download"}, None, b"400"),
        ({"eventType": "Test"}, 1024 * 1024 + 1, b"413"),
    ],
)
def test_invalid_request(tmp_path, payload, length, status):
    """Malformed event envelopes and oversized bodies fail before persistence."""
    config, output = request(tmp_path, payload, length=length)
    assert status in output
    assert not config.database.exists()


def test_failed_job_retry_and_recovery(tmp_path):
    """Interrupted jobs recover, and retry exhaustion retains a failed event."""
    state = State(tmp_path / "db")
    state.enqueue({"eventType": "Rename"})
    job = state.next_job()
    state.recover()
    assert state.next_job()["id"] == job["id"]
    job["attempts"] = 5
    state.finish(job, "PermissionError")
    assert state.next_job() is None
    row = state.db.execute("SELECT status,error FROM jobs").fetchone()
    assert tuple(row) == ("failed", "PermissionError")
    state.close()


def test_job_error_keeps_message_and_masks_url_credentials():
    """Failed jobs say what went wrong without leaking URL credentials."""
    from urllib.error import URLError

    from sidecar.config import UnsafePath

    error = describe_error(UnsafePath("Outside allowed roots: /mnt/media/x"))
    assert error == "UnsafePath: Outside allowed roots: /mnt/media/x"
    masked = describe_error(URLError("http://user:secret@sonarr:8989/api refused"))
    assert "secret" not in masked and "***@sonarr" in masked
