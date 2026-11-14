"""Cliente del laboratorio local. No envía mensajes a clientes."""
import argparse
import json
import os
import uuid
from urllib.request import Request, urlopen
from app import load_env, now

load_env()
p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--lead", required=True)
p.add_argument("--text", required=True)
p.add_argument("--port", type=int, default=8080)
a = p.parse_args()
token = os.environ.get("API_TOKEN", "")
if len(token) < 32:
    raise SystemExit("Configura API_TOKEN en .env")
event = {"event_id": str(uuid.uuid4()), "lead_id": a.lead, "text": a.text, "occurred_at": now()}
req = Request(f"http://127.0.0.1:{a.port}/events", data=json.dumps(event).encode(), method="POST",
              headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
with urlopen(req, timeout=150) as response:
    print(json.dumps(json.load(response), ensure_ascii=False, indent=2))
