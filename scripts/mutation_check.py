"""Mutation check: injects 29 deliberate bugs (one at a time) into a TEMP COPY and reports which the test-suite fails to catch.
Usage: python scripts/mutation_check.py     (takes ~2 min). Known equivalent mutant: 'tier boundary off-by-one'."""
import subprocess, sys, shutil, os, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()
shutil.copytree(ROOT, TMP + "/r", ignore=shutil.ignore_patterns(".git", "__pycache__", "*.db", ".hypothesis", ".pytest_cache"))
os.chdir(TMP + "/r")
M = [
("app/engine.py", 'billable = max(cq - included, Decimal(0))', 'billable = cq - included', "allowance can go negative"),
("app/engine.py", 'if cap is None or qty <= cap:', 'if cap is None or qty < cap:', "tier boundary off-by-one"),
("app/engine.py", 'ROUND_HALF_UP', 'ROUND_DOWN', "rounding mode"),
("app/engine.py", 'period[0] <= d <= period[1]', 'period[0] < d <= period[1]', "period start exclusive"),
("app/engine.py", 'period[0] <= d <= period[1]', 'period[0] <= d < period[1]', "period end exclusive"),
("app/engine.py", 'if key in seen:', 'if False and key in seen:', "duplicates counted"),
("app/engine.py", 'Decimal(1) - D(rule["discount_pct"]) / 100', 'Decimal(1) + D(rule["discount_pct"]) / 100', "discount sign"),
("app/engine.py", 'amt < D(rule["minimum"])', 'amt > D(rule["minimum"])', "minimum charge inverted"),
("app/engine.py", 'metric not in metrics_with_events', 'False and metric not in metrics_with_events', "missing usage = zero (P1)"),
("app/engine.py", 'l.billed_amount if "AMBIGUOUS_RULE" in l.flags else l.correct_amount', 'l.correct_amount', "ambiguous not held"),
("app/engine.py", 'original_total - recalculated_total - credited', 'original_total - recalculated_total', "history credits ignored"),
("app/engine.py", 'if e.get("is_test"):', 'if False:', "test events counted"),
("app/engine.py", 'sorted(events, key=lambda e: (e.get("dedupe_key") is not None, e["date"], e["id"]))', 'sorted(events, key=lambda e: e["id"])', "dedupe id-order dependence (P2)"),
("app/engine.py", 'if rule.get("ambiguous"):', 'if False:', "ambiguity flag dropped"),
("app/engine.py", 'if money(billed_amt) != amt and', 'if False and', "rate mismatch undetected"),
("app/store.py", 'if already + amt > cap:', 'if already + amt >= cap:', "cap boundary exclusive"),
("app/store.py", 'if already + amt > cap:', 'if False:', "cap not enforced"),
("app/store.py", 'if calc_row["evidence_version"] < cur["version"]:', 'if False:', "stale calc credit allowed"),
("app/store.py", 'if calc_row is None:', 'if False:', "credit before investigation"),
("app/store.py", 'if amt <= 0:', 'if amt < 0:', "zero credit allowed"),
("app/store.py", 'if money(D(amount)) != D(existing["amount"]):', 'if False:', "key reuse w/ new amount silent"),
("app/store.py", 'r["evidence_version"] < latest_evidence(cid)["version"]:', 'False:', "stale finding acceptable"),
("app/store.py", '"stale": r["evidence_version"] < cur}', '"stale": False}', "findings never stale"),
("app/store.py", '["max_creditable"]))  # net of credits', '["overbilled"]))  # net of credits', "cap ignores history credits (P3)"),
("app/store.py", "(cid, idempotency_key)).fetchone()\n        if existing:", "(cid, idempotency_key + 'x')).fetchone()\n        if existing:", "idempotency lookup broken"),
("app/store.py", "c.execute(\"DELETE FROM findings WHERE case_id=? AND evidence_version=? AND status='proposed'\", (cid, version))", "pass", "findings duplicate on rerun (P5)"),
("app/agent.py", 'all(c in ids for c in cites)', 'any(c in ids for c in cites)', "partial citations accepted"),
("app/agent.py", '(ok if cites and all', '(ok if all', "empty citations accepted"),
("app/main.py", 'if len(w) >= int(', 'if len(w) > int(', "rate limit off-by-one"),
]
only = sys.argv[1:] 
surv, killed = [], 0
for f, old, new, label in M:
    src = open(f).read()
    if old not in src: print("SKIP (pattern missing):", label); continue
    open(f, "w").write(src.replace(old, new, 1))
    try:
        r = subprocess.run(["python3", "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", "--deselect=tests/test_properties.py::test_fuzz_create_case_never_5xx", "--deselect=tests/test_properties.py::test_fuzz_add_evidence_never_5xx", "--deselect=tests/test_properties.py::test_fuzz_path_and_credit_fields_never_5xx"], capture_output=True, text=True, timeout=120)
        ok = r.returncode != 0
    except subprocess.TimeoutExpired: ok = True
    open(f, "w").write(src)
    if ok: killed += 1
    else: surv.append(label); print("SURVIVED:", label)
print(f"\nkilled {killed}/{killed+len(surv)}; survivors: {surv}")
