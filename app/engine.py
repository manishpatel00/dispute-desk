"""Deterministic billing engine. No LLM here: all money math uses Decimal."""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")

def D(x) -> Decimal:
    return Decimal(str(x))

def money(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)

def evidence_hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]

@dataclass
class LineResult:
    line_id: str
    metric: str
    billed_qty: Decimal
    billed_amount: Decimal
    correct_qty: Decimal
    correct_amount: Decimal
    delta: Decimal
    rule_id: str | None
    notes: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

def _tier_price(qty: Decimal, tiers: list[dict]) -> Decimal:
    total, prev = Decimal(0), Decimal(0)
    for t in tiers:
        cap = D(t["up_to"]) if t.get("up_to") is not None else None
        top = qty if cap is None else min(qty, cap)
        if top > prev:
            total += (top - prev) * D(t["rate"])
        if cap is None or qty <= cap:
            break
        prev = cap
    return total

def clean_usage(events: list[dict], period: tuple[date, date]):
    seen: set[str] = set()
    qty: dict[str, Decimal] = {}
    excluded: dict[str, list[dict]] = {"DUPLICATE_USAGE": [], "OUT_OF_PERIOD": [], "TEST_EVENT": []}
    for e in sorted(events, key=lambda e: (e.get("dedupe_key") is not None, e["date"], e["id"])):
        d = date.fromisoformat(e["date"])
        if e.get("is_test"):
            excluded["TEST_EVENT"].append(e); continue
        if not (period[0] <= d <= period[1]):
            excluded["OUT_OF_PERIOD"].append(e); continue
        key = e.get("dedupe_key") or e["id"]
        if key in seen:
            excluded["DUPLICATE_USAGE"].append(e); continue
        seen.add(key)
        qty[e["metric"]] = qty.get(e["metric"], Decimal(0)) + D(e["quantity"])
    return qty, excluded

def _calc_line(ln, rules, qty, excluded, metrics_with_events) -> LineResult:
    rule = rules.get(ln.get("rule_id") or "")
    metric = ln["metric"]
    billed_qty, billed_amt = D(ln["quantity"]), D(ln["amount"])
    notes, flags = [], []
    if rule is None:
        return LineResult(ln["id"], metric, billed_qty, billed_amt, billed_qty, billed_amt, Decimal(0), None,
                          ["No matching rule: cannot recalculate"], ["AMBIGUOUS_RULE"])
    if rule["type"] != "flat" and metric not in metrics_with_events:
        # No usage evidence at all is NOT zero usage. Do not invent an overbilling.
        return LineResult(ln["id"], metric, billed_qty, billed_amt, billed_qty, billed_amt, Decimal(0), rule["id"],
                          ["No usage events supplied for this metric; cannot verify billed quantity"], ["MISSING_USAGE"])
    if rule["type"] == "flat":
        cq, amt = Decimal(1), D(rule["amount"])
    else:
        cq = qty.get(metric, Decimal(0))
        included = D(rule.get("included", 0))
        billable = max(cq - included, Decimal(0))
        amt = billable * D(rule["rate"]) if rule["type"] == "per_unit" else _tier_price(billable, rule["tiers"])
        if included:
            notes.append(f"{included} units included")
    if rule.get("discount_pct"):
        amt = amt * (Decimal(1) - D(rule["discount_pct"]) / 100)
    if rule.get("minimum") is not None and amt < D(rule["minimum"]):
        amt = D(rule["minimum"]); notes.append("minimum charge applied")
    if rule.get("ambiguous"):
        flags.append("AMBIGUOUS_RULE"); notes.append(rule["ambiguous"])
    amt = money(amt)
    if rule["type"] != "flat" and billed_qty != cq:
        flags.append("QTY_MISMATCH")
        for k, ev in excluded.items():
            if any(e["metric"] == metric for e in ev):
                flags.append(k)
    if money(billed_amt) != amt and "QTY_MISMATCH" not in flags and "AMBIGUOUS_RULE" not in flags:
        flags.append("RATE_MISMATCH")
    return LineResult(ln["id"], metric, billed_qty, billed_amt, cq, amt, money(billed_amt - amt), rule["id"], notes, sorted(set(flags)))

