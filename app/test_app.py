import io
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import Engine, EXTRACT, api_model, demo_model, now


class SalesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test.sqlite3"
        self.engine = Engine(self.path, deliver=True)
        self.engine.register("L1", "Persona ficticia", "test@example.com", True, "Permiso de prueba")
        self.engine.register("L2", "Otra persona", "other@example.com", True, "Permiso de prueba")
        self.engine.add_slot("SLOT-A", (datetime.now(timezone.utc) + timedelta(days=3)).isoformat())

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, text, eid="E1", lid="L1", stamp=None):
        return {"event_id": eid, "lead_id": lid, "text": text, "occurred_at": stamp or now()}

    def test_catalog_price(self):
        result = self.engine.handle(self.event("precio AUDIT-150"))
        self.assertIn("150.00", result["reply"])
        self.assertEqual(result["action"], "explain")

    def test_quote_has_catalog_amount(self):
        self.engine.handle(self.event("propuesta AUDIT-150"))
        with self.engine.connect() as db:
            row = db.execute("SELECT * FROM proposals").fetchone()
        self.assertEqual(row["amount_cents"], 15000)
        self.assertEqual(row["state"], "issued")

    def test_duplicate_reuses_result(self):
        event = self.event("propuesta AUDIT-150")
        first, second = self.engine.handle(event), self.engine.handle(event)
        self.assertEqual(first["proposal_id"], second["proposal_id"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(self.engine.metrics()["proposals"], 1)

    def test_id_reuse_with_changed_body_rejected(self):
        self.engine.handle(self.event("precio"))
        with self.assertRaises(ValueError):
            self.engine.handle(self.event("propuesta AUDIT-150"))

    def test_explicit_stop_cancels_pending(self):
        self.engine.handle(self.event("precio"))
        self.engine.handle(self.event("BAJA", "E2"))
        self.assertIsNone(self.engine.claim()["item"])
        result = self.engine.handle(self.event("precio", "E3"))
        self.assertEqual(result["action"], "suppressed")
        self.assertEqual(result["reply"], "")

    def test_stop_works_without_model(self):
        def broken(*args):
            raise RuntimeError("unavailable")
        self.engine.model = broken
        self.assertEqual(self.engine.handle(self.event("no me escriban"))["action"], "stop")

    def test_model_error_pauses_without_side_effect(self):
        def broken(*args):
            raise TimeoutError()
        self.engine.model = broken
        self.assertEqual(self.engine.handle(self.event("propuesta AUDIT-150"))["action"], "pause")
        self.assertEqual(self.engine.metrics()["proposals"], 0)

    def test_discount_paused(self):
        result = self.engine.handle(self.event("quiero descuento en AUDIT-150"))
        self.assertEqual(result["action"], "pause")
        self.assertEqual(self.engine.metrics()["proposals"], 0)

    def test_booking_requires_exact_confirmation(self):
        result = self.engine.handle(self.event("quizás reservar SLOT-A"))
        self.assertEqual(result["action"], "ask")
        self.assertEqual(self.engine.metrics()["bookings_local"], 0)

    def test_booking_confirmed(self):
        result = self.engine.handle(self.event("RESERVAR SLOT-A"))
        self.assertEqual(result["action"], "book")
        self.assertEqual(self.engine.metrics()["bookings_local"], 1)

    def test_two_engines_cannot_double_book(self):
        other = Engine(self.path, deliver=True)
        with ThreadPoolExecutor(2) as pool:
            a = pool.submit(self.engine.handle, self.event("RESERVAR SLOT-A", "E1", "L1"))
            b = pool.submit(other.handle, self.event("RESERVAR SLOT-A", "E2", "L2"))
        self.assertEqual(sorted([a.result()["action"], b.result()["action"]]), ["ask", "book"])
        self.assertEqual(self.engine.metrics()["bookings_local"], 1)

    def test_old_event_does_not_advance(self):
        self.engine.handle(self.event("precio"))
        old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        result = self.engine.handle(self.event("propuesta AUDIT-150", "E2", stamp=old))
        self.assertEqual(result["action"], "stale")

    def test_old_stop_still_suppresses(self):
        self.engine.handle(self.event("precio"))
        old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        result = self.engine.handle(self.event("BAJA", "E2", stamp=old))
        self.assertEqual(result["action"], "stop")
        self.assertIsNone(self.engine.claim()["item"])

    def test_unverified_consent_not_dispatched(self):
        self.engine.register("L3", "Sin permiso", "none@example.com")
        self.engine.handle(self.event("precio", lid="L3"))
        self.assertIsNone(self.engine.claim()["item"])

    def test_consent_requires_evidence(self):
        with self.assertRaises(ValueError):
            self.engine.register("L3", "Sin evidencia", "none@example.com", True, "")

    def test_claim_only_once(self):
        self.engine.handle(self.event("precio"))
        first = self.engine.claim()["item"]
        self.assertIsNotNone(first)
        self.assertIsNone(self.engine.claim()["item"])
        self.assertEqual(first["email"], "test@example.com")

    def test_new_reply_cancels_stale_outbox(self):
        first = self.engine.handle(self.event("precio"))
        second = self.engine.handle(self.event("propuesta AUDIT-150", "E2"))
        self.assertEqual(self.engine.claim()["item"]["outbox_id"], second["outbox_id"])
        with self.engine.connect() as db:
            status = db.execute("SELECT status FROM outbox WHERE id=?", (first["outbox_id"],)).fetchone()[0]
        self.assertEqual(status, "cancelled")

    def test_delivery_ack_is_idempotent(self):
        self.engine.handle(self.event("precio"))
        oid = self.engine.claim()["item"]["outbox_id"]
        self.engine.record_delivery(oid, "accepted", "provider-123")
        result = self.engine.record_delivery(oid, "accepted", "provider-123")
        self.assertTrue(result["duplicate"])

    def test_uncertain_not_automatically_retried(self):
        self.engine.handle(self.event("precio"))
        oid = self.engine.claim()["item"]["outbox_id"]
        self.engine.record_delivery(oid, "uncertain")
        self.assertIsNone(self.engine.claim()["item"])

    def test_preview_never_claimed(self):
        engine = Engine(self.path, deliver=False)
        engine.handle(self.event("precio"))
        self.assertIsNone(engine.claim()["item"])

    def test_auditor_veto_before_booking(self):
        def veto(role, data, schema):
            if role == "auditor":
                return {"approved": False, "reason": "Prueba"}
            return demo_model(role, data, schema)
        self.engine.model = veto
        result = self.engine.handle(self.event("RESERVAR SLOT-A"))
        self.assertEqual(result["action"], "pause")
        self.assertEqual(self.engine.metrics()["bookings_local"], 0)

    def test_model_cannot_choose_unpermitted_action(self):
        def unsafe(role, data, schema):
            if role == "consultor":
                return {"action": "quote", "reason": "Inventado"}
            return demo_model(role, data, schema)
        self.engine.model = unsafe
        self.assertEqual(self.engine.handle(self.event("precio AUDIT-150"))["action"], "pause")

    def test_extra_model_fields_rejected(self):
        def bad(role, data, schema):
            return {**demo_model(role, data, schema), "consent": True}
        self.engine.model = bad
        self.assertEqual(self.engine.handle(self.event("precio"))["action"], "pause")

    def test_unknown_lead_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.handle(self.event("precio", lid="missing"))

    def test_client_cannot_set_recipient(self):
        value = self.event("precio")
        value["email"] = "attacker@example.com"
        with self.assertRaises(ValueError):
            self.engine.handle(value)

    def test_paid_is_never_claimed(self):
        self.engine.handle(self.event("ya pagué USD 150"))
        self.assertEqual(self.engine.metrics()["sales_paid"], 0)


class AdapterTests(unittest.TestCase):
    @patch.dict(os.environ, {"OPENAI_API_KEY": "fake", "OPENAI_MODEL": "model-for-test"})
    def test_response_shape_and_request(self):
        answer = {"intent": "price", "sku": "AUDIT-150", "slot_id": "", "evidence": "precio"}
        payload = {"status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer)}]}]}
        with patch("app.urlopen", return_value=io.BytesIO(json.dumps(payload).encode())) as call:
            result = api_model("calificador", {"message": "precio AUDIT-150"}, EXTRACT)
        sent = json.loads(call.call_args.args[0].data)
        self.assertEqual(result, answer)
        self.assertFalse(sent["store"])
        self.assertTrue(sent["text"]["format"]["strict"])
        self.assertEqual(sent["model"], "model-for-test")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fake", "OPENAI_MODEL": "model-for-test"})
    def test_incomplete_rejected(self):
        payload = {"status": "incomplete", "output": []}
        with patch("app.urlopen", return_value=io.BytesIO(json.dumps(payload).encode())):
            with self.assertRaises(RuntimeError):
                api_model("calificador", {}, EXTRACT)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "fake", "OPENAI_MODEL": "model-for-test"})
    def test_refusal_rejected(self):
        payload = {"status": "completed", "output": [{"content": [{"type": "refusal", "refusal": "no"}]}]}
        with patch("app.urlopen", return_value=io.BytesIO(json.dumps(payload).encode())):
            with self.assertRaises(RuntimeError):
                api_model("calificador", {}, EXTRACT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
