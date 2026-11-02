# OpsPilot: agentic AI operations assistant

*Take-home submission for the Forward Deployed Engineer role: Rishabh Kumar Raizada.*

OpsPilot takes a retail operations request (an email, a pasted note or an uploaded `.eml`) and carries it to closure:

1. Extracts the facts, deadlines and dependencies.
2. Checks them against inventory, purchase orders and the store calendar.
3. Assesses urgency, impact and risk.
4. Proposes costed options with rationale.
5. Routes the decision to the person authorised to make it, and escalates when nobody responds.
6. Lets people correct or override any AI output.
7. Drafts stakeholder communications and management summaries.
8. Records every AI, human and system action in a tamper-evident audit trail.

| | |
|---|---|
| **Live app** | https://92-4-93-133.sslip.io |
| **Demo password** (all accounts) | `Demo@2026`. The login page has one-click role pickers. |
| **Docs** | [`docs/PRD.pdf`](docs/PRD.pdf) · [`docs/OpsPilot-deck.pptx`](docs/OpsPilot-deck.pptx) |

| Account | Role | Can do |
|---|---|---|
| analyst@opspilot.demo | Ops analyst (central desk, all regions) | Triage, correct facts, choose options, send comms. Cannot approve spend |
| manager.west@ · manager.north@ · manager.south@ | Ops manager | Approve up to ₹50,000 in own region |
| head.west@ · head.north@ · head.south@ | Regional head | Approve up to ₹5,00,000 in own region |
| director@opspilot.demo | Director, finance | Approve any amount, all regions |
| auditor@opspilot.demo | Internal audit | Read-only |
| admin@opspilot.demo | Admin | Policy, fault injection, demo clock, reset |

All accounts are `@opspilot.demo`.

---

## A two-minute tour

1. Sign in as **Ananya (analyst)**. Go to **Requests → Simulated inbox** and deliver **"Urgent Delivery Delay – Store Launch"**. This is the assignment's sample email.
2. Watch the **agent timeline** fill in: Guard → Intake → Reconcile → Risk → Planner (which calls tools) → Reviewer → Comms.
   - The source email is highlighted where each fact came from.
   - The system finds that the PO is from Vertex Fixtures (the email never says so), that the store calendar launch is in 6 days, that the shortfall is 380 units, and that the revenue at risk is ₹56 lakh.
3. **Options & approval** shows three costed options built from tool results. Each one says who must approve it and why. Choose one; the ₹1.42 lakh option routes to the **Regional head**.
4. Sign in as **Vikram (North manager)** and open Approvals. Approving is refused with a reason, and the attempt is audited. Sign in as **Kavita (West regional head)** and approve or reject.
5. Execute the actions, send the drafted comms, close the request, and generate the **management summary** under Analytics.

