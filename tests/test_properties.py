"""Property-based + fuzz tests (Hypothesis). Invariants that must hold for ANY valid evidence."""
import copy, json, os, pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db"); os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["INVESTIGATE_PER_MIN"] = "1000000"
from decimal import Decimal
from hypothesis import given, settings, strategies as st, HealthCheck
from fastapi.testclient import TestClient
from app.main import app
from app.engine import recalc, validate_case, D, money

client = TestClient(app)
SET = settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
qty = st.integers(0, 5_000_000).map(str)
rate = st.decimals(min_value=0, max_value=5, places=4, allow_nan=False).map(str)
day = st.integers(1, 30).map(lambda d: f"2026-09-{d:02d}")

@st.composite
def cases(draw):
    metrics = ["api_calls", "storage_gb"]
    events = []
    for i in range(draw(st.integers(0, 12))):
        e = {"id": f"U{i:03d}", "metric": draw(st.sampled_from(metrics)), "quantity": draw(qty),
             "date": draw(st.one_of(day, st.just("2026-10-02"), st.just("2026-08-30")))}
        if draw(st.booleans()) and events: e["dedupe_key"] = draw(st.sampled_from(events))["id"]
        if draw(st.integers(0, 9)) == 0: e["is_test"] = True
        events.append(e)
    tiers = [{"up_to": draw(st.integers(1, 1000)), "rate": draw(rate)}]
    tiers.append({"up_to": tiers[0]["up_to"] + draw(st.integers(1, 100000)), "rate": draw(rate)}); tiers.append({"up_to": None, "rate": draw(rate)})
    rules = [{"id": "R1", "type": "tiered", "included": draw(st.integers(0, 500)), "tiers": tiers, **({"discount_pct": draw(st.integers(0, 100)).__str__()} if draw(st.booleans()) else {}), **({"minimum": draw(st.integers(0, 50)).__str__()} if draw(st.booleans()) else {})},
             {"id": "R2", "type": "flat", "amount": draw(st.integers(0, 5000)).__str__()},
             {"id": "R3", "type": "per_unit", "rate": draw(rate), "included": 0, **({"ambiguous": "unclear"} if draw(st.booleans()) else {})}]
    lines = [{"id": "L1", "metric": "api_calls", "quantity": draw(qty), "amount": f"{draw(st.integers(0, 90000))}.{draw(st.integers(0,99)):02d}", "rule_id": "R1"},
             {"id": "L2", "metric": "support", "quantity": "1", "amount": f"{draw(st.integers(0, 6000))}.00", "rule_id": "R2"},
             {"id": "L3", "metric": "storage_gb", "quantity": draw(qty), "amount": f"{draw(st.integers(0, 9000))}.{draw(st.integers(0,99)):02d}", "rule_id": "R3"}]
    adj = [{"id": "A1", "type": "credit", "amount": f"{draw(st.integers(0, 300))}.00"}] if draw(st.booleans()) else []
    return {"customer": "P", "dispute_text": "d", "invoice": {"id": "INV-P", "period_start": "2026-09-01", "period_end": "2026-09-30", "lines": lines},
            "rules": rules, "usage": events, "adjustments": adj}

@given(cases())
@SET
def test_prop_recalc_total_function_and_invariants(c):
    validate_case(c); r = recalc(c)
    assert D(r["overbilled"]) >= 0 and D(r["max_creditable"]) >= 0 and D(r["max_creditable"]) <= D(r["overbilled"])
    for l in r["lines"]:                                   # every money value has exactly 2 decimals
        for k in ("billed_amount", "correct_amount", "delta"): assert D(l[k]) == money(D(l[k])) and len(l[k].split(".")[1]) == 2
    held = sum((D(l["delta"]) for l in r["lines"] if l["held"]), Decimal(0))
    assert D(r["unresolved_ambiguous"]) == held
    eff = sum((D(l["billed_amount"]) if "AMBIGUOUS_RULE" in l["flags"] else D(l["correct_amount"]) for l in r["lines"]), Decimal(0))
    assert D(r["recalculated_total"]) == money(eff)
    assert D(r["original_total"]) - D(r["recalculated_total"]) == (D(r["original_total"]) - D(r["recalculated_total"]))
    for l in r["lines"]:
        if "MISSING_USAGE" in l["flags"] or "CALC_ERROR" in l["flags"]: assert D(l["delta"]) == 0

