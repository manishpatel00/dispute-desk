import json, logging, os, sys, time, uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import store, agent
from .engine import recalc, validate_case

logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
log = logging.getLogger("api")
ROOT = Path(__file__).resolve().parent.parent
MAX_BODY = int(os.environ.get("MAX_BODY_BYTES", "2000000"))
SEC_HEADERS = {
    "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
}
app = FastAPI(title="Billing Dispute Investigation Agent")
store.init()
if os.environ.get("AUTO_SEED") == "1" and not store.list_cases():
    store.create_case(json.loads((ROOT / "data" / "sample_case.json").read_text()))

@app.middleware("http")
async def access_log(request: Request, call_next):
    t = time.time(); rid = uuid.uuid4().hex[:8]
    try:
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            resp = JSONResponse({"detail": f"request body too large (max {MAX_BODY // 1000} KB)"}, status_code=413)
        else:
            resp = await call_next(request)
    except Exception as e:
        log.error(json.dumps({"event": "unhandled", "rid": rid, "path": request.url.path, "error": str(e)}))
        return JSONResponse({"detail": "internal error", "rid": rid}, status_code=500)
    for k, v in SEC_HEADERS.items(): resp.headers[k] = v
    log.info(json.dumps({"event": "request", "rid": rid, "method": request.method, "path": request.url.path, "status": resp.status_code, "ms": round((time.time() - t) * 1000)}))
    return resp

class NewCase(BaseModel):
    payload: dict
class Evidence(BaseModel):
    patch: dict
    note: str = Field(min_length=3)
class FindingAction(BaseModel):
    status: str = Field(pattern="^(accepted|rejected|edited)$")
    statement: str | None = None
    actor: str = "reviewer"
class Credit(BaseModel):
    amount: str
    reason: str = Field(min_length=3)
    idempotency_key: str = Field(min_length=6)
    actor: str = "reviewer"

def _guard(fn, *a):
    try: return fn(*a)
    except KeyError: raise HTTPException(404, "not found")
    except ValueError as e: raise HTTPException(409, str(e))
    except ArithmeticError: raise HTTPException(422, "invalid number")

@app.get("/api/health")
def health(): return {"ok": True, "llm_configured": bool(os.environ.get("ANTHROPIC_API_KEY"))}

@app.get("/api/sample")
def sample(): return json.loads((ROOT / "data" / "sample_case.json").read_text())

@app.get("/api/cases")
def cases(): return store.list_cases()

@app.post("/api/cases")
def create(body: NewCase):
    p = body.payload
    for k in ("invoice", "rules", "usage"):
        if k not in p: raise HTTPException(422, f"payload missing '{k}'")
    try: validate_case(p); recalc(p)
    except Exception as e: raise HTTPException(422, f"evidence not calculable: {e}")
    return {"id": store.create_case(p)}

@app.get("/api/cases/{cid}")
def get_case(cid: str): return _guard(store.get_case, cid)

_hits: dict[str, list[float]] = {}
@app.post("/api/cases/{cid}/investigate")
def investigate(cid: str, request: Request):
    _guard(store.latest_evidence, cid)
    ip = request.client.host if request.client else "?"
    w = [t for t in _hits.get(ip, []) if time.time() - t < 60]
    if len(w) >= int(os.environ.get("INVESTIGATE_PER_MIN", "30")):
        raise HTTPException(429, "too many investigations; wait a minute (protects the LLM budget)")
    _hits[ip] = w + [time.time()]
    if len(_hits) > 1000:
        for k in [k for k, v in _hits.items() if not v or time.time() - v[-1] > 60]: _hits.pop(k, None)
    return agent.run(cid)

@app.post("/api/cases/{cid}/evidence")
def add_ev(cid: str, body: Evidence):
    return {"version": _guard(store.add_evidence, cid, body.patch, body.note)}

@app.post("/api/cases/{cid}/findings/{fid}")
def finding(cid: str, fid: str, body: FindingAction):
    _guard(store.set_finding, cid, fid, body.status, body.actor, body.statement)
    return {"ok": True}

@app.post("/api/cases/{cid}/request-info")
def request_info(cid: str, body: dict):
    _guard(store.latest_evidence, cid)
    q = str(body.get("question", "")).strip()
    if len(q) < 3: raise HTTPException(422, "question is required")
    store.log_decision(cid, body.get("actor", "reviewer"), "info_requested", {"question": q})
    store.set_status(cid, "awaiting_info")
    return {"ok": True}

@app.post("/api/cases/{cid}/credit")
def credit(cid: str, body: Credit):
    return _guard(store.approve_credit, cid, body.amount, body.reason, body.actor, body.idempotency_key)

@app.get("/")
def index(): return FileResponse(ROOT / "static" / "index.html")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
