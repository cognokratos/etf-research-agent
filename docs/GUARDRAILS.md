# Guardrails

NeMo Guardrails, configured in `agent/config.yml` and driven by
`agent/src/nat_streaming_react/text_guardrails.py`.

## Input rail

An LLM self-check classifier, plus two deterministic layers that can override
it. Precedence is deliberately asymmetric:

1. a deterministic **critical-pattern** match always blocks;
2. otherwise a narrow, fully-anchored **read-only allow** template can correct an
   LLM false positive;
3. otherwise the LLM verdict stands.

The allow templates are anchored to the complete message (`fullmatch` on
whitespace-normalised text, length-bounded), so appending an instruction
override to an otherwise valid query does not inherit the allow.

The templates themselves are this application's vocabulary
(`_READ_ONLY_LOOKUP_TEMPLATES`): listing the universe, ranking by score, the
research summary, one fund, its details, its history, and "why is this fund
marked X". The *mechanism* around them is not domain-specific, and adapting it
means replacing those patterns and nothing else — subject to two invariants the
module documents and `agent/verify_input_guardrails.py` asserts:

* every pattern is matched with `fullmatch` against the normalised message;
* **no pattern names a state-changing action.** An allow rule that admitted
  "shortlist VWCE-XETRA" would let the override reach a mutation, which is the
  one thing this layer must never do. It exists to correct an over-eager input
  rail on requests that *read*.

### Client-supplied history

The gateway validates that each history message has role `user` or `assistant`;
it cannot validate who *wrote* it. A caller can therefore replay fabricated
assistant turns that no rail ever screened, carrying the implied authority of
the agent's own voice. The deterministic patterns run across those turns, and a
match joins the block set so it outranks the allow override.

Prior **user** turns are deliberately not re-screened. Each was screened by the
full rail when it was the latest turn, and a refused one never reached the
model — but its text stays in the history the client replays. Screening it again
made one refusal poison the rest of the conversation: every later message,
however innocuous, matched the injection still sitting in the transcript.
Recovery after a blocked turn is a required behaviour.

The residual gap is a fabricated prior *user* turn, which no rail sees either.
It is knowingly left open: closing it means re-screening text the user can see
was already refused, and the same caller can simply send that text as the latest
turn, where the full rail does screen it.

## Output rails

Two are implemented and they do different things. **This application enables
only the first.** `mask sensitive data on output` is deliberately left off, and
the Presidio (`sdd`) extra is not installed — see *Why masking is off here*
below. The masking path is documented in full because it is one configuration
change away, not because it is running.

### `regex check output` — blocks

Deterministic credential and prompt-leakage patterns, no LLM. This is what
blocks a streamed response. NeMo evaluates a rolling window of
`chunk_size + context_size` chunks, so a credential split across several tokens
is still caught, and `stream_first: false` means a credential in the final chunk
is seen before it is released.

### `mask sensitive data on output` — masks the complete answer, buffered

**Not enabled in this application.** Presidio-backed PII masking.
**Measured behaviour in nemoguardrails 0.21**, not
assumed — `agent/verify_output_guardrails.py` asserts all of it:

* `entities` is the only key the masking action reads.
* Each match is replaced with `<ENTITY_TYPE>`. There is no configurable mask
  token: `mask_token` is declared in the Guardrails config schema and read
  nowhere in the package, so it is not set in `config.yml`.
* The confidence floor is Guardrails' own hardcoded **0.4**. The configured
  `score_threshold: 0.6` is **not** passed to Presidio by `mask_sensitive_data`
  (only by `detect_sensitive_data`, which this policy does not use). It is kept
  as a declaration of intent and would become live if a detect rail were added.

NeMo's own streaming rail runner (`stream_async`) can only use an action's
return value to decide blocked/not-blocked, never to rewrite text — that is a
property of NeMo 0.21 itself, unrelated to anything fixable in this repository.
So when this flow is enabled, `TextGuardrailsMiddleware` does not call
`stream_async` for output evaluation at all: it dispatches to
`_stream_with_buffered_masking`, which buffers the complete streamed answer,
runs the same masking evaluation (`generate_async`, the blocking-capable path)
over it exactly once, and only then releases the masked result. This is
deliberate: an entity can straddle any two adjacent model-token chunk
boundaries, so the complete answer is the only boundary that is always safe to
mask against, and nothing is released until that evaluation has finished — a
masked, blocked, or errored outcome never has an unmasked prefix already sent
to the client. The trade is latency: the client waits for the whole answer
instead of seeing it token by token. `GUARDRAILS_PII_MAX_BUFFER_CHARS` (default
200000) bounds how much is buffered; a response over that limit is refused
outright rather than released partially masked or unmasked.

