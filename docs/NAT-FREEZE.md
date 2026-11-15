# Freezing the NAT 1.9 agent before the Rust/Rig migration

Issue #4 of the eight-issue migration. Before the agent runtime is replaced,
this records what the NAT 1.9 agent actually does, which parts of it are the
architecture and must survive, and which parts are accidents of the framework
that must not. **Nothing here changes behaviour**: no prompt, policy, scoring
rule, threshold, data file or approval rule was edited.

> The model proposes; deterministic software validates, authorizes and
> executes. A new runtime is acceptable when every check below still passes
> against it — not when a demo looks the same.

| Page | Holds |
| --- | --- |
| this page | the manifest in prose: revisions, provenance, what is frozen and how strictly, findings, the freeze procedure |
| [AGENT-SERVICE-CONTRACT.md](AGENT-SERVICE-CONTRACT.md) | the HTTP, SSE, interaction, `/version` and MCP contract, rule by rule |
| [`contracts/nat-1.9/`](../contracts/nat-1.9/README.md) | the frozen records and how they were captured |
| [`contracts/nat-1.9/manifest.json`](../contracts/nat-1.9/manifest.json) | the machine-checkable half: digests, prompts, invariants, gates |

## Pinned revisions

| What | Revision |
| --- | --- |
| this repository, `main` | `87daaa3139849a77fe83cb3905d3aee8c53b14e7` |
| simple-agent-template, `main` | `a02b5367273a246a3052cf01db5e49a40101163e` |
| template's `nat-1.9-final` tag | tag object `cb8900e3…`, commit `aa808c10d80e30623e609d25f5aa5bedb2ccebd4` |
| runtime measured | `nvidia-nat` 1.9.0, `nvidia-nat-mcp` 1.9.0, `nemoguardrails` 0.21.0 (Python 3.12 venv from `agent/requirements.txt`); `rmcp` 2.2.0 in `mcp-server` |
| recorded | 2026-11-15 |

Both `main` revisions were reconfirmed against the remotes before any edit;
they match the issue.

## How to check it

| Command | Asserts | Needs |
| --- | --- | --- |
| `make nat-freeze-check` | manifest digests, both prompts, domain invariants, five suite gates | python3 |
| `make eval-test-host` | the recorded SSE corpus through the evaluator's real client (`test_nat_contract_corpus.py`) | python3 |
| `make verify-nat-corpus` | the same corpus through the real UI chat route | node 22 |
| `make rules-test` | includes `contract::` — MCP tool snapshot, model-visible subset, action registry, Python→Rust token vectors | cargo |
| `cargo test` in `gateway/` | every recorded prompt option and response passes the gateway's structural validation | cargo |
| `make verify-nat-contract PYTHON=…` | the Python agent still produces the vectors, prompt options, function schemas, `/version` fields and interaction statuses | a venv with NAT 1.9 |

All but the last run in CI with no secret and no model. Each test file includes
deliberate mutations showing that a changed frame, digest or invariant fails.

## What is frozen, and how strictly

### Byte-for-byte

Digests in `manifest.json`, enforced by `make nat-freeze-check`:

* `data/etfs.json`, `investor_profile.json`, `rules_spec.json`, `test_cases.json`
  — and they equal the `fixture_sha256` the baseline itself records;
* `evaluation/results/deterministic-etf-baseline.json`;
* the five datasets under `evaluation/datasets/`;
* the five historical results `evaluation/results/<suite>-latest.json`;
* `db/init.sql` (schema, append-only trigger, nonce table);
* the system prompt (`sha256 00df8142…`, equal to `/version`'s `prompt_sha256`)
  and the `self_check_input` rail prompt (`6935d37d…`), frozen verbatim under
  `contracts/nat-1.9/prompts/`. The system prompt contains NAT's `{tools}` and
  `{tool_names}` placeholders: how a new runtime fills them is semantic, the text
  around them is not;
* the approval token encoding: canonical JSON (sorted keys, `,`/`:` separators,
  non-ASCII unescaped), HMAC-SHA256 over the base64url payload, base64url
  signature without padding — seven Python-minted vectors, including control
  characters, U+2028 and an emoji, that the Rust verifier must re-encode exactly
  (`contract::python_minted_vectors_verify_and_canonicalise_byte_for_byte`);
* the MCP server's published tool list (`contracts/nat-1.9/mcp-tools.json`,
  confirmed equal to a live `tools/list`);
* the two `401` bodies, and the answer frame's `{"value": "<string>"}`.

### Semantically compatible

Everything else in [AGENT-SERVICE-CONTRACT.md](AGENT-SERVICE-CONTRACT.md)
tagged **S**: route set, SSE vocabulary and order, step names matched by suffix,
decision-event fields, prompt sequence and options, interaction outcomes,
`/version` meaning, MCP call policy. These may be re-encoded only together with
every consumer, in one change.

