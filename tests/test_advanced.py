import copy, json, os, pathlib, random, sys, tempfile, threading
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")
os.environ.pop("ANTHROPIC_API_KEY", None)
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app import agent, store
from app.engine import recalc, validate_case, _tier_price, D, money

client = TestClient(app)
CASE = json.loads((pathlib.Path(__file__).resolve().parent.parent / "data" / "sample_case.json").read_text())

def new_case(payload=None):
    return client.post("/api/cases", json={"payload": payload or CASE}).json()["id"]

def invest(cid): return client.post(f"/api/cases/{cid}/investigate").json()

# ---------- engine: edge cases & properties ----------
def test_tier_boundaries_exact():
    t = [{"up_to": 100, "rate": "1"}, {"up_to": 200, "rate": "2"}, {"up_to": None, "rate": "3"}]
    assert _tier_price(D(0), t) == 0
    assert _tier_price(D(100), t) == 100
    assert _tier_price(D(101), t) == 102
    assert _tier_price(D(200), t) == 300
    assert _tier_price(D(201), t) == 303

def test_tier_price_is_monotonic_and_continuous_random():
    rnd = random.Random(7)
    t = [{"up_to": 500, "rate": "0.5"}, {"up_to": 5000, "rate": "0.2"}, {"up_to": None, "rate": "0.1"}]
    prev = Decimal(0)
    for q in sorted(rnd.randint(0, 20000) for _ in range(300)):
        p = _tier_price(D(q), t)
        assert p >= prev; prev = p

def test_recalc_is_deterministic_and_does_not_mutate_input():
    c = copy.deepcopy(CASE); snap = json.dumps(c, sort_keys=True)
    a, b = recalc(c), recalc(c)
    assert a == b and json.dumps(c, sort_keys=True) == snap

def test_event_order_does_not_change_result():
    c = copy.deepcopy(CASE); r1 = recalc(c)
    random.Random(3).shuffle(c["usage"])
    r2 = recalc(c)
    assert r1["lines"] == r2["lines"]  # hash differs by ordering but money must not

def test_test_events_excluded():
    c = copy.deepcopy(CASE); c["usage"].append({"id": "U50", "metric": "api_calls", "quantity": "999999", "date": "2026-09-05", "is_test": True})
    r = recalc(c); assert "U50" in r["excluded_events"]["TEST_EVENT"] and r["lines"][0]["correct_qty"] == "1200000"

def test_period_boundary_dates_inclusive():
    c = copy.deepcopy(CASE)
    c["usage"] = [{"id": "A", "metric": "api_calls", "quantity": "10", "date": "2026-09-01"},
                  {"id": "B", "metric": "api_calls", "quantity": "10", "date": "2026-09-30"},
                  {"id": "C", "metric": "api_calls", "quantity": "10", "date": "2026-08-31"}]
    r = recalc(c); assert r["excluded_events"] == {"OUT_OF_PERIOD": ["C"]}

def test_discount_minimum_and_rounding():
    c = copy.deepcopy(CASE)
    c["rules"][1] = {"id": "R2", "type": "per_unit", "rate": "0.333", "included": 0, "discount_pct": "10"}
    c["invoice"]["lines"][1].update(metric="seats", quantity="7", amount="2.10")
    c["usage"].append({"id": "S", "metric": "seats", "quantity": "7", "date": "2026-09-02"})
    assert recalc(c)["lines"][1]["correct_amount"] == "2.10"      # 7*0.333*0.9 = 2.0979 -> 2.10
    c["rules"][1]["minimum"] = "5.00"
    assert recalc(c)["lines"][1]["correct_amount"] == "5.00"

def test_underbilling_offsets_overbilling_net():
    c = copy.deepcopy(CASE); c["invoice"]["lines"][1]["amount"] = "900.00"
    r = recalc(c); assert r["lines"][1]["delta"] == "-100.00"
    assert D(r["overbilled"]) == D("200.00")  # L1 +300 over, L2 -100 under => net 200 creditable

def test_unknown_rule_line_is_flagged_not_crashing():
    c = copy.deepcopy(CASE); c["invoice"]["lines"][0]["rule_id"] = "R404"
    assert "AMBIGUOUS_RULE" in recalc(c)["lines"][0]["flags"]