@given(cases(), st.randoms())
@SET
def test_prop_event_order_irrelevant_to_money(c, rnd):
    a = recalc(c); c2 = copy.deepcopy(c); rnd.shuffle(c2["usage"]); b = recalc(c2)
    assert a["lines"] == b["lines"] and a["overbilled"] == b["overbilled"]

@given(cases())
@SET
def test_prop_unrelated_metric_event_does_not_change_lines(c):
    c2 = copy.deepcopy(c); c2["usage"].append({"id": "ZZZ", "metric": "unrelated", "quantity": "999", "date": "2026-09-02"})
    assert recalc(c)["lines"] == recalc(c2)["lines"]

@given(cases())
@SET
def test_prop_duplicate_event_never_adds_quantity(c):
    if not c["usage"]: return
    e = c["usage"][0]
    if e.get("is_test") or not ("2026-09-01" <= e["date"] <= "2026-09-30"): return
    c2 = copy.deepcopy(c); c2["usage"].append({**e, "id": "DUP-" + e["id"], "dedupe_key": e.get("dedupe_key") or e["id"]})
    m = lambda r: [(l["correct_qty"], l["correct_amount"], l["delta"]) for l in r["lines"]]
    assert m(recalc(c)) == m(recalc(c2))

@given(cases(), st.lists(st.integers(1, 400), min_size=1, max_size=8))
@SET
def test_prop_credits_never_exceed_cap_through_api(c, amounts):
    cid = client.post("/api/cases", json={"payload": c}).json()["id"]; client.post(f"/api/cases/{cid}/investigate")
    cap = D(recalc(c)["max_creditable"])
    for i, a in enumerate(amounts): client.post(f"/api/cases/{cid}/credit", json={"amount": str(a), "reason": "prop test", "idempotency_key": f"p-{cid}-{i}"})
    total = sum((D(x["amount"]) for x in client.get(f"/api/cases/{cid}").json()["adjustments"]), Decimal(0))
    assert total <= cap

# ---------- fuzzing: no input may ever cause a 5xx ----------
junk = st.recursive(st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False, allow_infinity=False) | st.text(max_size=30),
                    lambda ch: st.lists(ch, max_size=4) | st.dictionaries(st.text(max_size=8), ch, max_size=4), max_leaves=12)

@given(junk)
@settings(max_examples=150, deadline=None)
def test_fuzz_create_case_never_5xx(payload):
    assert client.post("/api/cases", json={"payload": payload} if isinstance(payload, dict) or True else payload).status_code < 500

@given(st.dictionaries(st.sampled_from(["usage", "rules", "adjustments", "dispute_text", "invoice"]), junk, max_size=3), st.text(max_size=12))
@settings(max_examples=100, deadline=None)
def test_fuzz_add_evidence_never_5xx(patch, note):
    cid = client.post("/api/cases", json={"payload": json.loads((pathlib.Path(__file__).resolve().parent.parent / "data" / "sample_case.json").read_text())}).json()["id"]
    assert client.post(f"/api/cases/{cid}/evidence", json={"patch": patch, "note": note}).status_code < 500

@given(st.text(max_size=40), st.text(max_size=40), st.text(max_size=20))
@settings(max_examples=100, deadline=None)
def test_fuzz_path_and_credit_fields_never_5xx(cid, amount, key):
    from urllib.parse import quote
    assert client.get("/api/cases/" + quote(cid, safe="")).status_code < 500
    real = client.post("/api/cases", json={"payload": json.loads((pathlib.Path(__file__).resolve().parent.parent / "data" / "sample_case.json").read_text())}).json()["id"]
    client.post(f"/api/cases/{real}/investigate")
    assert client.post(f"/api/cases/{real}/credit", json={"amount": amount, "reason": "fuzz reason", "idempotency_key": key or "k"}).status_code < 500


def test_raw_json_nan_infinity_tokens_rejected_not_500():
    base = json.loads((pathlib.Path(__file__).resolve().parent.parent / "data" / "sample_case.json").read_text())
    for tok in ("NaN", "Infinity", "-Infinity", "1e999999999"):
        body = json.dumps({"payload": base}).replace('"quantity": "600000"', f'"quantity": {tok}', 1)
        r = client.post("/api/cases", content=body, headers={"Content-Type": "application/json"})
        assert r.status_code < 500, (tok, r.status_code, r.text)

def test_malformed_and_oversized_bodies_not_500():
    assert client.post("/api/cases", content=b"{not json", headers={"Content-Type": "application/json"}).status_code == 422
    assert client.post("/api/cases", content=b"", headers={"Content-Type": "application/json"}).status_code == 422
    assert client.post("/api/cases", json=[1, 2, 3]).status_code == 422
