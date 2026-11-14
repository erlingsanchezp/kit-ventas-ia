"""Prueba de contrato HTTP real en localhost sin invocar proveedores."""
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from app import Engine, now

with tempfile.TemporaryDirectory() as temp:
    path = Path(temp) / "http.sqlite3"
    engine = Engine(path)
    engine.register("HTTP1", "Prueba", "http@example.com", True, "Permiso ficticio")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    token = secrets.token_urlsafe(32)
    env = {**os.environ, "API_TOKEN": token, "API_MODE": "demo", "DELIVER_OUTBOX": "0"}
    proc = subprocess.Popen([sys.executable, str(Path(__file__).with_name("app.py")), "serve",
                             "--db", str(path), "--port", str(port)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = "http://127.0.0.1:" + str(port)
    def request(route, data=None, auth=True):
        headers = {"Authorization": "Bearer " + token} if auth else {}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = Request(base + route, data=None if data is None else json.dumps(data).encode(), headers=headers)
        with urlopen(req, timeout=5) as response:
            return json.load(response)
    try:
        for _ in range(60):
            try:
                request("/health", auth=False)
                break
            except (URLError, ConnectionError):
                time.sleep(0.05)
        assert request("/health", auth=False) == {"ok": True}
        try:
            request("/metrics", auth=False)
            raise AssertionError("missing authentication")
        except HTTPError as error:
            assert error.code == 401
        event = {"event_id": "HTTP-E1", "lead_id": "HTTP1", "text": "propuesta AUDIT-150", "occurred_at": now()}
        first = request("/events", event)
        assert first["action"] == "quote"
        second = request("/events", event)
        assert second["duplicate"] and first["proposal_id"] == second["proposal_id"]
        assert request("/outbox/claim", {})["item"] is None
        metrics = request("/metrics")
        assert metrics["proposals"] == 1 and metrics["sales_paid"] == 0
        print("HTTP local: 6 comprobaciones correctas; sin llamadas externas")
    finally:
        proc.terminate()
        proc.wait(timeout=5)