@pytest.mark.parametrize("mutate,msg", [
    (lambda c: c["invoice"].update(period_start="2026-10-05"), "after"),
    (lambda c: c["usage"].append({"id": "U1", "metric": "x", "quantity": "1", "date": "2026-09-01"}), "unique"),
    (lambda c: c["usage"][0].update(quantity="-5"), "must be >= 0"),
    (lambda c: c["usage"][0].update(date="not-a-date"), ""),
    (lambda c: c["rules"][0].update(type="magic"), "unknown type"),
    (lambda c: c["rules"][0].update(tiers=[{"up_to": 10, "rate": "1"}]), "open tier"),
    (lambda c: c["invoice"].update(lines=[]), "no lines"),
    (lambda c: c["invoice"]["lines"][0].update(amount="abc"), "bad number"),
    (lambda c: c["invoice"]["lines"][0].pop("metric"), "missing"),
])
def test_validation_rejects_bad_evidence(mutate, msg):
    c = copy.deepcopy(CASE); mutate(c)
    with pytest.raises(ValueError) as e: validate_case(c)
    assert msg in str(e.value)
    assert client.post("/api/cases", json={"payload": c}).status_code == 422

# ---------- agent: LLM path with a fake model ----------
def test_llm_findings_used_when_grounded(monkeypatch):
    monkeypatch.setattr(agent, "llm_findings", lambda case, calc: [
        {"kind": "calculation_error", "statement": "dup U3", "citations": ["INV-2026-09-0042", "L1", "U3"], "source": "llm"}])
    cid = new_case(); r = invest(cid)
    assert r["llm_used"] and r["findings"] == 1

def test_llm_hallucinated_citation_triggers_retry_then_fallback(monkeypatch):
    calls = []
    def bad(case, calc):
        calls.append(1); return [{"kind": "calculation_error", "statement": "x", "citations": ["U999"], "source": "llm"}]
    monkeypatch.setattr(agent, "llm_findings", bad)
    cid = new_case(); r = invest(cid)
    assert len(calls) == 2 and r["ok"] and not r["llm_used"]
    assert any(t["node"] == "interpret_fallback_rules" for t in r["trace"])

def test_llm_timeout_falls_back_and_is_reported(monkeypatch):
    def boom(case, calc): raise TimeoutError("llm timed out")
    monkeypatch.setattr(agent, "llm_findings", boom)
    cid = new_case(); r = invest(cid)
    assert r["ok"] and not r["llm_used"] and "timed out" in r["llm_error"]

def test_llm_cannot_change_money(monkeypatch):
    monkeypatch.setattr(agent, "llm_findings", lambda c, k: [{"kind": "other", "statement": "refund $99999", "citations": ["L1"], "source": "llm"}])
    cid = new_case(); invest(cid)
    d = client.get(f"/api/cases/{cid}").json()
    assert d["calculation"]["overbilled"] == "500.00"
    r = client.post(f"/api/cases/{cid}/credit", json={"amount": "99999", "reason": "llm said so", "idempotency_key": "llm-key-1"})
    assert r.status_code == 409