The edge cases each have their own simulated email or admin control. See [Edge cases](#edge-cases-how-each-is-detected-communicated-recovered-and-recorded) below.

---

## Architecture

```
React SPA (Vite) ──HTTPS──> Caddy ──/api──> FastAPI (single process)
                                              ├─ Orchestrator: explicit state machine over a shared fact ledger
                                              ├─ 7 PydanticAI agents (typed I/O) ──> Gemini 3.5 Flash-Lite → 3 Flash → 3.5 Flash → 3.6 Flash → Groq gpt-oss-120b
                                              │     → deterministic rules if both fail
                                              ├─ Deterministic services: policy, risk rules, reconciler, grounding,
                                              │     confidence, dedup, outbox, SLA monitor, audit hash-chain
                                              ├─ Mock enterprise systems: inventory, POs, supplier quotes,
                                              │     transfer lanes, store calendar (with circuit breakers + chaos flags)
                                              └─ SQLite (WAL) via SQLAlchemy (Postgres-ready)
```

### Agents vs deterministic code

The LLM reads, interprets and proposes. Code computes, checks, authorises and routes.

| Agent | What it does | Contract |
|---|---|---|
| Guard | Classifies inbound text for prompt injection or manipulation. It sees only datamarked text and is combined with regex heuristics | `GuardVerdict` |
| Intake | Splits multi-issue emails and extracts each field with status, confidence and an **exact evidence quote** | `IntakeResult` |
| Correlation | Decides whether a new message duplicates an open case, updates it, or is new. It is shown candidates retrieved by code | `Correlation` |
| Risk & impact | Urgency, risk level and impact, citing fact refs | `RiskAssessment` |
| Planner | Calls **read-only tools** (inventory, transfer lanes, alternate suppliers, expedite quotes, POs) and proposes 2–3 costed options | `Plan` |
| Reviewer | Critiques the plan against the tool results, with at most one revise loop back to the Planner | `Review` |
| Comms / Reporter | Draft stakeholder emails and management summaries from verified facts and pre-computed numbers | `Draft`, `Report` |

Deterministic services:
- **Orchestrator:** a state machine with illegal transitions rejected.
- **Approval policy:** amount bands, region scope, approval limits, self-approval ban.
- **Risk rules engine:** an independent second opinion to the Risk agent.
- **Reconciler:** compares email claims with system data.
- **Grounding check:** a quote must be a substring of the email.
- **Date and money normalisation.**
- **Confidence calculator, dedup retrieval, SLA monitor, outbox with retry and dead-letter, audit hash chain, circuit breakers.**

### How the agents coordinate and share context
- **The agents never call each other.** The orchestrator builds a minimal context pack for each one from the case's **fact ledger** (`F1…Fn`: value, source, status, confidence and evidence; AI and human values stored side by side).
- Every claim must cite fact refs (`F#`) or tool results (`T#`). Output validators reject unknown refs, ungrounded quotes, budget breaches and repeated rejected options, raising `ModelRetry` so the model corrects itself.
- **Every invocation is stored** as an `AgentRun`: prompt version, model actually used, fallback flag, input hash, tool calls, validation retries, tokens and latency.
- **A human correction bumps the ledger version.** Only the downstream agents (risk, planner, reviewer) re-run, and approvals bound to the old facts are withdrawn.

### When human approval is mandatory
Approval is mandatory if any of these holds:
- The option commits spend (> ₹0).
- Risk is high or critical.
- It changes a customer commitment or a supplier order.
- Composite confidence is below 0.75.
- Injection is suspected.
- The case ran in degraded mode.

The amount decides the approver:

| Amount | Approver |
|---|---|
| ≤ ₹50k | Ops manager |
| ≤ ₹5L | Regional head |
| > ₹5L | Director |

Further rules:
- The approver must be in the request's region and within their limit, and cannot be the person who submitted it.
- Every outbound stakeholder email needs a human click.
- **No agent has an approve, send or spend capability.**

### How confidence is determined
The model's self-reported confidence is only one input:

```
field  = min(model confidence, grounding)   grounding: verbatim quote 1.0 · inferred 0.6 · ungrounded 0.3 · missing 0
overall = extraction × risk-agreement(agent vs rules) × reconciliation(0.7^conflicts) × reviewer verdict × citation validity
          capped by planner confidence + 0.15, and at 0.55 in degraded mode
```

The bands are high (≥ 0.75), medium, and low (< 0.5). At low, the AI option cannot be submitted without a written justification. The UI shows the breakdown. These are heuristic thresholds, calibrated on the scenario set, not statistically.

### Unsupported or incorrect AI output
Seven layers:
1. Schema validation with retries.
2. Grounding and citation validators.
3. Confidence caps.
4. A Reviewer critic loop.
5. A rules engine cross-check, where a gap of two or more levels becomes a blocking "agents disagree" issue.
6. Human override with a mandatory reason; the AI value is preserved.
7. A fallback model, then deterministic degraded mode.

Nothing executes automatically.

### Monitoring and audit
- The `agent_runs` trace for each request is shown in the timeline with inspectable input and output.
- **Analytics → AI quality** covers fallback rate, self-correction rate, human-override rate, ungrounded-extraction rate, AI-option acceptance, per-agent latency and failures, and estimated cost.
- `audit_events` is **append-only** (DB triggers block UPDATE and DELETE) and **SHA-256 hash-chained**. Use *Verify chain* or `GET /api/audit/verify`, and export it as CSV.
- `/api/readyz` reports breaker states, chaos flags, the SLA monitor heartbeat and chain integrity.

---

## Edge cases: how each is detected, communicated, recovered and recorded

| # | Scenario (simulated inbox key or control) | Detection → communication → recovery → record |
|---|---|---|
| 1 | Missing information (S3) | Required-field rules per category flag critical gaps. The case moves to *Waiting for info* with the SLA paused, and the Comms agent drafts a clarification email. When the requester replies or a person fills the fact, analysis resumes. Recorded as issue events in the audit log |
| 2 | Conflicting information (S4) | The reconciler compares email and inventory (150 vs 100) and opens a blocking conflict card with both sources. A person picks one, it becomes a recorded override, and the case re-runs |
| 3 | Duplicate requests (S2 after S1) | An exact content hash, or entity and fuzzy retrieval followed by the Correlation agent. The duplicate is merged into the original, the requester is added and the owner notified (`case.linked`) |
| 4 | Unclear intent / multiple issues (S5) | Intake flags `multi_issue`. A split suggestion creates linked child cases with one click |
| 5 | Incorrect AI extraction (any case, Facts tab) | Edit any field with a reason. The AI value is preserved, and risk, plan and review re-run on the new ledger version (`fact.override`) |
| 6 | Low-confidence recommendation (S9, unknown SKU) | Confidence < 0.5 raises a blocking *insufficient evidence* issue. The AI option cannot be submitted without a justification, and a human-defined option is available |
| 7 | Approval delay (Admin → demo clock) | The SLA monitor sends a reminder 60 min before the due time. At the due time it escalates to the next level and notifies both people. At the top level, or after the request's own deadline, it flags an SLA breach and alerts management |
| 8 | Approval rejection (Approvals → Reject) | A reason is mandatory, with an optional budget cap. The Planner re-plans under the constraint (rejected option excluded, cap enforced by a validator) and a new approval is routed |
| 9 | Changing circumstances (S7 after S1) | Correlation detects an UPDATE. The facts change (delay 10 → 6 days, slip 7 → 3 days), the pending approval is superseded and the approver told, and the case is re-assessed |
| 10 | System or LLM unavailable (Admin → fault injection) | Primary model down: FallbackModel answers, and the timeline shows *fallback model*. All models down: the rules extractor and rules planner run, the case is flagged degraded and needs review. Inventory down: the circuit breaker opens, figures are marked unverified with a confidence penalty, and reprocessing is available |
| 11 | Unauthorized action | Role rank, region scope, approval limit and self-approval are checked in code. A 403 explains why, and the attempt is pinned in the audit trail (`unauthorized_attempt`) |
| 12 | Prompt injection (S6) | Heuristics plus the Guard agent on datamarked text flag *malicious*. The spans are highlighted in red and a blocking review is forced. There is structurally no approve or send tool, so nothing happens |
| 13 | Communication failure (Admin → email failing) | The outbox retries ×3 with backoff, then dead-letters, opens an issue and notifies the owner in-app. *Retry now* or *deliver in-app* recovers it |
| 14 | Contradictory agent outputs | The Risk agent and rules engine are two or more levels apart, so the higher is taken and a blocking "agents disagree" issue needs a human acknowledgement |
| 15 | Request cancellation (S8 after S1) | Correlation detects a cancellation signal. Pending approvals are withdrawn, the case is put on hold with *Cancel / Resume*, and on cancel the open actions are cancelled and status updates drafted |

Every row above has an automated test in `backend/tests/integration/test_workflow.py`.

---

## Run it locally

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), and Node 22.

