"""Tests added after mutation testing showed these behaviours were unprotected."""
import copy, json, os, pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db"); os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["INVESTIGATE_PER_MIN"] = "100000"
from fastapi.testclient import TestClient
from app.main import app
from app import agent, main
from app.engine import recalc

client = TestClient(app)
CASE = json.loads((pathlib.Path(__file__).resolve().parent.parent / "data" / "sample_case.json").read_text())
def mk(c=None): return client.post("/api/cases", json={"payload": c or CASE}).json()["id"]
def inv(cid): return client.post(f"/api/cases/{cid}/investigate").json()

def test_usage_below_included_allowance_costs_zero_never_negative():
    c = copy.deepcopy(CASE)
    c["usage"] = [{"id": "A", "metric": "api_calls", "quantity": "50000", "date": "2026-09-02"}] + [u for u in c["usage"] if u["metric"] != "api_calls"]
    l1 = recalc(c)["lines"][0]
    assert l1["correct_amount"] == "0.00" and l1["correct_qty"] == "50000"      # 100k included, only 50k used

def test_crediting_exactly_the_cap_is_allowed_and_one_cent_more_is_not():
    cid = mk(); inv(cid)
    assert client.post(f"/api/cases/{cid}/credit", json={"amount": "499.99", "reason": "almost all", "idempotency_key": "cap-ex-001"}).status_code == 200
    assert client.post(f"/api/cases/{cid}/credit", json={"amount": "0.01", "reason": "exactly the rest", "idempotency_key": "cap-ex-002"}).status_code == 200
    assert client.post(f"/api/cases/{cid}/credit", json={"amount": "0.01", "reason": "one cent over", "idempotency_key": "cap-ex-003"}).status_code == 409

def test_finding_with_one_valid_and_one_invented_citation_is_rejected():
    ok, rej = agent.verify([{"citations": ["INV-2026-09-0042", "U999"]}, {"citations": ["L1", "R1"]}], agent.valid_ids(CASE))
    assert len(ok) == 1 and ok[0]["citations"] == ["L1", "R1"] and len(rej) == 1

def test_rerun_replaces_unreviewed_findings_from_a_different_interpreter(monkeypatch):
    monkeypatch.setattr(agent, "llm_findings", lambda c, k: [{"kind": "other", "statement": "LLM-only wording", "citations": ["L1"], "source": "llm"}])
    cid = mk(); inv(cid)
    assert [f["source"] for f in client.get(f"/api/cases/{cid}").json()["findings"]] == ["llm"]
    monkeypatch.setattr(agent, "llm_findings", lambda c, k: (_ for _ in ()).throw(RuntimeError("down")))
    inv(cid); fs = client.get(f"/api/cases/{cid}").json()["findings"]
    assert "llm" not in [f["source"] for f in fs] and len(fs) == 4

def test_reviewed_findings_survive_a_rerun_and_are_not_duplicated():
    cid = mk(); inv(cid); f = client.get(f"/api/cases/{cid}").json()["findings"][0]
    client.post(f"/api/cases/{cid}/findings/{f['id']}", json={"status": "accepted"})
    inv(cid); fs = client.get(f"/api/cases/{cid}").json()["findings"]
    assert len(fs) == 4 and [x["status"] for x in fs if x["id"] == f["id"]] == ["accepted"]

def test_rate_limit_exact_boundary(monkeypatch):
    monkeypatch.setenv("INVESTIGATE_PER_MIN", "3"); main._hits.clear()
    cid = mk(); codes = [client.post(f"/api/cases/{cid}/investigate").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]; main._hits.clear()

def test_per_unit_usage_below_allowance_is_zero_not_negative():
    c = copy.deepcopy(CASE)
    c["rules"][2] = {"id": "R3", "type": "per_unit", "rate": "0.80", "included": 1000}
    c["usage"] = [u for u in c["usage"] if u["metric"] != "storage_gb"] + [{"id": "S1", "metric": "storage_gb", "quantity": "400", "date": "2026-09-30"}]
    l3 = recalc(c)["lines"][2]
    assert l3["correct_amount"] == "0.00" and l3["delta"] == "400.00"
    c["usage"][-1]["quantity"] = "1500"
    assert recalc(c)["lines"][2]["correct_amount"] == "400.00"                 # (1500-1000) * 0.80
