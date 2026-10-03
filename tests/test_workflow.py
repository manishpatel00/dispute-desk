import json, os, pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")
os.environ.pop("ANTHROPIC_API_KEY", None)
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import agent

client = TestClient(app)
CASE = json.loads((pathlib.Path(__file__).resolve().parent.parent / "data" / "sample_case.json").read_text())

def new_case():
    return client.post("/api/cases", json={"payload": CASE}).json()["id"]

def test_rejects_invalid_payload():
    assert client.post("/api/cases", json={"payload": {"invoice": {}}}).status_code == 422

def test_investigation_falls_back_without_llm_and_cites_real_ids():
    cid = new_case(); r = client.post(f"/api/cases/{cid}/investigate").json()
    assert r["ok"] and not r["llm_used"]
    ids = agent.valid_ids(CASE)
    for f in client.get(f"/api/cases/{cid}").json()["findings"]:
        assert all(c in ids for c in f["citations"])

def test_ungrounded_llm_findings_are_dropped():
    ok, rej = agent.verify([{"citations": ["INV-2026-09-0042"]}, {"citations": ["U999"]}, {"citations": []}], agent.valid_ids(CASE))
    assert len(ok) == 1 and len(rej) == 2

def test_duplicate_credit_is_idempotent_and_cap_enforced():
    cid = new_case(); client.post(f"/api/cases/{cid}/investigate")
    body = {"amount": "300.00", "reason": "duplicate usage", "idempotency_key": "key-0001"}
    a = client.post(f"/api/cases/{cid}/credit", json=body).json()
    b = client.post(f"/api/cases/{cid}/credit", json=body).json()
    assert a["status"] == "approved" and b["status"] == "duplicate_ignored" and a["adjustment_id"] == b["adjustment_id"]
    over = client.post(f"/api/cases/{cid}/credit", json={**body, "idempotency_key": "key-0002"})
    assert over.status_code == 409
    assert len(client.get(f"/api/cases/{cid}").json()["adjustments"]) == 1

def test_new_evidence_reopens_and_marks_findings_stale():
    cid = new_case(); client.post(f"/api/cases/{cid}/investigate")
    assert not any(f["stale"] for f in client.get(f"/api/cases/{cid}").json()["findings"])
    client.post(f"/api/cases/{cid}/evidence", json={"patch": {"usage": [{"id": "U8", "metric": "api_calls", "quantity": "5", "date": "2026-09-03"}]}, "note": "customer export"})
    d = client.get(f"/api/cases/{cid}").json()
    assert d["case"]["status"] == "reopened" and d["calc_stale"] and all(f["stale"] for f in d["findings"])

def test_calculation_failure_is_reported_not_guessed(monkeypatch):
    cid = new_case()
    monkeypatch.setattr(agent, "recalc", lambda c: (_ for _ in ()).throw(RuntimeError("pricing service down")))
    r = client.post(f"/api/cases/{cid}/investigate").json()
    assert r["ok"] is False and r["stage"] == "calculate"

def test_decision_history_preserved():
    cid = new_case(); client.post(f"/api/cases/{cid}/investigate")
    f = client.get(f"/api/cases/{cid}").json()["findings"][0]
    client.post(f"/api/cases/{cid}/findings/{f['id']}", json={"status": "accepted"})
    actions = [d["action"] for d in client.get(f"/api/cases/{cid}").json()["decisions"]]
    assert actions[0] == "case_created" and "finding_accepted" in actions