```bash
cp backend/.env.example backend/.env        # add GOOGLE_API_KEY, or set LLM_FAKE=true for the offline scripted model
make api        # FastAPI on :8100 - creates and seeds SQLite on first start
make web        # Vite on :5173 (proxies /api)
make test       # 34 offline tests (fake LLM, temp DB)
```

To run it in Docker instead: `docker compose up --build`, then open http://localhost:8080.

Deploy with `make deploy`, which builds the SPA, rsyncs to the VM, runs `uv sync`, and restarts the systemd unit and Caddy (see `deploy/`).

### Configuration (`backend/.env`)
- **Model:** `GOOGLE_API_KEY`, `LLM_PRIMARY_MODEL` (default `gemini-3.5-flash-lite`), `LLM_FALLBACK_MODEL` (comma-separated chain), `GROQ_API_KEYS` (comma-separated; each key is its own fallback backend), `GROQ_MODEL`, `LLM_MAX_RUNS_PER_MIN`.
- **Thresholds:** `CONF_HIGH`, `CONF_BLOCK`.
- **Mail:** `MAIL_PROVIDER` (`mock` or `smtp` with `SMTP_*`).
- **Webhook intake:** `INTAKE_WEBHOOK_SECRET` enables HMAC-signed email intake at `POST /api/intake/webhook`.
- **Storage:** `DATABASE_URL` (any SQLAlchemy URL).

---

## Deploying OpsPilot at a customer

I built this the way I would run a customer engagement:
- Start from how the ops team works today: email, spreadsheets, chasing approvers.
- Automate the legwork.
- Keep authority and money with the people and rules they already trust.
- Isolate every integration point so it can be swapped for the customer's real systems.

