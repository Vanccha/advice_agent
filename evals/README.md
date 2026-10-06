# Evaluation harness

`evals/` proves, repeatably, that the AI support assistant behaves correctly against the
**real** `Orchestrator` (`assistant/modes/orchestrator.py`), a `ScriptedProvider` (no API
key anywhere in this harness), and a **live** `ToolGateway` pointed at the tenant's real MCP
adapters (`mcp-core`, `mcp-payment`, `mcp-ticketing`, `mcp-monitoring`, `mcp-notification`)
— never a `FakeGateway`. Chaos scenarios are injected by shelling out to the company's own
`chaos.cli` (never imported in-process: `evals/` must not import `company`).

The two datasets (`evals/datasets/scenarios.yaml`, `evals/datasets/advisory_profiles.yaml`)
are written and owned by the orchestrator agent and are **inputs only** — this harness
reads them, never edits them.

## Running

```bash
make eval                                                   # full run, writes a report
docker compose run --rm test-runner python -m evals.run --report evals/reports
docker compose run --rm test-runner python -m evals.run --only advisory --report evals/reports
docker compose run --rm test-runner python -m evals.run --only scenarios --case double_charge
docker compose run --rm test-runner python -m evals.run --no-chaos --only advisory
```

Flags:
- `--only scenarios|advisory` — run one suite only.
- `--case <id>` — run a single scenario id / profile id only (matched against whichever
  suite(s) are selected).
- `--no-chaos` — skip chaos injection entirely. Every scenario case is then reported as
  `ERROR` ("chaos injection skipped") rather than silently passing — this flag exists for a
  quick advisory-only loop (`--no-chaos --only advisory`), not to make the scenario suite
  green without chaos. The three `no_action_cases` never touch chaos and always run.

Exit code is `0` only when every case `PASS`ed; any `FAIL` or `ERROR` exits non-zero, so
`make eval` is usable as a CI gate.

`EVAL_MODE=scripted` (the default, and the only value exercised by this environment) needs
no API key — a `ScriptedProvider` loaded with one exact-match fixture per conversation's
*first* message answers the only `DecisionService` call either suite's code paths actually
make (`classify_intent`, in `modes/router.py`; see "What is — and isn't — exercised" below).
`EVAL_MODE=live` routes through `llm.factory.get_provider()` instead (the real configured
provider) — that code path exists in `evals/wiring.py` but was **not run** in this repo: no
`OPENAI_API_KEY`/`ANTHROPIC_API_KEY` is configured here.

## What each suite checks

### Scenario suite (`evals/datasets/scenarios.yaml`)

For each of the six chaos scenarios, every tone variant (polite/angry/vague) is injected
and sent as one message to a brand-new conversation, then every expectation key present in
that case's `expect:` block is asserted. Injection strategy (see
`evals/scenario_suite.py` module docstring for the full reasoning):

- `stuck_provisioning`, `paid_not_active`, `double_charge`, `missed_installation`: **each**
  tone variant gets its own fresh chaos injection (a new customer/subscription), so one
  variant's fix (e.g. `retry_provisioning_job` actually resolving the stuck job) can never
  contaminate the next variant's test of the same expectation.
- `regional_outage`: injected once (it is a whole-region event); tone variants are sent as
  different affected customers (read from `get_incident_detail`'s `affected_subscriptions`,
  never guessed). An explicit extra check additionally sends **two different** affected
  customers and asserts the incident still has exactly one ticket
  (`find_tickets_by_incident`).
- `payment_down`: injected once (a global PSP outage flag, no customer to pick); all tone
  variants are sent as the same fixed seeded customer (`NS-100001`).

The chaos CLI is reset once after all of a scenario id's variants have run (never between
variants of the *same* id — each already has independent state), and once more at the end
of the whole run.

### Advisory suite (`evals/datasets/advisory_profiles.yaml`)

For each of the ten profiles: builds the `AdvisoryProfile` from the dataset's `profile:`
block, fetches the **live** package catalogue via the `list_packages` tool, and calls the
deterministic `recommendation.engine.recommend_packages` directly (not through the chat
loop) — this is the primary check and is what `expected_best`/`must_not_recommend`/etc. are
asserted against. It also:

- calls `recommend_packages` a second time with the same input and asserts the result is
  byte-for-byte identical (`invariants.deterministic`);
- asserts every `PackageOffer.reasons` string matches one of `routing.yaml:
  recommendation.reason_codes_en`'s templates (built into a regex per template with each
  `{placeholder}` turned into a wildcard) — `invariants.reasons_from_config_only`;
- asserts every returned `package_code` is a code the live catalogue actually has
  (`invariants.no_package_outside_catalogue`), and that `1 <= len(offers) <= 3`
  (`invariants.min/max_offers_returned`).

