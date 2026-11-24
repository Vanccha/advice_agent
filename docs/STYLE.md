# Code style and commenting standard

This repository is also a teaching project: juniors read the code to learn how a production-shaped
system fits together. The comments exist to make that reading fast and honest. This file is the
standard every module follows. `CLAUDE.md` holds the architecture rules; this file holds the
writing rules.

## 1. Language split

| What | Language |
|---|---|
| Code, identifiers, comments, docstrings, commit messages, logs | English |
| Everything an end user or department agent reads (UI strings, replies, ticket bodies, policy `*_en` fields) | British English; money is GBP |
| `docs/ogrenme/` (the learning guide for the Turkish team) | Turkish, on purpose |
| `docs/contracts.md`, `docs/observability.md`, this file, READMEs next to code | English |

Technical terms stay in English inside the Turkish guide too (endpoint, webhook, state machine,
tool calling...); each is explained once where it first matters. If the code and the guide
disagree, the code is right and the guide gets a fix.

## 2. The module header

Every Python module starts with a docstring that has these parts, in this order. Keep each part
short; the header is a map, not an essay.

1. **One-line purpose.** What this module is, as a noun phrase or a verb phrase.
2. **`Where this sits:`** who calls it, whom it calls, what it reads and writes. A reader who has
   never seen the repo must be able to place the file in the architecture from this paragraph alone.
3. **`Key ideas:`** 3-5 bullets naming the concepts the file demonstrates (default-deny, fail-safe,
   composition root, ...). Explain a term in one clause the first time it matters; do not define
   whole textbooks.
4. **`Read next:`** the next one to three files, in reading order.
5. Optional: **`Public entry points:`**, **`Import as:`** (the real import path in this repo's
   layout), **`See: docs/contracts.md §x`**.

Example (from `assistant/policy/engine.py`):

```python
"""The authority engine: interprets a tenant's ``policy.yaml`` to allow or deny an action.

Where this sits: ``policy`` package. Called by ``policy.executor`` (and ``modes`` for
pre-checks). It reads a ``PolicyFile`` (``core_common.config_models``) and returns a
``PolicyDecision``; it does no I/O.

Key ideas:
- Default-deny: an action not listed in ``policy.yaml`` is refused (``action_not_in_policy``).
  The model never decides its own authority.
- Fail-safe on missing data: a condition that refers to data we do not have counts as NOT
  met, never as met.
- Pure and stateless: same config and context in, same decision out, so it is easy to test.

Read next: ``policy/executor.py``, ``config/tenants/netswift/policy.yaml``.
See: docs/contracts.md §4.6.
"""
```

Packages (`__init__.py`) get a header too. For a package of handlers it also states the
**reading order** of its files (see `assistant/modes/investigation/__init__.py`).

Do not narrate how the file came to be, and do not repeat what the code says line by line.

## 3. Docstrings

- Every public function, class and method gets a docstring. Private helpers get one when the
  name alone does not explain the *contract* (inputs, outputs, failure behaviour).