def validate_case(case: dict) -> None:
    """Raise ValueError with a human-readable message for bad evidence."""
    try:
        def num(v, label, lo=None, hi=None):
            x = D(v)
            if not x.is_finite():
                raise ValueError(f"{label} must be a finite number")
            if lo is not None and x < lo:
                raise ValueError(f"{label} must be >= {lo}")
            if hi is not None and x > hi:
                raise ValueError(f"{label} must be <= {hi}")
        inv = case["invoice"]
        date.fromisoformat(inv["period_start"]); date.fromisoformat(inv["period_end"])
        if date.fromisoformat(inv["period_start"]) > date.fromisoformat(inv["period_end"]):
            raise ValueError("invoice period_start is after period_end")
        if not inv["lines"]:
            raise ValueError("invoice has no lines")
        for l in inv["lines"]:
            num(l["quantity"], f"line {l['id']} quantity", 0); num(l["amount"], f"line {l['id']} amount"); l["metric"]
        if len(str(case.get("dispute_text") or "")) > 5000:
            raise ValueError("dispute_text too long (max 5000 characters)")
        if len(case["usage"]) > 20000:
            raise ValueError("too many usage events (max 20000)")
        ids = [u["id"] for u in case["usage"]]
        if len(ids) != len(set(ids)):
            raise ValueError("usage event ids must be unique")
        for u in case["usage"]:
            date.fromisoformat(u["date"])
            num(u["quantity"], f"usage event {u['id']} quantity", 0)
        for r in case["rules"]:
            if r["type"] not in ("flat", "per_unit", "tiered"):
                raise ValueError(f"rule {r['id']} has unknown type {r['type']}")
            if r["type"] == "flat": num(r["amount"], f"rule {r['id']} amount", 0)
            if r["type"] == "per_unit": num(r["rate"], f"rule {r['id']} rate", 0)
            num(r.get("included", 0), f"rule {r['id']} included", 0)
            if r.get("discount_pct") is not None: num(r["discount_pct"], f"rule {r['id']} discount_pct", 0, 100)
            if r.get("minimum") is not None: num(r["minimum"], f"rule {r['id']} minimum", 0)
            for t in r.get("tiers", []): num(t["rate"], f"rule {r['id']} tier rate", 0)
            if r["type"] == "tiered":
                caps = [t["up_to"] for t in r["tiers"] if t.get("up_to") is not None]
                if caps != sorted(caps) or r["tiers"][-1].get("up_to") is not None:
                    raise ValueError(f"rule {r['id']} tiers must be ascending and end with an open tier")
    except (KeyError, TypeError, AttributeError, IndexError) as e:
        raise ValueError(f"malformed evidence: missing or invalid field {e}")
    except ArithmeticError as e:
        raise ValueError(f"malformed evidence: bad number ({e})")

def recalc(case: dict) -> dict:
    validate_case(case)
    inv = case["invoice"]
    period = (date.fromisoformat(inv["period_start"]), date.fromisoformat(inv["period_end"]))
    qty, excluded = clean_usage(case["usage"], period)
    rules = {r["id"]: r for r in case["rules"]}
    lines: list[LineResult] = []
    metrics_with_events = {u["metric"] for u in case["usage"]}
    for ln in inv["lines"]:
        try:
            lines.append(_calc_line(ln, rules, qty, excluded, metrics_with_events))
        except Exception as e:  # partial failure: isolate the line, keep the rest
            b = D(ln["amount"])
            lines.append(LineResult(ln["id"], ln["metric"], D(ln["quantity"]), b, D(ln["quantity"]), b, Decimal(0),
                                    ln.get("rule_id"), [f"calculation failed: {e}"], ["CALC_ERROR"]))
    original_total = money(sum((l.billed_amount for l in lines), Decimal(0)))
    # Lines whose contract rule is ambiguous are held at the billed amount: a human must interpret the contract.
    recalculated_total = money(sum((l.billed_amount if "AMBIGUOUS_RULE" in l.flags else l.correct_amount for l in lines), Decimal(0)))
    unresolved = money(sum((l.delta for l in lines if "AMBIGUOUS_RULE" in l.flags and l.delta != 0), Decimal(0)))
    credited = money(sum((D(a["amount"]) for a in case.get("adjustments", []) if a["type"] == "credit"), Decimal(0)))
    def ser(l: LineResult) -> dict:
        d = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in l.__dict__.items()}
        for k in ("billed_amount", "correct_amount", "delta"):
            d[k] = str(money(getattr(l, k)))
        d["held"] = "AMBIGUOUS_RULE" in l.flags and l.delta != 0
        return d
    return {
        "lines": [ser(l) for l in lines],
        "original_total": str(original_total),
        "recalculated_total": str(recalculated_total),
        "overbilled": str(max(original_total - recalculated_total, Decimal(0))),
        "unresolved_ambiguous": str(unresolved),
        "already_credited": str(credited),
        "max_creditable": str(max(original_total - recalculated_total - credited, Decimal(0))),
        "errors": [l.line_id for l in lines if "CALC_ERROR" in l.flags],
        "missing_usage": [l.line_id for l in lines if "MISSING_USAGE" in l.flags],
        "partial": any("CALC_ERROR" in l.flags for l in lines),
        "excluded_events": {k: [e["id"] for e in v] for k, v in excluded.items() if v},
        "evidence_hash": evidence_hash(case),
    }