As a secondary, best-effort check, the profile's `conversation_en` is also played through
the real `Orchestrator`'s ADVISORY mode (padded with a neutral "farketmez" answer if the
scripted conversation runs out of turns before the question budget does) and the verbalised
reply is checked to never name a package outside the catalogue — this does **not** have to
reproduce `expected_best` (the deterministic field parser in `modes/advisory.py` is not an
NLU model and is not expected to recover the exact intended profile from free text), so a
mismatch there does not fail the primary check; it is its own named check
(`conversation_reply_only_catalogue_packages`).

## Reading a report

Console output groups cases by suite, prints `PASS`/`FAIL`/`ERROR` per case (a `FAIL` also
prints every failing expectation's expected-vs-actual value), then per-suite and overall
totals. `evals/reports/<timestamp>-report.md` has the same information as a table plus a
"Failures in detail" section; `<timestamp>-report.json` is the same data, machine-readable,
for tooling. Both carry a header: provider mode (`EVAL_MODE`), tenant, and
`git rev-parse --short HEAD` at run time.

`ERROR` is distinct from `FAIL` on purpose: it means the harness itself could not get an
answer (chaos CLI failed, an MCP adapter was unreachable, the orchestrator raised) — this is
never folded into a silent pass, and never presented as if the assistant had been judged.

## Interpretation of ambiguous expectation keys

- **`must_not_blame_customer`**: asserts the reply does not contain any of a small
  blame-phrase list (`evals/checks.py: BLAME_PHRASES_EN`) pinning the fault on the
  customer's own card/device/input (e.g. "a problem with your card", "caused by
  your device"). This is necessarily a judgement call for a string-matching harness; the
  list is a module-level constant specifically so it's easy to find and extend.
- **`action_executed` / `action_executed_any`**: read from `asst.action_records`
  (`core_common.models.ActionRecord`, written by `policy.executor.ActionExecutor`) filtered
  by `conversation_id` and `executed=True` — the authoritative record of what actually ran,
  rather than parsing the reply text for a label.
- **`policy_denied_action`**: an `ActionRecord` for that `conversation_id` with
  `action_name` equal to the expected value and `policy_allowed=False`.
- **`ticket_department` / `ticket_department_if_created`**: fetched from the live
  ticketing adapter (`get_ticket`) by the `ticket_key` recorded in `asst.ticket_links` for
  this conversation — the department as the ticketing system itself has it, not as inferred
  from the reply. `..._if_created` only asserts when a ticket actually exists (it is a
  conditional expectation: `payment_down`'s ticket is optional per contracts §5f).
- **`ticket_evidence_must_include_payment_ids`**: the fetched ticket's
  `evidence.record_ids.payment_ids` is a non-empty list of **at least two** ids (a double
  charge's whole point is two payments, not one).
- **`ticket_evidence_must_include_appointment_id`**: the fetched ticket's
  `evidence.record_ids.appointment_id` is present and non-null.
- **`ticket_must_list_attempted_steps`**: the fetched ticket's `attempted_steps` is a
  non-empty list.
- **`incident_ref_required`**: the turn's `Diagnosis.incident_no` is non-null (the
  `TurnResult.diagnosis` the orchestrator returns, not a second lookup).
- **`max_tickets_for_incident`**: after sending two distinct affected customers' messages
  for the same regional incident, `find_tickets_by_incident(incident_no)` returns at most
  that many tickets (in practice exactly 1).
- **`must_not_execute_action`**: no `asst.action_records` row for this conversation has
  `executed=True` — smalltalk/out-of-scope/status-query must never reach `ActionExecutor`
  at all, not merely "not mention" an action in the reply.
- **`reply_must_mention_any_en` / `reply_must_not_mention_any_en`**: case-insensitive
  substring match against `TurnResult.reply_en` (the last turn's reply — every scenario
  case in this dataset resolves in a single turn; see below).
- **`min_down_mbps` / `max_monthly_price_gbp`** (like `must_have_static_ip`/`must_have_tv`/
  `must_have_no_commitment`): checked against the **best** (top-ranked) offer only, not
  every item in the returned top-3. A top-3 list legitimately includes a cheaper/slower
  comparison alternative alongside the best recommendation — `must_not_recommend` is the
  key that bans a package from the list *entirely*; these floors/ceilings describe what is
  actually recommended. (Checking every returned offer against these floors was tried first
  and produced false failures for `crowded_household`/`small_business_from_home`, where a
  legitimately-weaker alternative package appeared alongside a correct best pick.)
- **`acceptable_best`** (present in the advisory dataset's docstring, not currently used by
  any profile): not implemented as a separate relaxation, since no profile's `expect:` block
  actually sets it — if one is added, it should be read as "pass if `expected_best` **or**
  `acceptable_best` is the top offer."

## Why (almost) every scenario case is a single turn

Tracing `assistant/modes/action.py::handle_action`: every root cause this harness's
scenarios produce resolves — success or policy denial + ticket — inside the **same**
`handle_message()` call that ran `DIAGNOSTIC` immediately followed by `ACTION`
(`modes/orchestrator.py`'s dispatch loop `continue`s between modes within one turn). So
`TurnResult.diagnosis`, `TurnResult.reply_en`, and any ticket/action already reflect the
full outcome after the very first message for `stuck_provisioning`, `paid_not_active`,
`double_charge`, `missed_installation`, and `payment_system_down`.

`regional_outage` is the one exception: the same first turn attaches the customer to the
incident ticket *and* attempts the (confirmation-required) `apply_outage_credit` goodwill
credit, which — for a customer not already credited in the last 30 days, i.e. every fresh
affected customer this harness sends — raises `ConfirmationRequired` and leaves the
conversation at `AWAITING_APPROVAL`. That is not one of the dataset's `final_mode` values,
so the harness resolves it (`Orchestrator.resolve_approval(granted=False)` — denied, so no
real monetary side effect reaches the live payment system) and uses the mode *after*
resolution as `final_mode`, while every other check (`issue_type`, `scope`, the ticket
itself, the reply text) still reads the **first** turn's `TurnResult` — the incident
ticket and the substantive reply both already exist by then; only the conversation's
terminal mode needed a second turn to materialise.

## What is — and isn't — exercised

`modes/router.py` calls `DecisionService.classify_intent` once per conversation (its first
message). `modes/action.py` **also** calls `classify_issue_type` and `assess_urgency` for
every root cause that reaches its `_ROOT_CAUSE_ACTION` mapping — every scenario in this
dataset except `regional_outage`, which resolves through a separate branch
(`ensure_incident_ticket` + `apply_outage_credit`) before ever reaching that mapping.
`choose_department` is only called when a denied action's policy entry has **no**
`escalate_to` — tracing `config/tenants/netswift/policy.yaml` shows every denied action this
dataset's diagnoses reach (`issue_refund`, `reschedule_installation`,
`repair_infrastructure`) has one, so `exc.decision.escalate_to or _decide_department(...)`
short-circuits before the call would happen: `choose_department` is **not reachable** by
the current dataset/policy combination (confirmed by reading the code, not assumed).

`evals/fixtures.py` builds fixtures for all three anyway:
- `classify_intent`: one `exact=True` rule per conversation's first message (masked with
  the assistant's own `privacy.masking.mask_text`, so it always matches exactly what the
  router sees).
- `classify_issue_type` / `assess_urgency`: one regex rule per scripted root cause, matched
  on the literal `'root_cause': '<value>'` substring their prompt always contains (the
  prompt here is **not** the user's message — `modes/action.py::_decision_context` builds
  it from `Diagnosis`/`evidence`, which contain chaos-run-specific ids, so matching on the
  user's text would not be stable run to run).
- `choose_department`: one generic fixture, documented above as present-but-unreachable.

**One root cause, `payment_system_down`, is deliberately left unscripted** for
`classify_issue_type`/`assess_urgency`, so this run also exercises
`decision.fallback.apply_confidence_floor`'s confidence-0.0 → deterministic-table fallback
end to end, not only the confident path every other scenario takes. Each scenario case's
report carries an informational `decision_paths_exercised` check (always recorded as
passed — it never affects PASS/FAIL) naming exactly which path (`"confident"` or
`"fallback"`) each `DecisionService` call took that turn, read from the audit trail's
`evidence.escalated` flag.

### Honest gaps (not covered by this harness)

- **Multi-turn approval flows** (`AWAITING_APPROVAL` → granted/denied) are not exercised by
  the scenario suite, because no case in the current dataset requires resolving one to
  observe its expected behaviour (see above). `Orchestrator.resolve_approval` exists and is
  unit-tested elsewhere (`assistant/modes/tests/`), just not by this harness.
- **`EVAL_MODE=live`** (a real `LLM_PROVIDER`) is wired but not run here — no API key is
  configured in this environment.
- **Ticket webhook delivery / `POST /webhooks/ticket`** and the notification-hub channel
  messages (`post_department_message` for `payment_down`) are not independently asserted —
  this harness checks the ticket and the assistant's own state, not the downstream webhook
  fan-out or the channel UI.
- **Audit hash-chain integrity** (`AuditLog.verify_chain`) is not checked here — that is a
  property of `assistant/audit/`, already covered by `assistant/audit/tests/`.
- The conversation-driven advisory honesty check parses the known `modes/advisory.py`
  bullet format (`"• <name> — ..."`) rather than doing real NLG understanding; if that
  template's exact punctuation ever changes, the check degrades to "no bullets found", not a
  false failure (an empty `mentioned` list cannot contain an unknown name).

## Adding a new scenario or profile

Datasets are owned by the orchestrator agent — do not edit
`evals/datasets/scenarios.yaml`/`advisory_profiles.yaml` from this harness. If a new
scenario id is added there with a chaos command this harness doesn't yet know how to
inject, add a branch in `evals/scenario_suite.py`'s `run_scenario_suite` (pick one of the
three existing injection strategies, or write a new one) — an unrecognised id is reported
as an honest `ERROR` ("no injection strategy wired"), never silently skipped. A new
profile needs no harness changes at all: `evals/advisory_suite.py` reads every key `expect:`
may contain generically.
