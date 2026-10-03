"""SQLite persistence: cases, evidence versions, calculations, findings, decisions, adjustments."""
import json, os, sqlite3, time, uuid
from contextlib import contextmanager
from decimal import Decimal
from .engine import D, money

DB_PATH = os.environ.get("DB_PATH", "billing.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, customer TEXT, status TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS evidence (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, version INTEGER, hash TEXT, payload TEXT, note TEXT, created_at REAL, UNIQUE(case_id, version));
CREATE TABLE IF NOT EXISTS calculations (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, evidence_version INTEGER, result TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS findings (id TEXT PRIMARY KEY, case_id TEXT, evidence_version INTEGER, kind TEXT, statement TEXT, citations TEXT, source TEXT, status TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, actor TEXT, action TEXT, detail TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS adjustments (id TEXT PRIMARY KEY, case_id TEXT, idempotency_key TEXT, amount TEXT, reason TEXT, evidence_version INTEGER, created_at REAL, UNIQUE(case_id, idempotency_key));
CREATE TABLE IF NOT EXISTS agent_runs (id TEXT PRIMARY KEY, case_id TEXT, evidence_version INTEGER, trace TEXT, created_at REAL);
CREATE INDEX IF NOT EXISTS idx_evidence_case ON evidence(case_id);
CREATE INDEX IF NOT EXISTS idx_calculations_case ON calculations(case_id);
CREATE INDEX IF NOT EXISTS idx_findings_case ON findings(case_id);
CREATE INDEX IF NOT EXISTS idx_decisions_case ON decisions(case_id);
CREATE INDEX IF NOT EXISTS idx_adjustments_case ON adjustments(case_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_case ON agent_runs(case_id);
"""

@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=10000")
    try:
        yield c
        c.commit()
    finally:
        c.close()

def init():
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)

def now(): return time.time()

def create_case(payload: dict) -> str:
    cid = "CASE-" + uuid.uuid4().hex[:8].upper()
    from .engine import evidence_hash
    with conn() as c:
        c.execute("INSERT INTO cases VALUES (?,?,?,?)", (cid, payload.get("customer", "?"), "open", now()))
        c.execute("INSERT INTO evidence (case_id,version,hash,payload,note,created_at) VALUES (?,?,?,?,?,?)",
                  (cid, 1, evidence_hash(payload), json.dumps(payload), "initial submission", now()))
    log_decision(cid, "system", "case_created", {})
    return cid

def latest_evidence(cid: str) -> dict:
    with conn() as c:
        r = c.execute("SELECT * FROM evidence WHERE case_id=? ORDER BY version DESC LIMIT 1", (cid,)).fetchone()
    if not r: raise KeyError(cid)
    return {"version": r["version"], "hash": r["hash"], "payload": json.loads(r["payload"]), "note": r["note"]}

def add_evidence(cid: str, patch: dict, note: str, actor="reviewer") -> int:
    """Append new usage/rules/adjustments; reopens case and marks older findings stale (by version)."""
    from .engine import evidence_hash
    cur = latest_evidence(cid)
    if not isinstance(patch, dict) or not patch:
        raise ValueError("patch must be a non-empty object")
    unknown = set(patch) - {"usage", "rules", "adjustments", "dispute_text"}
    if unknown:
        raise ValueError(f"patch may only contain usage, rules, adjustments, dispute_text (got {sorted(map(str, unknown))})")
    for k in ("usage", "rules", "adjustments"):
        if k in patch and (not isinstance(patch[k], list) or not all(isinstance(x, dict) and isinstance(x.get("id"), str) and x["id"] for x in patch[k])):
            raise ValueError(f"patch.{k} must be a list of objects that each have a string 'id'")
    if "dispute_text" in patch and (not isinstance(patch["dispute_text"], str) or len(patch["dispute_text"]) > 5000):
        raise ValueError("patch.dispute_text must be a string up to 5000 characters")
    p = json.loads(json.dumps(cur["payload"]))
    for k in ("usage", "rules", "adjustments"):
        if k in patch:
            existing = {x["id"]: i for i, x in enumerate(p.get(k, [])) if "id" in x}
            for item in patch[k]:
                if item.get("id") in existing: p[k][existing[item["id"]]] = item
                else: p.setdefault(k, []).append(item)
    if "dispute_text" in patch: p["dispute_text"] = (p.get("dispute_text") or "") + "\n" + patch["dispute_text"]
    from .engine import validate_case
    validate_case(p)
    v = cur["version"] + 1
    try:
        with conn() as c:
            c.execute("INSERT INTO evidence (case_id,version,hash,payload,note,created_at) VALUES (?,?,?,?,?,?)",
                      (cid, v, evidence_hash(p), json.dumps(p), note, now()))
            c.execute("UPDATE cases SET status='reopened' WHERE id=?", (cid,))
    except sqlite3.IntegrityError:
        raise ValueError("another evidence update happened at the same time: refresh and retry")
    log_decision(cid, actor, "evidence_added", {"version": v, "note": note})
    return v

def save_calc(cid, version, result):
    with conn() as c:
        c.execute("INSERT INTO calculations (case_id,evidence_version,result,created_at) VALUES (?,?,?,?)",
                  (cid, version, json.dumps(result), now()))

def save_findings(cid, version, findings: list[dict]):
    with conn() as c:
        have = {(r["kind"], r["statement"]) for r in c.execute("SELECT kind,statement FROM findings WHERE case_id=? AND evidence_version=?", (cid, version))}
        c.execute("DELETE FROM findings WHERE case_id=? AND evidence_version=? AND status='proposed'", (cid, version))
        have = {(r["kind"], r["statement"]) for r in c.execute("SELECT kind,statement FROM findings WHERE case_id=? AND evidence_version=?", (cid, version))}
        for f in findings:
            if (f["kind"], f["statement"]) in have:
                continue
            c.execute("INSERT INTO findings VALUES (?,?,?,?,?,?,?,?,?)",
                      (f"F-{uuid.uuid4().hex[:6].upper()}", cid, version, f["kind"], f["statement"],
                       json.dumps(f["citations"]), f.get("source", "rules"), "proposed", now()))

def list_findings(cid):
    cur = latest_evidence(cid)["version"]
    with conn() as c:
        rows = c.execute("SELECT * FROM findings WHERE case_id=? ORDER BY created_at", (cid,)).fetchall()
    return [{**dict(r), "citations": json.loads(r["citations"]), "stale": r["evidence_version"] < cur} for r in rows]

def set_finding(cid, fid, status, actor, statement=None):
    with conn() as c:
        r = c.execute("SELECT * FROM findings WHERE id=? AND case_id=?", (fid, cid)).fetchone()
        if not r: raise KeyError(fid)
        if status in ("accepted", "edited") and r["evidence_version"] < latest_evidence(cid)["version"]:
            raise ValueError("finding is stale (evidence changed): re-run the investigation first")
        if statement: c.execute("UPDATE findings SET statement=? WHERE id=?", (statement, fid))
        c.execute("UPDATE findings SET status=? WHERE id=?", (status, fid))
    log_decision(cid, actor, f"finding_{status}", {"finding": fid, "edited": bool(statement)})

def latest_calc(cid):
    with conn() as c:
        r = c.execute("SELECT * FROM calculations WHERE case_id=? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
    return None if not r else {"evidence_version": r["evidence_version"], **json.loads(r["result"])}

def log_decision(cid, actor, action, detail):
    with conn() as c:
        c.execute("INSERT INTO decisions (case_id,actor,action,detail,created_at) VALUES (?,?,?,?,?)",
                  (cid, actor, action, json.dumps(detail), now()))

def approve_credit(cid: str, amount: str, reason: str, actor: str, idempotency_key: str):
    """Mock credit. Idempotent, capped by deterministic recalculation, refused on stale/missing calculation.
    Uses BEGIN IMMEDIATE so concurrent requests cannot both pass the cap check."""
    from .engine import recalc
    import sqlite3
    cur = latest_evidence(cid)
    calc_row = latest_calc(cid)
    c = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    try:
        c.execute("BEGIN IMMEDIATE")
        existing = c.execute("SELECT * FROM adjustments WHERE case_id=? AND idempotency_key=?", (cid, idempotency_key)).fetchone()
        if existing:
            if money(D(amount)) != D(existing["amount"]):
                raise ValueError("idempotency key already used for a different amount")
            c.execute("ROLLBACK")
            return {"status": "duplicate_ignored", "adjustment_id": existing["id"], "amount": existing["amount"]}
        if calc_row is None:
            raise ValueError("no calculation yet: run the investigation first")
        if calc_row["evidence_version"] < cur["version"]:
            raise ValueError("calculation is stale (new evidence added): re-run the investigation before approving a credit")
        amt = money(D(amount))
        if amt <= 0:
            raise ValueError("amount must be positive")
        cap = money(D(recalc(cur["payload"])["max_creditable"]))  # net of credits already in evidence history
        already = sum((D(r["amount"]) for r in c.execute("SELECT amount FROM adjustments WHERE case_id=?", (cid,))), Decimal(0))
        if already + amt > cap:
            raise ValueError(f"exceeds recalculated overbilling: cap {cap}, already credited {already}, requested {amt}")
        aid = "ADJ-" + uuid.uuid4().hex[:8].upper()
        c.execute("INSERT INTO adjustments VALUES (?,?,?,?,?,?,?)", (aid, cid, idempotency_key, str(amt), reason, cur["version"], now()))
        c.execute("UPDATE cases SET status='resolved' WHERE id=?", (cid,))
        c.execute("COMMIT")
    except sqlite3.IntegrityError:
        c.execute("ROLLBACK")
        row = c.execute("SELECT * FROM adjustments WHERE case_id=? AND idempotency_key=?", (cid, idempotency_key)).fetchone()
        return {"status": "duplicate_ignored", "adjustment_id": row["id"], "amount": row["amount"]}
    except Exception:
        if c.in_transaction: c.execute("ROLLBACK")
        raise
    finally:
        c.close()
    log_decision(cid, actor, "credit_approved", {"adjustment": aid, "amount": str(amt), "evidence_version": cur["version"]})
    return {"status": "approved", "adjustment_id": aid, "amount": str(amt)}

def get_case(cid):
    with conn() as c:
        r = c.execute("SELECT * FROM cases WHERE id=?", (cid,)).fetchone()
        if not r: raise KeyError(cid)
        dec = [dict(x) for x in c.execute("SELECT * FROM decisions WHERE case_id=? ORDER BY id", (cid,))]
        adj = [dict(x) for x in c.execute("SELECT * FROM adjustments WHERE case_id=? ORDER BY created_at", (cid,))]
        ev = [dict(x) | {"payload": None} for x in c.execute("SELECT * FROM evidence WHERE case_id=? ORDER BY version", (cid,))]
    cur = latest_evidence(cid)
    calc = latest_calc(cid)
    given = sum((D(a["amount"]) for a in adj), Decimal(0))
    if calc:
        calc["creditable_now"] = str(max(money(D(calc["max_creditable"]) - given), Decimal(0)))
        calc["credited_in_app"] = str(money(given))
    adj = [a | {"stale": a["evidence_version"] < cur["version"]} for a in adj]
    return {"case": dict(r), "evidence": ev, "current": cur, "calculation": calc,
            "calc_stale": bool(calc and calc["evidence_version"] < cur["version"]),
            "findings": list_findings(cid), "decisions": dec, "adjustments": adj}

def list_cases():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM cases ORDER BY created_at DESC")]

def set_status(cid, status):
    with conn() as c:
        c.execute("UPDATE cases SET status=? WHERE id=? AND status!='resolved'", (status, cid))
