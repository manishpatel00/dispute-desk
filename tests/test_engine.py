import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from decimal import Decimal
from app.engine import recalc, _tier_price, D

CASE = json.loads((pathlib.Path(__file__).resolve().parent.parent / "data" / "sample_case.json").read_text())

def test_tiered_pricing_is_graduated():
    tiers = [{"up_to": 500000, "rate": "0.01"}, {"up_to": None, "rate": "0.007"}]
    assert _tier_price(D(1_100_000), tiers) == Decimal("9200.000")

def test_duplicate_and_out_of_period_events_removed():
    r = recalc(CASE)
    assert r["excluded_events"] == {"DUPLICATE_USAGE": ["U3"], "OUT_OF_PERIOD": ["U4"]}
    l1 = r["lines"][0]
    assert l1["correct_qty"] == "1200000" and l1["correct_amount"] == "9200.00" and l1["delta"] == "300.00"

def test_flat_rate_mismatch_detected():
    l2 = recalc(CASE)["lines"][1]
    assert "RATE_MISMATCH" in l2["flags"] and l2["delta"] == "200.00"

def test_ambiguous_rule_is_flagged_not_adjusted():
    l3 = recalc(CASE)["lines"][2]
    assert "AMBIGUOUS_RULE" in l3["flags"]

def test_totals_and_cap():
    r = recalc(CASE)
    assert (r["original_total"], r["recalculated_total"], r["max_creditable"]) == ("11100.00", "10600.00", "500.00")

def test_no_float_drift():
    c = json.loads(json.dumps(CASE)); c["rules"][1] = {"id": "R2", "type": "per_unit", "rate": "0.1", "included": 0}
    c["invoice"]["lines"][1].update(metric="x", quantity="3", amount="0.30")
    c["usage"].append({"id": "U9", "metric": "x", "quantity": "3", "date": "2026-09-02"})
    assert recalc(c)["lines"][1]["correct_amount"] == "0.30"

def test_evidence_hash_changes_with_evidence():
    c2 = json.loads(json.dumps(CASE)); c2["usage"].append({"id": "U7", "metric": "api_calls", "quantity": "1", "date": "2026-09-02"})
    assert recalc(CASE)["evidence_hash"] != recalc(c2)["evidence_hash"]