def test_llm_json_with_code_fences_is_parsed(monkeypatch):
    import types
    txt = "```json\n{\"findings\":[{\"kind\":\"calculation_error\",\"statement\":\"s\",\"citations\":[\"L1\"]}]}\n```"
    fake = types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **k: types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=txt)])))
    import anthropic; monkeypatch.setattr(anthropic, "Anthropic", lambda **k: fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    out = agent.llm_findings(CASE, recalc(CASE)); assert out[0]["source"] == "llm" and out[0]["citations"] == ["L1"]

# ---------- workflow / safety ----------
def test_credit_requires_investigation_first():
    cid = new_case()
    r = client.post(f"/api/cases/{cid}/credit", json={"amount": "10", "reason": "early", "idempotency_key": "early-key-1"})
    assert r.status_code == 409 and "investigation" in r.json()["detail"]

def test_credit_blocked_on_stale_calculation_then_allowed_after_rerun():
    cid = new_case(); invest(cid)
    client.post(f"/api/cases/{cid}/evidence", json={"patch": {"usage": [{"id": "U60", "metric": "api_calls", "quantity": "1", "date": "2026-09-03"}]}, "note": "late event"})
    body = {"amount": "100", "reason": "dup usage", "idempotency_key": "stale-key-1"}
    r = client.post(f"/api/cases/{cid}/credit", json=body); assert r.status_code == 409 and "stale" in r.json()["detail"]
    invest(cid)
    assert client.post(f"/api/cases/{cid}/credit", json=body).json()["status"] == "approved"

def test_stale_finding_cannot_be_accepted():
    cid = new_case(); invest(cid)
    fid = client.get(f"/api/cases/{cid}").json()["findings"][0]["id"]
    client.post(f"/api/cases/{cid}/evidence", json={"patch": {"usage": []}, "note": "customer says more coming"})
    assert client.post(f"/api/cases/{cid}/findings/{fid}", json={"status": "accepted"}).status_code == 409
    assert client.post(f"/api/cases/{cid}/findings/{fid}", json={"status": "rejected"}).status_code == 200

def test_new_evidence_that_resolves_dispute_changes_recalculation():
    cid = new_case(); invest(cid)
    client.post(f"/api/cases/{cid}/evidence", json={"patch": {"rules": [{"id": "R2", "type": "flat", "amount": "1200.00", "text": "amendment: support is $1,200"}]}, "note": "signed amendment"})
    invest(cid); d = client.get(f"/api/cases/{cid}").json()
    assert d["calculation"]["overbilled"] == "300.00" and not d["calc_stale"] and d["case"]["status"] == "under_review"

def test_concurrent_identical_credit_requests_create_one_adjustment():
    cid = new_case(); invest(cid)
    body = {"amount": "300.00", "reason": "race test", "idempotency_key": "race-key-1"}
    results = []
    def go(): results.append(client.post(f"/api/cases/{cid}/credit", json=body).json().get("status"))
    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert results.count("approved") == 1 and results.count("duplicate_ignored") == 7
    assert len(client.get(f"/api/cases/{cid}").json()["adjustments"]) == 1

def test_concurrent_distinct_credits_never_exceed_cap():
    cid = new_case(); invest(cid)
    results = []
    def go(i): results.append(client.post(f"/api/cases/{cid}/credit", json={"amount": "200", "reason": "split", "idempotency_key": f"cap-key-{i}"}).status_code)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    total = sum(D(a["amount"]) for a in client.get(f"/api/cases/{cid}").json()["adjustments"])
    assert total <= D("500.00") and results.count(200) == 2

def test_input_validation_and_404s():
    assert client.get("/api/cases/NOPE").status_code == 404
    assert client.post("/api/cases/NOPE/investigate").status_code == 404
    cid = new_case()
    assert client.post(f"/api/cases/{cid}/credit", json={"amount": "1", "reason": "x", "idempotency_key": "k"}).status_code == 422
    assert client.post(f"/api/cases/{cid}/findings/F-X", json={"status": "weird"}).status_code == 422
    assert client.post(f"/api/cases/{cid}/request-info", json={"question": ""}).status_code == 422
    assert client.post(f"/api/cases/{cid}/evidence", json={"patch": {"usage": [{"id": "Z", "metric": "m", "quantity": "-1", "date": "2026-09-01"}]}, "note": "bad"}).status_code == 409

def test_zero_negative_and_nonnumeric_credit_rejected():
    cid = new_case(); invest(cid)
    for i, amt in enumerate(["0", "-5", "0.001"]):
        assert client.post(f"/api/cases/{cid}/credit", json={"amount": amt, "reason": "bad amt", "idempotency_key": f"amt-key-{i}"}).status_code == 409
    r = client.post(f"/api/cases/{cid}/credit", json={"amount": "abc", "reason": "bad amt", "idempotency_key": "amt-key-x"})
    assert r.status_code == 422

def test_no_error_case_proposes_no_credit():
    c = copy.deepcopy(CASE)
    c["invoice"]["lines"][0].update(quantity="1200000", amount="9200.00"); c["invoice"]["lines"][1]["amount"] = "1000.00"
    c["usage"] = [u for u in c["usage"] if u["id"] not in ("U3", "U4")]
    cid = new_case(c); r = invest(cid)
    assert [o["option"] for o in r["options"]][0] != "Credit confirmed overbilling"
    assert client.get(f"/api/cases/{cid}").json()["calculation"]["overbilled"] == "0.00"

def test_audit_trail_complete_and_ordered():
    cid = new_case(); invest(cid)
    client.post(f"/api/cases/{cid}/request-info", json={"question": "send usage export"})
    client.post(f"/api/cases/{cid}/credit", json={"amount": "50", "reason": "partial", "idempotency_key": "audit-key-1"})
    acts = [d["action"] for d in client.get(f"/api/cases/{cid}").json()["decisions"]]
    assert acts == ["case_created", "info_requested", "credit_approved"]

def test_json_logs_are_structured(caplog):
    import logging
    caplog.set_level(logging.INFO)
    cid = new_case(); invest(cid)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("{")]
    assert {json.loads(l)["event"] for l in lines} >= {"request", "node"}
    assert lines and all(json.loads(l)["event"] for l in lines)

def test_html_escapes_untrusted_text_in_ui():
    html = (pathlib.Path(__file__).resolve().parent.parent / "static" / "index.html").read_text()
    assert "const esc=" in html and "${esc(d.current.payload.dispute_text)}" in html and "${d.current.payload.dispute_text}" not in html