| Phase | What happens | Exit criterion |
|---|---|---|
| **0. Discover** (week 0) | Shadow the ops desk, collect 50–100 real requests, map the real approval matrix, regions and limits, baseline time-to-decision and missed deadlines | Signed-off workflow map, policy table and baseline |
| **1. Integrate** (weeks 1–2) | Ops mailbox → `POST /api/intake/webhook` (Microsoft Graph or Gmail push); replace the mock adapters with read-only ERP/WMS/PO APIs or the team's existing stock sheets; SSO; load the policy in Admin | Real requests flow in; adapters pass the existing tests |
| **2. Shadow** (week 3) | Agents analyse every request while people work as today; compare the AI's output with what people did; tune prompts, rules and thresholds | ≥ 90% field accuracy on real mail |
| **3. Assisted pilot** (weeks 4–6) | One region decides in OpsPilot; weekly override and rejection review with the ops lead | Pilot targets met two weeks running |
| **4. Scale & hand over** | Region-by-region rollout, Teams/Slack approvals, Postgres + job queue, runbook and admin training | The customer's team operates it |

| Customer-specific piece | Where it plugs in | Code change? |
|---|---|---|
| Approval matrix, limits, SLA hours, confidence thresholds | Admin → settings (audited) | No |
| Inbound email | `POST /api/intake/webhook` (HMAC-signed) | No |
| Inventory / PO / supplier / store calendar | `backend/app/integrations/systems.py`, one adapter per system with a circuit breaker | Adapter only |
| Outbound email | `integrations/mail.py` (`MAIL_PROVIDER=smtp` or a provider API) | Config / small adapter |
| LLM provider and models | `LLM_PRIMARY_MODEL`, `LLM_FALLBACK_MODEL`, `GROQ_*` env | No |
| Agent behaviour | Versioned prompts in `backend/app/agents/prompts/*.md`; every run records its prompt version | Prompt edit |

`backend/scripts/live_eval.py` and `backend/scripts/live_flows.py` replay the scenario set and the human-in-the-loop flows against the configured model. These are the regression checks I would run after every prompt or model change during a deployment.

---

## Technology choices and trade-offs

| Choice | Why | Trade-off |
|---|---|---|
| **PydanticAI**, not LangGraph or CrewAI | Typed outputs with validator-driven retries, `FallbackModel`, tool calling, and offline `FunctionModel` tests. Approvals can wait hours, so they are database rows with a due time rather than paused graph threads | No graph DSL; the orchestration is plain Python (explicit and testable) |
| **Deterministic orchestrator + state machine** | Every transition is audited and illegal moves are rejected. Money, authority and routing never depend on a model | Less "autonomous" by design |
| **Gemini 3.5 Flash-Lite first, larger Gemini models and Groq as fallbacks, then rules** | Cost-optimised: the fast model handles the routine path (S1 in about 25 s); each fallback has its own quota pool, so outages and rate limits roll over; a deterministic floor | Lite models are weaker at long reasoning. The Reviewer agent and the validators compensate, and any backend can be promoted to primary by an env change |
| **SQLite (WAL)** | The demo VM has 1 GB RAM. One file, zero ops | Single writer. The code is dialect-neutral, so switching to Postgres means changing `DATABASE_URL` |
| **In-process SLA monitor and worker** | No Redis or Celery needed for a prototype | Single instance. A queue (e.g. Postgres `SKIP LOCKED` or Temporal) is the scale path |
| **Mock enterprise systems** | Deterministic, explainable demo data, with fault injection | Real ERP/WMS adapters plug in behind `integrations/systems.py` |
| **Vite SPA served by Caddy** | No Node runtime on the box; automatic HTTPS via sslip.io | No SSR (not needed for an authenticated internal tool) |

Research that informed the design: Netflix Dispatch's case model, Keep's side-by-side AI and human fields, LangChain's agents-from-scratch HITL loop and agent-inbox approval schema, oneneural/supplier-disruption's propose-then-verify pattern, and Microsoft's Spotlighting (datamarking) defence against prompt injection.

## Repository map
```
backend/app/agents/         contracts.py (agent I/O), definitions.py (agents, tools, validators), prompts/*.md (versioned)
backend/app/orchestrator/   pipeline.py (agent coordination), workflow.py (HITL ops, approvals, escalation), states.py
backend/app/services/       policy, risk_rules, confidence, ledger, injection, parsing, degraded, outbox, audit, reporting
backend/app/integrations/   llm.py (model chain + chaos), systems.py (mock ERP/WMS), mail.py, breaker.py, fake_llm.py
backend/tests/              unit + integration (one test per edge case)
frontend/src/pages/         Dashboard, Requests, RequestDetail, Approvals, Communications, Audit, Analytics, Admin
deploy/                     Caddyfile, systemd unit, deploy.sh
```
