# Agent usage

**Tools:** Google Antigravity IDE, Claude / Anthropic API, pytest as the verification loop, uvicorn, git, and GitHub CLI.

**Research inputs reviewed:** addyosmani/agent-skills (spec -> plan -> build -> verify -> review workflow, TDD, security/observability checklists), VoltAgent/awesome-design-md (DESIGN.md architecture and state modeling), Egonex-AI/Understand-Anything (deterministic parse + LLM semantics split - borrowed as the "deterministic facts, LLM meaning" principle), Owl-Listener/designer-skills (error-flow / state-mapping guidance for the UI states).

**Representative prompts**
1. "Pick the best of two assessment problems, then design a deterministic billing engine + agent graph where the LLM can never change money."
2. "Write the SQLite store so credits are idempotent and capped by the recalculated overbilling, and new evidence marks older findings stale."
3. "Write pytest cases for tiered pricing, duplicate/out-of-period events, idempotent credit, stale findings, and calculation failure."

**Delegated to the agent:** engine, store, agent graph, API, UI, tests, docs draft.

**Mistakes / rejected suggestions (real, from this build)**
- Shell scaffold failed because `mkdir -p dir/{a,b}` is not supported by `/bin/sh`; caught from the error output and re-run with explicit paths.
- First draft of the sample data had a duplicate event with a different quantity than its original (inconsistent evidence); corrected so the duplicate shares a `dedupe_key`.
- Chose *not* to let the LLM compute or suggest credit amounts; amounts come only from the engine. Chose *not* to auto-resolve the ambiguous storage rule.
- A first test expected net overbilling of 400 when under-billing offset it; the engine's 200 was correct (my arithmetic was wrong), so the test was fixed, not the code.
- The first credit implementation had a check-then-insert race and allowed credits on stale calculations; found by writing concurrency/staleness tests, fixed with `BEGIN IMMEDIATE` + stale checks.
- **Adversarial pass (second iteration):** instead of trusting my own tests, I probed the app against each requirement of the brief and found 10 real defects (see README). The worst: missing usage data was treated as zero usage, which would have credited a whole $9,500 line. Each defect got a failing regression test first.
- My patch to `validate_case` initially broke every test (helper used before definition) and the first exception handler I wrote was convoluted; I caught both from the failing run and rewrote it simply.
- Two new failures were mistakes in my own tests (notes shorter than the API's 3-char minimum), not code bugs; I fixed the tests, not the code. One failure was a genuine design flaw (ambiguous clause amounts counted as creditable) and I changed the code.
- **Third iteration (advanced review):** added property-based tests, fuzzing, a security review and mutation testing. Fuzzing found a 500 on a null evidence patch; the security review found missing headers/limits and a proxy-IP rate-limit flaw; the first mutation run killed 28/29 injected bugs, and with targeted mutation tests, reached 29/29 killed. Two new property tests were wrong (too strict on flags; non-JSON floats), which I fixed in the tests, not the code.
- **Frontend data mapping defect:** In `static/index.html`, the UI script looked for `d.calc` instead of `d.calculation` returned by the `/api/cases/:id` endpoint. As a result, the comparison table stayed on "Not investigated yet" even when findings were shown. Diagnosed by checking the API JSON payload against the DOM render handler, fixed by referencing `d.calculation || d.calc`.
- **Security test split collision during design upgrade:** When elevating the UI to the Together AI design system with modular helper functions, an auxiliary function was named `renderCaseList`. This caused `test_stored_xss_payload_is_data_only_and_ui_escapes_every_dynamic_field` (which splits `index.html` on `function render`) to split prematurely and flag a non-escaped ternary. Renamed to `displayCases` to maintain strict boundary isolation for the AST security audit, keeping all 115 tests green.

**Verification:** `pytest -q` (115 tests incl. Hypothesis + fuzz, 99% coverage; mutation check 29/29 killed) covering money math, validation, idempotency incl. concurrent races, cap enforcement, staleness, failure handling, citation grounding, mocked LLM paths; live end-to-end run against a running server; manual run of the sample case through the UI; server smoke test (`scripts/smoke.py`).
