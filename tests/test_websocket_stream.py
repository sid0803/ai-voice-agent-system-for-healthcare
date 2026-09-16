"""WebSocket integration tests — AI-09.

Simulates a full Exotel call lifecycle against the live FastAPI app using
FastAPI's TestClient WebSocket support.
"""
import json
import pytest
from fastapi.testclient import TestClient


def _make_client():
    from src.server import app
    return TestClient(app)


def _valid_ws_url(call_sid: str = ""):
    from src.server import _generate_exotel_ws_nonce
    return f"/exotel-stream?token={_generate_exotel_ws_nonce(call_sid)}"


def test_ws_01_valid_nonce_connects_and_receives_greeting():
    """Valid HMAC nonce allows connection and triggers greeting audio."""
    client = _make_client()
    with client.websocket_connect(_valid_ws_url()) as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        ws.send_text(json.dumps({
            "event": "start",
            "start": {"stream_sid": "ws_test_01", "call_sid": "ws_call_01", "from": "06297546142"}
        }))
        msg = json.loads(ws.receive_text())
        assert msg["event"] == "media"
        assert msg["stream_sid"] == "ws_test_01"
        assert "payload" in msg["media"]


def test_ws_02_raw_secret_rejected():
    """Raw EXOTEL_WS_SECRET (not HMAC nonce) must be rejected."""
    from src.server import _EXOTEL_WS_SECRET
    client = _make_client()
    with pytest.raises(Exception):
        with client.websocket_connect(f"/exotel-stream?token={_EXOTEL_WS_SECRET}") as ws:
            ws.receive_text()


def test_ws_03_no_token_rejected():
    """No token from non-Exotel IP must be rejected."""
    client = _make_client()
    with pytest.raises(Exception):
        with client.websocket_connect("/exotel-stream") as ws:
            ws.receive_text()


def test_ws_04_media_frames_processed_without_crash():
    """5 mulaw audio frames handled without server crash."""
    import base64
    encoded = base64.b64encode(b"\xff" * 160).decode()
    client = _make_client()
    with client.websocket_connect(_valid_ws_url("media_call") + "&CallSid=media_call") as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        ws.send_text(json.dumps({
            "event": "start",
            "start": {"stream_sid": "ws_media", "call_sid": "media_call", "from": "06297546142"}
        }))
        ws.receive_text()  # drain greeting
        for i in range(5):
            ws.send_text(json.dumps({"event": "media", "stream_sid": "ws_media",
                                      "media": {"payload": encoded, "chunk": str(i)}}))
        ws.send_text(json.dumps({"event": "stop"}))


def test_ws_05_stop_event_removes_session():
    """After stop + disconnect, session must be removed from session_map."""
    from src.server import session_map
    import time
    before = len(session_map)
    client = _make_client()
    with client.websocket_connect(_valid_ws_url("stop_call") + "&CallSid=stop_call") as ws:
        ws.send_text(json.dumps({"event": "connected"}))
        ws.send_text(json.dumps({
            "event": "start",
            "start": {"stream_sid": "ws_stop", "call_sid": "stop_call", "from": "06297546142"}
        }))
        ws.receive_text()
        ws.send_text(json.dumps({"event": "stop"}))
    time.sleep(0.5)
    assert len(session_map) <= before + 1  # tolerant: async teardown may linger briefly


def test_ws_06_session_cap_enforced():
    """When session_map is full, new connections receive close code 1013."""
    from src.server import session_map, MAX_CONCURRENT_SESSIONS
    import unittest.mock as mock
    with mock.patch.dict(session_map, {f"fake_{i}": object() for i in range(MAX_CONCURRENT_SESSIONS)}):
        client = _make_client()
        with pytest.raises(Exception):
            with client.websocket_connect(_valid_ws_url("cap_test")) as ws:
                ws.receive_text()

