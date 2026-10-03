# Dispute Desk — Billing Dispute Investigation & Resolution Agent

Investigates a disputed invoice from supplied evidence (invoice lines, contract rules, usage events, adjustment history, customer complaint). **All money math is deterministic Python (`Decimal`)**; an optional LLM only *interprets* results, and every claim must cite real IDs or it is discarded. A human reviewer approves everything.

## Run
```bash
pip install -r requirements.txt
cp .env.example .env            # optional: set ANTHROPIC_API_KEY for AI interpretation
uvicorn app.main:app --port 8000   # open http://localhost:8000, click "Load sample case"
pip install -r requirements-dev.txt
pytest -q --cov=app             # ~110 tests, ~98% coverage (incl. property-based + fuzz)
```
Docker: `docker build -t dispute-desk . && docker run -p 8000:8000 -e ANTHROPIC_API_KEY=... -v $PWD/data:/data dispute-desk`

## Architecture
```
UI (static/index.html) -> FastAPI (app/main.py) -> SQLite (app/store.py)
                               |
                         app/agent.py  (state graph)
 gather -> calculate(engine.py, deterministic) -> interpret(LLM, retry once) -> verify citations -> [fallback: rule-based] -> options
```
- **Calculations vs interpretation are stored separately**: `calculations` table (engine output) vs `findings` table (`source` = `rules` | `llm`).
- **Duplicate-credit prevention**: unique `idempotency_key` on `adjustments`, plus cap = recalculated overbilling − prior credits (computed by the engine at approval time).
- **Partial tool failure**: if calculation fails the run stops and reports it (no guessed numbers); if the LLM fails/times out/returns ungrounded output, the run falls back to rule-based findings and the UI says so.
- **Reopen + staleness**: new evidence creates evidence version N+1, status `reopened`; findings and calculations from older versions are flagged *stale* until the investigation is re-run.
- **Audit trail**: `decisions` (every accept/edit/reject/credit/info-request), `evidence` (all versions), `agent_runs` (node trace). Structured JSON logs to stdout.

## Testing
~110 tests, ~98% line coverage, repeated runs stable. Layers:
- **Requirement traceability** (`test_requirements.py`): one group per requirement of the brief.
- **Property-based** (Hypothesis, `test_properties.py`): for any generated evidence, totals reconcile, money always has 2 decimals, creditable <= overbilled, event order never changes money, duplicates never add quantity, held/missing/failed lines never produce credit, and credits through the API never exceed the cap.
- **Fuzzing**: random JSON to case creation, evidence patches, ids, amounts and keys must never produce a 5xx (this found a real crash on `{"usage": null}`).
- **Security** (`test_security.py`): security headers/CSP, 413 on oversized bodies, SQL-injection and path-traversal inertness, stored-XSS escaping audit of every dynamic UI expression, no error-detail leakage, WAL mode.
- **Concurrency**: threaded same-key credits, cap races, concurrent evidence versions.
- **Mutation testing** (`python scripts/mutation_check.py`): injects 29 deliberate bugs into a temp copy; 28/29 are caught. The survivor ("tier boundary off-by-one") is an *equivalent mutant* - at exactly the cap the next tier adds nothing, so behaviour is identical. The first run caught only 23/29; the gaps (negative allowance, exact-cap credit, partially-grounded citations, finding replacement, rate-limit boundary) became `tests/test_mutation_gaps.py`.
After deploying: `python scripts/smoke.py https://<your-url>` runs the reviewer flow against the hosted app and reports whether the AI path was used.

### Bugs found by adversarial probing (all fixed, all regression-tested)
1. Metered line with *no* usage events was treated as zero usage and would credit the whole line -> now `MISSING_USAGE`, held at billed amount, asks for evidence.
2. Duplicate-event removal depended on lexicographic id order -> originals now always win.
3. Credits already listed in evidence history were ignored by the cap -> cap is net of them.
4. Idempotency key was global -> scoped per case; same key + different amount is a 409.
5. Re-running an investigation duplicated findings -> de-duplicated per evidence version.
6. `Infinity`/`NaN`, negative rates, discount >100% accepted -> rejected with clear errors.
7. One failing line aborted the whole recalculation -> isolated (`CALC_ERROR`), other lines still calculated, never credited.
8. UI showed a stale "creditable" figure after crediting -> now net of credits made in-app; credits made on older evidence are flagged.
9. Ambiguous-clause differences were counted as creditable -> held out and reported as `unresolved_ambiguous` for human interpretation.
10. Zero amounts serialised as `"0"` not `"0.00"`.
11. `{"usage": null}` (and other wrong-typed evidence patches) crashed the API with a 500 -> strict patch validation, found by fuzzing.
12. No security headers, no request-size limit, SQLite not in WAL mode, and the rate limiter would have treated every user behind the host's proxy as one IP -> fixed (CSP etc., 413 over 2 MB, WAL, `--proxy-headers`).

## Scope
Done: evidence intake, tiered/per-unit/flat/discount/minimum rules, duplicate/out-of-period/test usage detection, original vs recalculated comparison, findings review (accept/edit/reject), mock credit, request info, reopen, stale flags, history, tests.
Excluded (by brief): real payments, accounting integrations, tax advice, automatic adjustments. No auth (reviewer name is a free-text actor) — add SSO before real use.

## Limitations
No authentication: anyone with the URL can see and act on all cases (fine for a review demo; add SSO/roles and per-tenant scoping before real use). CSP allows inline scripts because the single-file UI uses them; all dynamic values are escaped (audited by a test). Rule types are limited to flat/per-unit/tiered; ambiguous contract terms are *flagged for a human*, never resolved by the AI. SQLite is single-node. Net over-billing is credited (under-billed lines offset over-billed ones); finance may want per-line policies. LLM output is only verified structurally for citations, not for factual quality.

## Deployment
**Render (free, Docker):** push to GitHub -> Render > New > Blueprint -> select repo (uses `render.yaml`) -> set `ANTHROPIC_API_KEY` in the dashboard. Free-tier disk is ephemeral and the service sleeps when idle, so the demo case is re-seeded on boot (`AUTO_SEED=1`); open the URL once before review to warm it up. For persistence use a paid disk at `/data`.

Render/Railway/Fly: deploy the Dockerfile, set `ANTHROPIC_API_KEY`, mount a volume at `/data`. Deployed URL: _<add before submitting>_. Reviewer steps: open URL → *Load sample case* → *Investigate* → review findings → *Approve credit* (try twice to see idempotency) → add evidence JSON to see stale flags.