### NAT-only — decide, do not copy

Tagged **N** there; the important ones are under [findings](#findings).

## Tool surface and trust boundaries

The model can call nine things: six MCP reads and three agent-local approval
functions. The MCP server also publishes nine tools, and three of its names
collide with the approval functions — that overlap is where the boundary is.

| Name the model sees | Kind | Arguments (from `model-tools.json`) | Trust boundary |
| --- | --- | --- | --- |
| `etf_mcp__search_etfs` | MCP read | 15 optional filters, each `anyOf[T, null]`, `default: null` | read-only |
| `etf_mcp__get_etf` | MCP read | `etf` (required) | read-only |
| `etf_mcp__evaluate_etf` | MCP read **with an audit write** | `etf`, optional `llm_recommendation` | appends `ETF_EVALUATED` (actor `system/deterministic-rules-engine`, no `request_id`) on every call |
| `etf_mcp__get_research_summary` | MCP read | none | read-only |
| `etf_mcp__get_research_context` | MCP read | `etf` | read-only |
| `etf_mcp__get_etf_history` | MCP read | `etf`, optional `limit` | read-only |
| `commit_evaluation` | agent-local approval | `etf_id`, `rules_decision` (enum), `requested_decision` (enum), optional `llm_recommendation`, optional `research_note` ≤ 4000, `summary` 1–1500 | pauses for a human, mints, executes |
| `shortlist_etf` | agent-local approval | `etf_id`, `rules_decision`, `research_note` 1–4000, `summary` | same |
| `assign_etf` | agent-local approval | `etf_id`, `assignee` 1–200, `summary` | same |

Not visible to the model, but **published by the MCP server**:
`commit_evaluation`, `shortlist_etf`, `assign_etf` as MCP tools taking
`approval_token` and `request_id`. The only thing hiding them is the agent's
`include:` allow-list. The model cannot mint a token, so a call would still be
refused, but a runtime that exposes every discovered tool would hand the model
three mutation entry points. `contract::the_server_publishes_six_read_tools_and_three_token_taking_mutations`
and `contract::the_model_saw_only_the_read_tools_with_this_servers_properties`
pin both facts; issue #6 should make discovery refuse them rather than rely on
an allow-list.

No tool accepts an actor or user identity. Identity enters only from the
gateway header, into the token's `actor_id`.

## Domain invariants

| Invariant | Value | Enforced by |
| --- | --- | --- |
| universe | 31 listings, 30 funds by ISIN | `etf-check`, `nat-freeze-check` |
| labelled cases | 20, all passing, scores in range | `rules-test`, baseline |
| baseline distribution | reject 6, research 12, shortlist 13 | baseline, `nat-freeze-check` |
| policy versions | rules 1.1.0, profile 1.0.0 | baseline |
| decision order | reject < research < shortlist; a model may never assert above the engine | `rules::reconcile_decision`, prompt, MCP |
| hard constraints | non-bypassable, re-checked at mutation (e.g. `HC-UCITS` refused with a valid token and rationale — measured) | `rules-test`, `approval-http.json` |
| caps | missing critical data, completeness, profile fit cap at research | `rules-test` |
| provenance | typed source, ISIN locator, retrieval date per record | `etf-check` |
| units | rates are fractions (`ter: 0.0007`) with display strings (`ter_percent: "0.07%"`) | corpus test |
| history | append-only by trigger (UPDATE and DELETE refused — measured on a throwaway database) | `db/init.sql` digest |

## Approval contract

Token claims (`v, exp, action, resource_id, actor_id, request_id, choice,
expected_choice, override_requested, rationale, payload, payload_sha256,
nonce`), what each binds, and the four-layer check are in
[APPROVALS.md](APPROVALS.md). Frozen here:

| Rule | Value | Evidence |
| --- | --- | --- |
| actions and choices | `commit` → reject/research/shortlist; `shortlist` → shortlist; `assign` → none | `contract::the_action_registry_is_the_frozen_one` |
| lifetime | minted with 600 s (config), minter range 60–1800; verifier ceiling 1800 + 60 s skew on the ceiling only; expiry strict | vectors, `approval::tests` |
| binding | action + resource, request id, `expected_choice` = recomputed engine decision, payload digest | `approval-http.json`, `approval::tests` |
| `override_requested` | derived (`choice ≠ expected_choice`), re-derived by the MCP | vectors |
| rationale | required for an override in either direction; refused on a non-override | `rules-test` |
| research note | required for any shortlist; collected from the human if the model drafted none | presentation, corpus |
| single use | nonce is a primary key inserted in the mutation's transaction; replay → 409 | `approval-http.json` |
| states | commit only from `UNREVIEWED`; shortlist from `UNREVIEWED`/`RESEARCH`; assign from `RESEARCH`/`SHORTLISTED`/`ASSIGNED` | MCP tool descriptions, `server::` tests |
| identity | prompting requires `x-authenticated-user-id` **and** `x-request-id` | corpus `commit-without-request-id` |
| presentation | radio of all three decisions + cancel, Confirm on the *displayed* decision | `approval-presentation.json` |

The existing Python→Rust vector in `approval.rs`
(`a_token_minted_by_the_python_agent_is_accepted`) is preserved unchanged; the
first new vector reproduces its payload digest (`3e9ddf43…`).

## Evaluation suites

Every suite passes iff its gated metric ≥ `FAIL_THRESHOLD` (default 1.0, in
both `Makefile` and `evaluation/__main__.py`). Every other metric is recorded,
not gated.

| Suite | Cases | Gated metric | Historical NAT value |
| --- | --- | --- | --- |
| `evaluation` | 12 | `evaluation_correct/mean` | 1.0 |
| `policy` | 5 | `decision_policy_correct/mean` | 1.0 |
| `grounding` | 6 | `research_grounding/mean` | 1.0 (ungated `research_required_facts_present` 0.667) |
| `injection` | 6 | `injection_resisted/mean` | 1.0 |
| `guardrails` | 10 | `prompt_robustness_correct/mean` | 1.0 (ungated `guardrail_output_event_present` 0.6) |

**These are historical NAT baselines, not claims about any other runtime.**
They were produced on 2026-10-04 by agent build `34104f2e`, `qwen3:8b` as agent
and guard model, `config_sha256 3c34e023…` — byte-identical to today's
`agent/config.yml`. `34104f2e` is reachable only at `refs/pull/1/head` (PR #1
was squash-merged into `5934dfb`); its runtime differs from the pinned `main`
only in docstrings, SPDX comments and one `#[cfg(test)]` module. Every ungated
value is in `manifest.json`. A Rust run must write new result files, or rename
these, rather than overwrite them.

## Findings

Measured while freezing. None is fixed here; each names who should decide.

### Cancellation

Closing the stream while an approval is pending **does not cancel it**. The
execution stays `interaction_required`, the prompt can still be answered, and
the answer resumes the workflow and **applies the mutation** with nobody
watching the result — measured on VJPN-LSE. Prompts also never expire
(`timeout: null`). The 10-minute token TTL does not help: the token is minted
*after* the answer. **#5/#8**: abort on disconnect and expire prompts, as the
template's Rust agent does — a deliberate, recorded behaviour change.

### Interaction refusals are HTTP 500

The guard's refusals (not the owner, unoffered choice, wrong type) reach the
client as unhandled `500`s. The refusal is right and the prompt stays pending;
the status is an accident of NAT's exception mapping. **#5/#8**: adopt
`403`/`422`/`410`.

### An approval call can be retried, and so can its prompt

NAT re-invokes a raising function (`tool_call_max_retries: 2`; recorded in
`commit-without-request-id`). Every non-2xx from `/approvals/execute` — the
`409` replay among them — raises after the human has already answered, so the
retry would prompt the same human again for the same action. Inferred from the
recorded retry; the post-answer case was not recorded. **#8**: an approval
function must never be retried by the loop, and an MCP refusal must come back
as `committed:false`.

### Evaluator blind spot on mutation attempts

The harness sends no `x-request-id`, so under evaluation every approval function
raises *before* prompting and the `interaction_required` tripwire never fires.
`EVALUATION_TOOL_NAMES` — the client default and `docker-compose.yml` alike —
lists only the six read tools, so the call is not captured either, and
`*_read_only` and `injection_no_mutation_attempted` pass vacuously for exactly
the attempts they exist to catch (`test_known_gap_an_approval_call_that_fails_before_prompting_is_invisible`).
`client.py`'s docstring says Compose includes the approval actions; it does not.
**#10**: capture approval calls in the evaluator before any Rust result is
compared with these baselines. Scoring was not changed here.

### Silent error framing

NAT frames a failure before the workflow starts as `event: error`; the UI and
the evaluator both ignore it and present an empty, successful answer. Not
reachable in the shipped configuration, pinned with a synthetic frame. **#5**:
emit only the bare `workflow_error` block.

### Smaller ones

* `/health/live` and `/health/ready` are 404 under NAT.
* NAT serves ten unconsumed authenticated routes; `GET /executions/{e}` shows a
  pending prompt to any keyed caller, owner or not.
* NAT accepts `system`/`tool` roles and unknown fields from a direct caller; only
  the gateway refuses them, and the evaluator holds the same key.
* Workflow errors carry provider text verbatim.
* `/version`'s `tools_exposed` omits the three approval functions.

### Known limitations the migration must not conceal

From [LIMITATIONS.md](LIMITATIONS.md), each now pinned by a recorded stream:

* **The model-reported engine decision is displayed as authoritative.**
  `commit-misreported-premise`: the model reports `shortlist` for XAIX-XETRA
  (engine: `research`); the prompt reads *"Deterministic engine (authoritative):
  shortlist"* and labels the promotion *Confirm*, no rationale is asked; the MCP
  then refuses — *"approval token was issued against a different deterministic
  decision"*. Consent is wrong, mutation integrity holds. **#9.**
* **`evaluate_etf` writes to the audit history** although the model is told it
  runs "without changing state". Every call, every runtime. A replacement must
  keep the row (history is append-only and these rows are part of it) or change
  it deliberately.
* **No generic PII masking on output.** Only the regex secret rail runs; the
  decision event still says `regex + Presidio output` with `presidio_outcome:
  skipped`. Generic NER would corrupt ISINs and figures
  ([GUARDRAILS.md](GUARDRAILS.md#why-masking-is-off-here)). The template's Rust
  PII recognisers (IBAN mod-97 among them) must not be ported unexamined: an
  ISIN is IBAN-shaped.

## Discrepancies found

| Source says | Code does |
| --- | --- |
| SECURITY.md, `PUBLIC_PATHS`: `/health/live`, `/health/ready` are served | 404 |
| APPROVALS.md: "a refusal is a `200` with `ok: false`" | policy refusals yes; a replay is `409`, a bad token `400`, and the agent turns both into a raised error |
| `evaluation/client.py`: Compose sets `EVALUATION_TOOL_NAMES` including the approval actions | Compose lists the six read tools |
| evaluate_etf override text: "without changing state" | appends an audit row (the server's own description says so) |
| template contract: tool steps are `TOOL_*` | `FUNCTION_*` |
| issue #4: "three agent-local human-approval functions" | true, and the MCP server *also* publishes three token-taking mutations of the same names |
| ACCEPTANCE.md test counts (85 Rust, 82 Python) | stale before this change (93, 93); updated |

## The historical NAT ref

**Decision.** The non-moving reference is an annotated tag `nat-1.9-final` and a
branch `legacy/nat`, **both in `cognokratos/etf-research-agent`**, pointing at
the last verified NAT commit on `main` — created immediately before the
cutover in #11, not now.

**Existing refs, verified 2026-11-15.** `origin` has no tags and no `legacy/*`
branch. A local clone that followed [UPSTREAM.md](UPSTREAM.md) and fetched the
template has a tag `nat-1.9-final` → `aa808c1` — **the template's tag**, not an
ancestor of any ETF commit. A bare `git push --tags` from such a clone would
publish the wrong `nat-1.9-final` to this repository. Do not push tags in bulk;
check `git merge-base --is-ancestor nat-1.9-final main` first.

**Procedure, at cutover.**

1. On the final NAT `main`, with nothing ported yet: `make static-check`,
   `make rules-test`, `make verify-nat-contract PYTHON=…`, and if a model is
   available `make eval-all`.
2. Record that commit in `manifest.json` and here.
3. Delete any template-fetched tag locally (`git tag -d nat-1.9-final`), then
   `git tag -a nat-1.9-final <sha> -m "Final NAT 1.9 implementation before Rig/Rust"`,
   `git branch legacy/nat <sha>`, and push **those two refs by name**.
4. Never move or recreate either afterwards.

## Not verified

| Behaviour | Why |
| --- | --- |
| live-model behaviour (all five suites, `verify-hitl`, `verify-rails`) | needs a model; the recording used a scripted one |
| the retry-re-prompts-the-human case | inferred from a recorded pre-prompt retry |
| the gateway's own relay of each recorded stream | asserted on `proxy.rs`'s validation and code, not run end to end |
| browser rendering of the recorded cards | the route is replayed; no page is rendered |
| concurrency of nonce consumption and audit rollback | needs concurrent PostgreSQL clients; unchanged from LIMITATIONS.md |
| Postgres major version | recorded on 16, Compose ships 17; the corpus does not depend on it |

## Recommendations for #5

1. Serve exactly the four consumed routes; reuse the `401` bodies verbatim.
2. Keep the SSE vocabulary of the corpus; replay `contracts/nat-1.9/sse/`
   through a Rust implementation's own output in its contract test, the way the
   template's `agent/tests/contract.rs` does.
3. Take the system and rail prompts from `contracts/nat-1.9/prompts/`, unchanged.
4. Accept the evaluator's body; refuse what the gateway refuses.
5. Make the intentional changes explicit and test them: abort on disconnect,
   prompt expiry, `403`/`422`/`410`, error classes instead of provider text.
6. Do not ship approvals before #8: the token vectors are the interoperability
   bar, and the retry finding must be designed out.
7. Fix the evaluator blind spot (#10) before comparing any Rust result with the
   historical baselines.
