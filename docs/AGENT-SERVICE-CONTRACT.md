# The agent-service contract

The boundary between the agent and everything that calls it, for **this**
application, as the NAT 1.9 agent actually serves it at
`87daaa3139849a77fe83cb3905d3aee8c53b14e7`. It is what a replacement runtime
(issue #5) must keep compatible, and it marks where the NAT behaviour is an
accident that must *not* be copied.

It was recovered from the consumers — `gateway/src/proxy.rs`,
`ui/app/api/gateway/chat/route.ts`, `evaluation/client.py` — and then measured
against the real NAT agent running `agent/config.yml`, the real MCP server and a
scripted model. Every claim below cites a frozen record in
[`contracts/nat-1.9/`](../contracts/nat-1.9/README.md) or the code that produces
it. Where documentation elsewhere disagrees, this page says so; the full list is
in [NAT-FREEZE.md](NAT-FREEZE.md#discrepancies-found).

The template's contract
([simple-agent-template `AGENT-SERVICE-CONTRACT.md`](https://github.com/cognokratos/simple-agent-template/blob/main/docs/AGENT-SERVICE-CONTRACT.md))
was the starting point, not the authority. The differences are listed
[at the end](#differences-from-the-template-contract).

Each rule is tagged:

| Tag | Meaning |
| --- | --- |
| **B** | byte-for-byte: a consumer or a signature depends on the exact bytes |
| **S** | semantic: the meaning must hold; the encoding may change if every consumer is updated in the same change |
| **N** | NAT-only: measured NAT behaviour that nothing depends on, or that is a defect. Not to be reproduced without a decision recorded in the issue that changes it |

## Routes

Measured: [`agent-routes.json`](../contracts/nat-1.9/agent-routes.json).

| Route | Auth | Consumer | Tag |
| --- | --- | --- | --- |
| `GET /health` → `{"status":"healthy"}` | none | Compose healthcheck | S |
| `GET /version` | key + identity | evaluator (`evaluation/provenance.py`), `make version` | S |
| `POST /v1/workflow/full?filter_steps=…` | key + identity | gateway, evaluator | S |
| `POST /executions/{e}/interactions/{i}/response` | key + identity | gateway | S |

`/health/live` and `/health/ready` are listed in `fastapi_worker.PUBLIC_PATHS`
and in [SECURITY.md](SECURITY.md) but **NAT serves neither: both answer 404**
without credentials (the key check is skipped for them, NAT has no route). No
consumer calls them.

NAT also serves, behind key and identity, routes nothing in this repository
calls: `POST /v1/workflow`, `/v1/workflow/stream`, `/v1/workflow/atif`,
`/v1/chat`, `/v1/chat/stream`, `/v1/chat/completions`, `/evaluate/item`,
`GET /executions/{e}`, `GET /mcp/client/tool/list`,
`GET /mcp/client/tool/list/per_user`, `GET /auth/redirect`, `/docs`,
`/openapi.json`. **N** — a replacement serves only the four routes above.
`GET /executions/{e}` deserves a note: it returns a pending prompt, text
included, to *any* keyed caller with an identity, without the owner check the
response route has.

## Authentication, in this order

| Step | Failure | Tag |
| --- | --- | --- |
| `Authorization: Bearer <NAT_GATEWAY_API_KEY>`, constant-time, then stripped from the request | `401`, `content-type: application/json`, `www-authenticate: Bearer`, body `{"error":"invalid or missing internal API key"}` | B (status, body) |
| exactly one non-empty `x-authenticated-user-id` (repeated, empty or blank → refused; name match case-insensitive) | `401`, body `{"error":"missing or ambiguous authenticated identity"}`, no `www-authenticate` | B (status, body) |

An unknown path with valid credentials is `404`; without, `401` — the key is
checked before routing. NAT's own `identity_header` refusal never reaches the
client on the workflow routes; `RequireIdentityHeaderMiddleware` is what
enforces it ([LIMITATIONS.md](LIMITATIONS.md)).

## Trusted request headers

| Header | Sent by | Used for | Tag |
| --- | --- | --- | --- |
| `x-authenticated-user-id` | gateway (Keycloak subject); evaluator (`evaluation-harness`, or `EVALUATION_PRINCIPAL`) | approval prompt owner and responder; token `actor_id` | S |
| `x-authenticated-username`, `-roles` (comma-separated), `-email` (chat only) | gateway | span metadata, redacted by `UserIdentityProcessor` | S |
| `x-request-id` | gateway: a fresh UUID per chat request and another per interaction response; evaluator: none | token `request_id` | S |
| `X-Evaluation-Case-Id` | evaluator | nothing in the agent | N |

The gateway percent-encodes values outside printable ASCII and builds a fresh
upstream request, so no browser header is forwarded.

`x-request-id` is **not validated, generated or echoed** by NAT: the response
carries none, and a missing, repeated or malformed value is not refused. The
approval functions read it and refuse to prompt without it
(`approval._identity`), so an approval can only be bound to a gateway-minted id.
The template's Rust agent adds a `400` for a repeated or malformed value; that
is stricter and is a decision for #5, not a NAT behaviour.

## `POST /v1/workflow/full`

### Request

The gateway sends `{"messages":[{"role","content"}]}`, re-serialised from a
`deny_unknown_fields` parse: roles `user`/`assistant` only, non-empty, last turn
`user`, ≤ 100 messages, ≤ 32 768 characters each, ≤ 262 144 in total
(`GATEWAY_MAX_*`). `filter_steps=TOOL_START,TOOL_END,FUNCTION_START,FUNCTION_END`
(`AGENT_FILTER_STEPS`). No timeout on the stream; one session may hold a bounded
number of streams (`429` beyond it).

The evaluator sends `{"messages":[…], "stream": true, "user": <case>, "evaluation_case_id": <case>}`
with `filter_steps` also including `WORKFLOW_START,WORKFLOW_END`, no
`x-request-id`, and retries on any `URLError` — which includes an HTTP error
status — up to `EVALUATION_HTTP_MAX_ATTEMPTS` (4).

NAT itself validates only that the body is JSON carrying `messages` or
`input_message` (`422` otherwise). It **accepts** `system`/`tool` roles, a final
`assistant` turn and unknown top-level fields; an empty `messages` list is a
`200` stream holding one `workflow_error` block. Those rules live in the gateway
alone, which matters because the evaluator holds the same key and bypasses it
(see `workflow_request_validation` in `agent-routes.json`). A replacement may
be stricter, but must accept the evaluator's three extra fields. **S**

History: `trim_messages(max_tokens=30, token_counter=len, strategy="last",
start_on="human")` — the last 30 messages, starting on a user turn
(`max_history: 30`; the template uses 20). Tool loop: LangGraph
`recursion_limit = (max_tool_calls + 1) × 2 = 62` graph steps, not an exact tool
budget; on exhaustion the stream emits an *answer* chunk "The agent could not
produce a final answer within 30 tool calls.", not an error. **S**

### Response

`200`, `content-type: text/event-stream; charset=utf-8`. Frames are separated
by a blank line. The recorded vocabulary is closed:

| Frame | Shape | Tag |
| --- | --- | --- |
| step | `intermediate_data: {"id","parent_id","type","name","payload"}` — `payload` is a **JSON string** holding the step (`event_type`, `name`, `data.input`, `data.output`, `metadata`, …) | S |
| answer | `data: {"value": "<text>"}` — `value` is always a string, even `"86"`, `"true"`, `"1e3"`, `"0"` | B (`value` key, string type) |
| interaction | `event: interaction_required\ndata: {"event_type":"interaction_required","execution_id","interaction_id","prompt",{…},"response_url"}` | S |
| workflow error | a bare JSON block `{"code":"workflow_error","message","details"}` — no SSE field name | S |
| session error | `event: error\ndata: {"code":"workflow_error",…}` — NAT's framing for a failure before the workflow starts | N (see below) |

Step facts the consumers rely on, all in the corpus:

* **MCP tools stream as `FUNCTION_START` / `FUNCTION_END`, never `TOOL_*`**, named
  `etf_mcp__<tool>` (the function group). The approval functions stream under
  their bare names (`commit_evaluation`, …). The evaluator's `TOOL_*` branch and
  the template contract's "tool steps are TOOL_START/TOOL_END" were never
  exercised by this runtime; both consumers strip everything up to the last `__`
  and match the remainder against a configured name list (`UI_TOOL_NAMES`,
  `EVALUATION_TOOL_NAMES`). **S**
* Tool arguments are in `payload.data.input` (with `null` for omitted optionals,
  e.g. `{"etf":"CSPX-LSE","llm_recommendation":null}`); results in
  `payload.data.output` as the MCP text content — a JSON string for success, the
  text `MCPToolClient tool call failed: <reason>` for a tool error. **S**
* `WORKFLOW_START`/`WORKFLOW_END` (only when filtered in) carry
  `workflow_trace_id`, which the evaluator records as `agent_trace_id`. **S**
* Guardrail decisions are function steps, scored from `payload.data.output` of
  the `FUNCTION_END`:

  | Event | Output fields | Tag |
  | --- | --- | --- |
  | `guardrail_input_self_check_decision` | `stage`, `name`, `outcome`, `blocked`, `modified`, `llm_blocked`, `deterministic_blocked`, `deterministic_block_matches`, `deterministic_allow_matches`, `allow_override_applied`, `decision_source`, `llm_call_count`; the length-limit refusal has `decision_source: "deterministic_input_limit"`, `input_length`, `input_limit` | S (`blocked` and `decision_source` are B for scoring) |
  | `guardrail_output_regex_decision` | `stage`, `name` (`"regex + Presidio output"` although Presidio is not installed), `outcome`, `blocked`, `modified`, `regex_outcome`, `presidio_outcome` (`"skipped"`), `sanitized_output_length` | S |

  The evaluator also accepts any `guardrail_input_` / `guardrail_output_`
  prefix, so the template's `guardrail_output_regex_presidio_decision` and the
  Rust `guardrail_output_regex_pii_decision` would score too.
* Order: the input decision precedes everything; answer frames precede the
  output decision event; the output rail **replaces** a blocked answer — the
  recorded stream carries only `Blocked by regex check output rails.`, never the
  secret. **S**
* An input-rail block answers with NeMo's own refusal (`I'm sorry, I can't
  respond to that.`) when the model also blocked, `GUARDRAILS_INPUT_BLOCK_MESSAGE`
  otherwise, and the oversize refusal for > 32 000 characters. No tool runs. **S**

Errors:

* A workflow exception (provider failure, `KeyError` from a `tool` role…) is the
  bare JSON block. Its `message` is the exception text **verbatim, provider text
  included** (`Error code: 500 - scripted provider failure`). Both consumers
  detect it by its leading `{` and surface `message`. **S** for the shape; the
  verbatim provider text is **N** (the template's Rust agent sends a class).
* The `event: error` framing is **silently ignored by both consumers**: the UI
  finishes the message normally with no text, the evaluator returns an empty
  answer. No recorded scenario reaches it — `RequireIdentityHeaderMiddleware`
  turns the identity case into a 401 first — so the frame in the corpus is
  produced from NAT's own model and format string and labelled synthetic. **N**
* An `observability_trace:` frame exists in NAT but did not appear in any
  recorded stream; both consumers would ignore it. **N**

## Human approval interactions

The prompts themselves are fixed in
[`approval-presentation.json`](../contracts/nat-1.9/approval-presentation.json)
and appear on the wire in the corpus. Prompt object fields: `input_type`,
`text`, `timeout` (always `null`), `error`, plus `options` (radio, binary) or
`placeholder` and `required` (text).

| Function | Prompts, in order | Tag |
| --- | --- | --- |
| `commit_evaluation` | `radio`: every decision `{id=value=decision}` labelled `Confirm — …` on the *displayed* engine decision and `Override — …` on the others, plus `{id:"cancel", value:"__CANCEL__"}`; then `text` rationale if the choice differs from the displayed decision; then `text` research note if the choice is `shortlist` and no note was drafted | S |
| `shortlist_etf` | promotion (displayed decision below `shortlist`): a single `text` rationale prompt carrying the full context instead of a confirmation; otherwise `binary_choice` | S |
| `assign_etf` | `binary_choice`: `{id:"confirm", value:true}`, `{id:"cancel", value:false}` | S |

A text answer equal to `__CANCEL__` cancels; an empty one raises after the
workflow has resumed (the gateway refuses it first).

### `POST /executions/{e}/interactions/{i}/response`

Body: `{"response": {"type": "radio"|"binary_choice"|"text", …}}`. The gateway
checks both ids are UUIDs, the body ≤ 16 KiB, bounded option fields, a
non-empty rationale ≤ 4 000 characters, and protocol-level cancel consistency
(radio: `id:"cancel"` iff `value:"__CANCEL__"`; binary: `confirm`↔`true`,
`cancel`↔`false`). Every option the agent recorded offering, echoed back, passes
it (`proxy.rs` test against the corpus). The agent then checks owner, response
type and the offered `(id, value)` pair.

Measured outcomes ([`approval-http.json`](../contracts/nat-1.9/approval-http.json)):

| Case | NAT status | Tag |
| --- | --- | --- |
| owner, offered pair, or self-consistent cancel | `204`, empty | S |
| not the owner / unoffered pair / wrong response type | **`500` `Internal Server Error`**; the prompt stays pending | N (status); S (refusal and still-pending) |
| already answered | `400` `{"detail":"Interaction … has already been resolved"}` | N |
| unknown execution or interaction | `404` `{"detail":"'Execution … not found'"}` | S |

The `500`s are not designed: `InteractionAuthorizationError` subclasses
`Exception`, and NAT's route maps only `KeyError`→404 and `ValueError`→400. The
gateway relays status and body unchanged and the UI treats only 2xx as accepted,
so the refusal works; the code is an accident. The template contract's
`403`/`422`/`410` is the target for #5.

### Cancellation

| Event | NAT behaviour | Tag |
| --- | --- | --- |
| human chooses Cancel | the function returns `{"approved":false,…,"message":"The human cancelled the requested change."}`; nothing is minted | S |
| client closes the stream while a prompt is pending | **nothing is cancelled**: the execution stays `interaction_required`, the prompt can still be answered (`204`), and the workflow then resumes and **applies the mutation** with no client attached (measured: VJPN-LSE `UNREVIEWED → RESEARCH`) | N — must change; see [NAT-FREEZE.md](NAT-FREEZE.md#cancellation) |
| prompt lifetime | none: `timeout` is `null`, a pending prompt lives until answered or the process restarts | N |

## `GET /version`

Fields, measured: `available`, `model`, `guard_model`, `reasoning_effort`,
`guard_reasoning_effort`, `build_commit`, `config_path`, `config_sha256`,
`prompt_sha256`, `guardrails_prompts_sha256`, `tools_exposed` (and `error` when
the config cannot be read). **S**, with three notes:

* `tools_exposed` lists only the six MCP tools; the three approval functions the
  model can call are absent.
* `guardrails_prompts_sha256` is SHA-256 of `yaml.safe_dump(prompts,
  sort_keys=True)`, not canonical JSON. The evaluator recomputes it the same way
  from the file, so a runtime that changes the method must change the evaluator
  in the same commit.
* There is no `agent_runtime` field. The evaluator records the runtime only from
  what the agent reports; issue #10 decides how a NAT result is labelled.

## Agent → MCP

| Property | Value | Tag |
| --- | --- | --- |
| transport | MCP streamable HTTP at `ETF_MCP_URL`, `Authorization: Bearer $MCP_API_KEY`, protocol `2024-11-05` | S |
| tools the server publishes | nine: six reads plus `commit_evaluation`, `shortlist_etf`, `assign_etf` taking `approval_token` and `request_id` ([`mcp-tools.json`](../contracts/nat-1.9/mcp-tools.json)) | B (snapshot test) |
| tools the model sees | the six reads, through `include:`, with `tool_overrides` descriptions ([`model-tools.json`](../contracts/nat-1.9/model-tools.json)) | S |
| call policy | `tool_call_timeout: 30`, reconnect ×4 (0.5–8 s), `session_aware_tools: true` | S |
| identity on reads | none: the MCP boundary authenticates the agent, not the person | S |
| approval execution | `POST {mcp}/approvals/execute {"approval_token","request_id"}` ([APPROVALS.md](APPROVALS.md)) | B (token format) |

`/approvals/execute` outcomes, measured: policy refusals are `200`
`{"ok":false,"result":"<reason>"}`; a nonce replay is **`409`**
`{"error":"approval token has already been consumed","ok":false}`; an
undecodable token `400` with an empty body; a missing field `422`; no key `401`.
The agent maps only 2xx to `committed: body.ok`; any other status raises
`ValueError("The approved change was rejected: …")`, which reaches the model as
a tool error rather than the structured `committed:false` result.

## Differences from the template contract

| Template says | ETF NAT actually | Consequence for #5 |
| --- | --- | --- |
| `GET /health/live`, `/health/ready` exist | 404; only `/health` | serve `/health`; adding the others is harmless |
| tool steps are `TOOL_START`/`TOOL_END` | `FUNCTION_START`/`FUNCTION_END` | either works for both consumers; pick one and record it |
| tools named `tickets_mcp__…` | `etf_mcp__…`; approval functions unprefixed | consumers match by suffix |
| error block `{"error":"workflow_error","message"}` | `{"code":"workflow_error","message","details"}` | both consumers read only `message`; keep `message` |
| error message is a class, never provider text | provider text verbatim | Rust should send a class (stricter) |
| output decision `guardrail_output_regex_pii_decision` | `guardrail_output_regex_decision` | prefix match covers both |
| `max_history` 20 | 30 | keep 30 |
| exact `max_tool_calls` tool budget | recursion limit of 62 graph steps | an exact budget of 30 is stricter, acceptable |
| interaction refusals `403`/`422`, run gone `410` | `500`/`500`, `400` once answered | adopt the template's codes; UI behaviour unchanged |
| dropping the stream aborts the run | the run survives and can still apply the mutation | adopt the template's behaviour — an intentional, recorded change |
| prompts expire | never | adopt expiry, aligned with the 10-minute token TTL |
| unknown body fields refused | accepted | must accept `stream`, `user`, `evaluation_case_id` |
| repeated/malformed `x-request-id` → 400 | not checked | stricter is acceptable |
| `/version` has `agent_runtime`, `guardrails_prompts_digest` | neither; YAML digest | change the evaluator in the same commit |
| one approval action, approvals optional | three actions, approvals mandatory | ETF-specific; never optional here |
| PII masking on output | deliberately none ([GUARDRAILS.md](GUARDRAILS.md#why-masking-is-off-here)) | do not port the template's masking |
