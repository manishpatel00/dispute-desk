import json, os, pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db"); os.environ.pop("ANTHROPIC_API_KEY", None)
from fastapi.testclient import TestClient
from app.main import app
from app import store

client = TestClient(app)
CASE = json.loads((pathlib.Path(__file__).resolve().parent.parent / "data" / "sample_case.json").read_text())

def test_security_headers_on_ui_api_and_errors():
    for r in (client.get("/"), client.get("/api/health"), client.get("/api/cases/NOPE")):
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"] and "connect-src 'self'" in r.headers["content-security-policy"]

def test_oversized_body_rejected_413_with_headers():
    r = client.post("/api/cases", content=b"x" * 2_100_000, headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and "nosniff" in r.headers["x-content-type-options"]

def test_overlong_dispute_text_rejected():
    c = dict(CASE, dispute_text="x" * 5001)
    assert client.post("/api/cases", json={"payload": c}).status_code == 422

def test_sql_injection_strings_are_inert():
    cid = client.post("/api/cases", json={"payload": CASE}).json()["id"]; client.post(f"/api/cases/{cid}/investigate")
    evil = "x'); DROP TABLE cases;--"
    client.post(f"/api/cases/{cid}/credit", json={"amount": "10", "reason": evil, "idempotency_key": evil + "kk"})
    client.post(f"/api/cases/{cid}/request-info", json={"question": evil})
    client.post(f"/api/cases/{cid}/evidence", json={"patch": {"dispute_text": evil}, "note": evil})
    assert client.get("/api/cases/" + evil.replace("/", "_")).status_code == 404
    assert len(client.get("/api/cases").json()) >= 1 and client.get(f"/api/cases/{cid}").status_code == 200

def test_path_traversal_blocked():
    for p in ("/static/../app/main.py", "/static/%2e%2e/app/main.py", "/static/..%2fapp%2fmain.py", "/../etc/passwd"):
        r = client.get(p); assert r.status_code in (400, 404) and "FastAPI" not in r.text

def test_stored_xss_payload_is_data_only_and_ui_escapes_every_dynamic_field():
    xss = "<img src=x onerror=alert(1)>"
    cid = client.post("/api/cases", json={"payload": dict(CASE, customer=xss, dispute_text=xss)}).json()["id"]
    assert client.get(f"/api/cases/{cid}").json()["case"]["customer"] == xss        # stored verbatim, escaped at render
    html = (pathlib.Path(__file__).resolve().parent.parent / "static" / "index.html").read_text()
    import re
    for expr in re.findall(r"\$\{([^}]*)\}", html.split("function render")[1]):
        if any(w in expr for w in ("customer", "dispute_text", "statement", "reason", "detail", "actor", "action", ".id", "citations", "note")):
            assert "esc(" in expr or "map(esc)" in expr, f"unescaped dynamic expression: {expr}"

def test_internal_errors_do_not_leak_details(monkeypatch):
    from app import main
    monkeypatch.setattr(main.store, "list_cases", lambda: (_ for _ in ()).throw(RuntimeError("secret /etc/path stack")))
    r = TestClient(app, raise_server_exceptions=False).get("/api/cases")
    assert r.status_code == 500 and "secret" not in r.text and "rid" in r.json()

def test_sqlite_uses_wal():
    with store.conn() as c: assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

def test_rate_limiter_memory_is_pruned(monkeypatch):
    from app import main; main._hits.clear()
    for i in range(1200): main._hits[f"ip{i}"] = [0.0]
    cid = client.post("/api/cases", json={"payload": CASE}).json()["id"]; client.post(f"/api/cases/{cid}/investigate")
    assert len(main._hits) < 100; main._hits.clear()
