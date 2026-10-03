"""Post-deploy smoke test:  python scripts/smoke.py https://your-app.onrender.com
Exits non-zero on failure. Prints whether the AI path was actually used."""
import json, sys, urllib.request as u, uuid
base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
def call(m, p, b=None):
    r = u.Request(base + p, method=m, data=json.dumps(b).encode() if b is not None else None, headers={"Content-Type": "application/json"})
    try:
        with u.urlopen(r, timeout=90) as x: return x.status, json.load(x)
    except u.HTTPError as e: return e.code, json.load(e)
def check(name, ok, extra=""):
    print(("PASS " if ok else "FAIL ") + name, extra)
    if not ok: sys.exit(1)
s, h = call("GET", "/api/health"); check("health", s == 200 and h["ok"]); print("   llm_configured:", h["llm_configured"])
s, sample = call("GET", "/api/sample"); check("sample", s == 200)
s, r = call("POST", "/api/cases", {"payload": sample}); cid = r["id"]; check("create case", s == 200, cid)
s, r = call("POST", f"/api/cases/{cid}/investigate"); check("investigate", s == 200 and r["ok"], f"AI used: {r.get('llm_used')} | error: {r.get('llm_error')}")
s, d = call("GET", f"/api/cases/{cid}"); check("recalc matches expected 500.00 overbilling", d["calculation"]["overbilled"] == "500.00")
check("findings cite evidence", all(f["citations"] for f in d["findings"]), f"{len(d['findings'])} findings")
k = {"amount": "300.00", "reason": "smoke test", "idempotency_key": "smoke-" + uuid.uuid4().hex}
s, a = call("POST", f"/api/cases/{cid}/credit", k); check("credit approved", a["status"] == "approved")
s, b = call("POST", f"/api/cases/{cid}/credit", k); check("duplicate credit ignored", b["status"] == "duplicate_ignored")
s, c = call("POST", f"/api/cases/{cid}/credit", {**k, "amount": "300", "idempotency_key": "smoke-" + uuid.uuid4().hex}); check("over-cap credit blocked", s == 409)
s, e = call("POST", f"/api/cases/{cid}/evidence", {"patch": {"usage": [{"id": "SMK1", "metric": "api_calls", "quantity": "1", "date": "2026-09-02"}]}, "note": "smoke evidence"})
s, d = call("GET", f"/api/cases/{cid}"); check("reopen + stale flags", d["case"]["status"] == "reopened" and d["calc_stale"])
print("ALL SMOKE CHECKS PASSED")