Disabling this flow (leaving only `regex check output` in `output.flows`)
returns output evaluation to the fully-streamed, low-latency path — credential
blocking alone does not need buffering, since it only needs to decide
block/no-block, never to rewrite text. The two flows are independent switches;
enabling or disabling one does not change the other's availability, only
which dispatch path the middleware uses for output evaluation as a whole (see
`TextGuardrailsMiddleware._pii_masking_enabled`).

`PERSON` and `ORGANIZATION` would have to stay absent from the entity list:
they are the issuer and index names in every answer, and masking them would
destroy it.

### Why masking is off here

This is a domain decision, not an oversight, and it is the one place this
application deliberately diverges from the template's guardrail posture.

The values a generic NER pass would mask are the answer. An ISIN is
structurally close enough to an IBAN that `IBAN_CODE` catches it; expense
ratios, fund sizes and scores are exactly what was asked for. Masking them
produces a confidently wrong answer rather than a redacted one — and for a
research decision about someone's money, a plausible wrong number is worse than
a refusal.

Two consequences, stated plainly:

* Output protection here is deterministic secret-leakage patterns **only**. It
  is not PII protection. If the ETF snapshot ever carries personal data, that
  changes and this decision has to be revisited.
* Because no masking flow is enabled, the middleware keeps NeMo's native
  streaming path: this deployment streams incrementally, and
  `GUARDRAILS_PII_MAX_BUFFER_CHARS` is inert.

Turning it on is three coordinated changes, and the compatibility shim already
handles its own absence (`guardrails_compat.masking_is_available`), so a partial
change degrades cleanly rather than erroring:

1. `nemoguardrails[sdd,tracing]` in `agent/requirements.txt`;
2. the `python -m spacy download en_core_web_lg` step in `agent/Dockerfile`;
3. `mask sensitive data on output` in `output.flows` **and** a
   `sensitive_data_detection` entity list in `rails.config` — `config.yml` is
   asserted to carry both or neither.

## What the LLM verdict actually is

The self-check prompt demands `Yes` or `No`, and `output_parser: is_content_safe`
turns that into a decision. That parser's real behaviour in nemoguardrails 0.21
is asserted by `verify_input_guardrails.py` rather than assumed, because the LLM
half of the input rail rests entirely on it:

* it normalises the response, keeps the **first two tokens**, and matches them
  against `safe`, `unsafe`, `yes`, `no` — token membership, not substring;
* **anything it does not recognise is unsafe.** An empty response, a refusal, a
  reply in another language and `Maybe` all block;
* the four keywords are tested in that order, so a response whose first two
  tokens contain the bare word `safe` parses as safe even when negated —
  **`Not safe` parses as safe.**

That last one is pinned as a known hazard. Three things keep it unreachable
here: the prompt demands "exactly Yes or No and nothing else", `max_tokens: 4`
bounds the reply to roughly one word, and the deterministic critical-pattern
layer blocks the high-risk categories without consulting the model at all. If
any of those is relaxed, this parser stops being safe to rely on alone.

## Input length bound

The user text is measured before the guard model is asked anything. Over
`GUARDRAILS_INPUT_MAX_CHARS` (32,000 by default, floor 256) the request is
refused with `GUARDRAILS_INPUT_OVERSIZE_MESSAGE`, recorded as a blocked input
decision with `decision_source=deterministic_input_limit`.

Refused, never truncated. A classifier shown a prefix decides about the prefix,
so truncating would let 32k of benign text followed by the real request be
classified on the benign part alone. The bound also stops the cheapest possible
request from being the most expensive one to serve: the guard model runs on
every request, ahead of all other work. Every shipped input fixture — read-only
lookups, attacks, and the decision requests that must reach the approval
boundary — is asserted to sit well under it.

`max_history: 20` in the workflow bounds the number of turns, not their length,
so it is not a substitute. This mirrors what NAT 1.9 added to its own
content-safety middleware (`max_content_length`), which this application does
not otherwise use — see below.

## NAT's own defense middleware: assessed, not adopted

NAT ships a `defense` middleware family (`content_safety_guard`, `pii_defense`,
`output_verifier_tools`, `pre_tool_verifier`) that overlaps these rails. Each
was assessed against NAT 1.9.0 and against this application, not only the
template:

| Component | Verdict |
| --- | --- |
| `content_safety_guard` | **Not adopted yet.** Its 1.9 fixes (fail-closed, exact verdicts, bounded input) are properties the input rail here already has by other means. Adopting it needs a real guard model returning `Safe`/`Unsafe`/`Controversial` rather than the general instruct model the self-check prompt targets — worth doing together with the dedicated guard model [LIMITATIONS.md](LIMITATIONS.md) lists under "before production". |
| `pii_defense` | **Not adopted, and doubly so here.** It is Presidio-based, and this application installs no Presidio for the reason given [above](#why-masking-is-off-here). Independently, it joins buffered chunks with `str(chunk)` — the defect `text_guardrails` exists to correct. |
| `output_verifier_tools` | **Not applicable.** It asks an LLM whether a tool result is correct. The tool results here are the deterministic engine's output, and the engine — not a second model — is what decides whether a decision is correct. |
| `pre_tool_verifier` | **Not adopted.** Unlike the template, this application *does* have state-changing tools, but they are protected deterministically: every one pauses for a signed human approval, and the MCP server re-verifies the token, recomputes the engine decision and re-checks hard constraints at the point of mutation. An LLM screen of tool arguments would add a probabilistic layer in front of a deterministic one, and in 1.9.0 it defaults to `fail_closed: false`. |

## Compatibility with the pinned release

`nvidia-nat-security[guardrails]==1.9.0` constrains `nemoguardrails` to
`>=0.11,<0.22`, so the pin is 0.21.0. **The 1.9 upgrade did not change this** —
the requirement is byte-identical to 1.8.0's, and NAT's generic Guardrails
middleware still stringifies streamed chunks with `str(chunk)`, which is why
`text_guardrails` remains. That release has three defects on the
streaming path, all fixed upstream in 0.23.0:

1. `detect_regex_pattern` is declared with no `output_mapping`, so NeMo's
   `is_output_blocked` falls back to a default that returns `False` for its dict
   result — **a match never blocks the stream**;
2. `detect_regex_pattern` and `mask_sensitive_data` are declared
   `(source, text, config)` while the streaming runner always injects `context`,
   `llm_task_manager`, `model_name`, `llms` and `llm` — **a `TypeError` before
   any check runs**;
3. `_prepare_params` resolves the `$bot_message` placeholder **in place** into
   the shared, process-wide flow configuration, so the first streamed response
   permanently rewrites it and every later request re-checks the first request's
   text.

`agent/src/nat_streaming_react/guardrails_compat.py` fixes all three from
application code, without modifying the installed package: corrected action
declarations registered through `LLMRails.register_action` (delegating the logic
itself upstream), a flow-parameter guard, and a pool of isolated rails
instances.

The pool exists because the guard is only enough for one request at a time. NeMo
resolves the placeholder into the instance's flow config *while* streaming, so
with two responses in flight one stream's chunks get checked against the other's
text. `verify_guardrails_rails.py` asserts that a single stream destroys the
shared placeholder, which is the deterministic root cause of that race.

**Removal condition:** every helper self-disables once the installed action
declares what it needs. Delete the module when `nvidia-nat-security` relaxes its
Guardrails pin to `>=0.23` and `requirements.txt` is upgraded.

## Configuration

| Variable | Default | Effect |
| --- | --- | --- |
| `GUARDRAILS_INPUT_DETERMINISTIC_FALLBACK` | `true` | Deterministic critical-pattern blocking |
| `GUARDRAILS_INPUT_READ_ONLY_ALLOW_OVERRIDE` | `true` | Allow templates may correct an LLM false positive |
| `GUARDRAILS_INPUT_BLOCK_MESSAGE` | a refusal | What a blocked user sees |
| `GUARDRAILS_INPUT_MAX_CHARS` | `32000` | Ceiling on the user text handed to the guard model; a longer message is refused, never truncated. Floor of 256. |
| `GUARDRAILS_INPUT_OVERSIZE_MESSAGE` | a refusal | What a user over that bound sees |
| `GUARDRAILS_RAIL_POOL_SIZE` | `4` | Concurrent rail evaluations before queueing |
| `GUARDRAILS_TRACE_CAPTURE_CONTENT` | `true` | Prompt/answer text on guardrail spans |
| `GUARDRAILS_TRACE_CAPTURE_RAW_OUTPUT` | `false` | See below |
| `GUARDRAILS_TRACE_MAX_CHARS` | `16384` | Per-attribute bound |
| `GUARDRAILS_PII_MAX_BUFFER_CHARS` | `200000` | Ceiling on a streamed answer buffered for PII masking; an answer over this is refused, not released unmasked. Only relevant when `mask sensitive data on output` is enabled. |

Every boolean is parsed strictly: an unrecognised value keeps the declared
default and logs a warning. It used to become `False`, which for
`GUARDRAILS_INPUT_DETERMINISTIC_FALLBACK` meant a typo silently switched off the
deterministic block patterns.

`GUARDRAILS_TRACE_CAPTURE_RAW_OUTPUT` stays `false`: pre-mask output contains
exactly the PII or secret the rails exist to stop, and enabling it writes that
text into the trace backend.

## Verifying it

```
make verify-input-guardrails    # decision precedence, history forgery, env parsing,
                                # input bound, and the pinned Yes/No verdict parser
make verify-output-guardrails   # config invariants, patterns, masking behaviour
make verify-rails               # the real NeMo runtime, blocking and concurrency
make verify-guardrails          # all three
```

`verify-rails` runs the deployed policy against a deterministic fake rail LLM, so
what is under test is the rail wiring rather than the model. It needs no network.