- First line: a short summary in the imperative or as a noun phrase, ending with a period.
- Say what a caller needs: what is returned, what is raised, what is guaranteed ("Never raises
  for provider or tool trouble: ..."). Mention units and formats (GBP, UTC, masked or raw).
- HTTP route docstrings list the status codes and error codes the route can produce.
- Prefer one precise paragraph to a bullet list of parameters when the parameters are obvious.
  Add an `Args:` block only when a parameter has a non-obvious meaning.
- Reference contracts by section: `contracts §4.6` (short) or `docs/contracts.md §4.6` (in headers).

## 4. Inline comments: why, not what

A comment earns its place by explaining something the code cannot say:

- **Why** this approach (and often why not the obvious one): "Masking happens first: nothing
  downstream ever sees the raw text."
- A **constraint or trap**: "psycopg treats `%` as a parameter placeholder and the trigger body
  contains one."
- A **safety property** and where it is enforced.
- The **unit or domain meaning** of a magic value.

Do not write comments that restate the code (`# increment i`). If a comment is needed to explain
*what* a block does, extract a well-named function instead.

**No history narration.** Comments describe the code as it is now. Do not write "previously we...",
"changed from X to Y", "added for ticket 123", "fixed bug". That belongs in the commit message and
in `git log`. Only explain a past incident when the present code would otherwise look wrong, and
then phrase it as a standing reason ("A shared gateway let concurrent conversations spend each
other's budget, so every turn gets its own view").

**`Demo simplification:`** is the label for anything deliberately simpler than production. Put it
in the module header or next to the line, followed by what production would do:

```python
# Demo simplification: fake streaming of an already finished reply (see header).
```

```text
Demo simplification: there is no password. Anyone who knows a customer number can sign in
as that customer. Production would authenticate with the company's real identity provider
(OIDC/SSO) and issue a signed, expiring session, with `secure=True` on the cookie.
```

Grep for `Demo simplification` to list them all. A shortcut without the label is a defect: either
label it or fix it.

## 5. Section banners

Long modules are split into sections with a one-line banner so a reader can scan them:

```python
# --- Cause -> action table ----------------------------------------------------------
# --- Running the action -------------------------------------------------------------
```

Rules: same shape everywhere (`# --- Title ---...`), roughly 85-90 columns wide, no more than
a handful per file. If a file needs many banners it probably wants to be a package.

## 6. Naming

- Python: `snake_case` for modules, functions and variables; `PascalCase` for classes;
  `UPPER_SNAKE_CASE` for constants; a leading underscore for module-private names.
- Names say the domain concept, not the type: `customer_no`, `ticket_key`, `root_cause`,
  `PolicyDecision`; avoid `data`, `info`, `obj`, `tmp`.
- Enums are `StrEnum` so they compare equal to the strings stored in the database and in JSON
  (`Mode`, `Intent`, `RootCause`, `StepType`, ...). Prefer an enum to a loose string for any
  closed set.
- User-visible text fields carry an `_en` suffix (`reply_en`, `reason_en`, `summary_en`) so a
  future locale can be added without renaming fields.
- Booleans read as questions (`is_stuck`, `requires_confirmation`, `allowed`).
- Tool names (MCP) are `verb_noun` in `snake_case`; read-only tools start with `get_`, `list_`,
  `find_` or `detect_`. The load-time check in `core_common/config_models.py` rejects tool
  names that look like writes in the investigation allowlist, so keep that convention.
- Packages: each immediate subdirectory of `assistant/` is a top-level package (`policy`,
  `modes`, ...). The names `common` and `mcp` are taken (the boundary test forbids `common`; `mcp`
  would shadow the installed SDK), hence `core_common` and `mcp_gateway`. Company services each
  call their package `app`.
- Types: annotate every public signature; use `from __future__ import annotations`; prefer
  pydantic models for data that crosses a boundary (HTTP, DB JSON, model output).
- Line length 100 (`pyproject.toml`, ruff).

## 7. Where tests live

| Kind | Location | Notes |
|---|---|---|
| Unit | `<package>/tests/` next to the code | No network, no real model: use `ScriptedProvider` and `FakeGateway` |
| Architecture | `tests/architecture/` | Import and vocabulary boundaries |
| Integration | `tests/integration/` | Needs the running stack; marked `integration`; skipped when it is down |
| Evals | `evals/` (+ `evals/tests/` for the harness itself) | Behaviour of the whole assistant against datasets |
| Chaos tooling tests | `company/chaos/tests/` | Skipped without the live company stack |

Rules:

- A behaviour change comes with a test in the same change. A user-visible behaviour change also
  gets an eval case where it makes sense.
- Test names state the guarantee: `test_unknown_action_is_denied`, not `test_engine_2`.
- Tests must not depend on another test's leftovers or on timing; wait for a condition, never a
  fixed `sleep`.
- Run suites through `scripts/run_tests.sh` (each suite in its own process, because every company
  service names its package `app`).
- Keep `docs/contracts.md`, `CLAUDE.md` and `README.md` current when behaviour changes.

## 8. Commit messages

Imperative, English, one idea per commit, a subject line under about 72 characters
("Scope tool gateway per turn, serialise audit appends"). The body explains *why* when it is not
obvious. History narration lives here, not in code comments.

## 9. Quick review checklist

- [ ] Module header has purpose, `Where this sits`, `Key ideas`, `Read next`.
- [ ] Public API has docstrings that state returns, raises and guarantees.
- [ ] Comments explain why; none narrate history or restate code.
- [ ] Shortcuts carry `Demo simplification:` and say what production would do.
- [ ] Names use domain words; closed sets are enums; user text fields end in `_en`.
- [ ] Tests added next to the code; `tests/architecture` still passes.
- [ ] Everything user-facing is British English; the learning guide stays Turkish.
