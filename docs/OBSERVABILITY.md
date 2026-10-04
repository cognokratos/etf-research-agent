# Observability

One trace per request, covering the agent run and the safety decisions together.

## The problem this solves

NAT builds its workflow/tool/LLM span tree itself and exports it through its own
exporter. NeMo Guardrails emits ordinary OpenTelemetry spans through the
process-wide SDK. Left alone the two pick unrelated trace ids, so MLflow shows
the agent run and the guardrail decisions as two disconnected traces.

They agree if, and only if, they start from the same `(trace_id, root_span_id)`.
`observability/trace_context.py` establishes that pair at the HTTP boundary,
before NAT sees the request, and installs a matching `NonRecordingSpan` as the
ambient OpenTelemetry parent. An inbound W3C `traceparent` is honoured, so an
instrumented caller's trace is joined rather than replaced.

## Pipeline

```
NAT intermediate steps
  → NAT Span
  → WorkflowContentProcessor          readable question/answer, bounded
  → SensitiveHeaderRedactionProcessor credential deny-list
  → SpanToOtelProcessor / batching    NAT built-ins, untouched
  → OTLP/HTTP → OpenTelemetry Collector → MLflow
```

Guardrails spans reach the same collector through the process-wide SDK, carrying
the trace and parent ids established above. Two exporters, one trace.

## Readable content

NAT records the workflow's raw boundary values on the root span: the whole
`ChatRequest` for the input, and for a streaming run a preview list of the first
50 `ChatResponseChunk` objects for the output. Both are accurate and neither is
readable, and with per-token streaming the 50-chunk cap truncates a normal
answer after a few words.

The workflow function (`register.py`) is the one place that sees the whole
request object, so that is where the readable *question* is captured. It is
**not** where the readable *answer* is captured: the guardrail middleware
wraps this function and runs strictly after it, so text produced there is
pre-rail. Recording it as "the answer" would let a masked or blocked response
leak into the trace — which is exactly the bug this pipeline used to have.

### What is captured, and when

The answer is captured at the boundary where `TextGuardrailsMiddleware`
(`text_guardrails.py`) actually releases text downstream, for both response
shapes:

* **Streaming** — `_stream_with_output_rails` accumulates exactly what it
  yields to the caller, in a `finally` that runs on normal completion, on a
  mid-stream block, on any exception, and on cancellation from a client
  disconnect, so a partial answer is never silently dropped. It dispatches to
  one of two paths depending on whether PII masking is configured (see
  `docs/GUARDRAILS.md`): the regex-only path yields many small chunks as the
  rail evaluates them incrementally; the PII-masking path buffers the whole
  answer and yields it once, already masked. Either way, what gets recorded
  here is exactly what got yielded — never the pre-mask buffer.
* **Non-streaming** (and streaming with `stream_output_rails: false`, which
  NAT buffers into a single non-streaming call) — `post_invoke` calls the
  base class's rail evaluation, which may block or mask `context.output` in
  place, and then records whatever value survives that call.
* **Input blocked** — when the input rail blocks a request, the workflow
  function never runs at all, so `pre_invoke` records both the question and
  the released refusal itself; nothing else would ever see them.

Each chunk is still **yielded first and recorded afterwards**, so observability
adds no latency and cannot delay or reorder a token — the recording point
moved to the actual release boundary, not the ordering guarantee.

**This is not proof of browser delivery.** "Recorded answer" means the text
that was handed downstream by the guardrail middleware, not a confirmation
that a byte reached the client; a network failure after that point is outside
what this pipeline can see. It also does **not** mean the LLM and tool spans
are redacted — those are captured and controlled separately (see
`GUARDRAILS_TRACE_CAPTURE_RAW_OUTPUT` below and `docs/GUARDRAILS.md`); fixing
the workflow root span's answer says nothing about what a tool-call span or an
LLM-call span carries.

The guardrail middleware records its own pre/post hashes separately on its
`guardrail.output.regex` span and keeps raw pre-rail text off spans
unless `GUARDRAILS_TRACE_CAPTURE_RAW_OUTPUT` is explicitly enabled — that
switch is independent of the workflow root span's answer described above.

A failed or abandoned run still gets a readable root span: the error is recorded
in a `finally`, so it lands on success, on failure and on client disconnect
alike. A stream that failed halfway keeps its partial answer *and* gains the
error, rather than one replacing the other.

## Redaction, and what it does not cover

`SensitiveHeaderRedactionProcessor` removes credential-bearing headers from span
metadata: `authorization`, `proxy-authorization`, `cookie`, `set-cookie`,
`x-api-key`, `api-key`, `x-auth-token`, `x-csrf-token`, and
`x-authenticated-email`. An email address adds nothing a trace needs and follows
the span into whatever backend stores it.

The gateway's subject id and username are not credentials, so they are not on
that list; they are removed by `UserIdentityProcessor` instead, as part of
per-user attribution below. Roles and `x-request-id` stay: roles do not name a
person, and the request id is what joins a trace to the history row that does
record the actor.

This is a **header deny-list and nothing more**. It does not make spans free of
sensitive data:

* request and response *content* is governed separately, by
  `NAT_TRACE_CAPTURE_CONTENT` here and by the guardrail middleware's own capture
  switches;
* a credential that appears inside a tool result or a model answer is not
  reached by this processor. The output regex rail is what stops that reaching
  the client; telemetry capture of tool results is NAT's own.

The front-end worker already strips `Authorization` from the ASGI scope before
NAT can see it, so this is the second layer, on the principle that a credential
must get past two independent controls to be exported.

## Per-user attribution

NAT 1.9 attributes every span to the authenticated user, writing two keys:
`nat.user.id` (always, `"unknown"` when there is none) and `user.id` (only when
set — backends such as MLflow and Langfuse group traces by it).

`UserIdentityProcessor` decides whether either leaves this process.
**Off unless `OTEL_TRACE_USER_ID=true`.**

What the value is matters to that decision. With
`general.front_end.identity_header` configured, NAT does not put the gateway's
identifier on the span: it derives
`uuid5(namespace, "trusted-header:<header>\x1f<id>")` and exports that.

**Pseudonymity is not anonymity.** The pseudonym is the same value for the same
person on every request, so a trace store holding it can reconstruct one
person's history of questions — and the traces already carry the question and
the answer. It is also an *unkeyed* hash: NAT's namespace is a public constant,
so anyone who knows or can guess a Keycloak subject can recompute the pseudonym
and link that person's traces. It hides the subject from a casual reader of the
trace store, not from someone looking for a particular person. That is why
exporting it is a deployment decision for whoever operates the trace store and
knows its access controls and retention, not a default.

When off, `user.id` is **dropped** rather than masked — a literal placeholder
would become a user in those backends' UIs — and `nat.user.id` is set to
`[redacted]`, which keeps "not exported by policy" distinguishable from NAT's
own `"unknown"`, meaning no identity was resolved at all.

### The raw identity is withheld in both modes

The switch alone is not enough. NAT also copies request headers into
`nat.metadata`, so the gateway's `x-authenticated-user-id` (the Keycloak subject)
and `x-authenticated-username` would be exported verbatim on every request,
whatever the switch says. Measured on NAT 1.9.0 with the switch off: `user.id`
was gone and `nat.user.id` read `[redacted]`, while both raw headers were present
in `nat.metadata` on the workflow span.

`UserIdentityProcessor` therefore redacts those two headers **unconditionally**.
With attribution on, a trace is attributable through NAT's pseudonym only; the
raw subject never leaves the agent. The template this application tracks ships
the switch without this redaction; it is an ETF-side addition, recorded in
[UPSTREAM.md](UPSTREAM.md). Asserted offline by `verify_trace_pipeline.py`.

## Configuration

| Variable | Default | Effect |
| --- | --- | --- |
| `NAT_TRACE_CAPTURE_CONTENT` | `true` | Record the readable question and answer. Disabling still records errors — a failure signal is not request content, and a root span with no output and no reason is what this pipeline exists to avoid. |
| `NAT_TRACE_CONTENT_MAX_CHARS` | `65536` | Per-field bound; truncation is marked with `nat.trace.content_truncated` |
| `OTEL_TRACE_USER_ID` | `false` | Export NAT 1.9's per-user pseudonym (`user.id`, `nat.user.id`). The raw gateway identity is withheld either way. See [Per-user attribution](#per-user-attribution). |
| `OTEL_SERVICE_NAME` | `etf-research-agent` | MLflow experiment / service name |
| `OTEL_COLLECTOR_TRACES_ENDPOINT` | collector | OTLP/HTTP endpoint |

Disabling capture here stops *this package* adding readable attributes. It does
not disable NAT's own raw boundary attributes.

## Reliance on private NAT attributes

Stated rather than glossed. This package is not purely public-API based:

| Private name | Why | Removal condition |
| --- | --- | --- |
| `ContextState._root_span_id` | Pre-seeds the root span id so NAT's exporter and the OpenTelemetry SDK agree on one trace. NAT's own evaluation runtime sets it the same way. | NAT exposes a public way to supply the root span id, or accepts an ambient OTel context for the workflow root |
| `nat.data_models.span._generate_nonzero_span_id` | Generates a span id in exactly NAT's format | NAT exports an equivalent without the leading underscore |
| `OtelSpanExporter._span_prefix` | The attribute-name prefix our processors must match | NAT exposes the prefix publicly |

All three are still private in NAT 1.9.0 (`_span_prefix` now lives on the base
`SpanExporter` that `OtelSpanExporter` inherits); the upgrade removed none of
them.

One further private name, in FastAPI rather than NAT:

| Private name | Why | Removal condition |
| --- | --- | --- |
| `FastAPI._telemetry` | FastAPI 0.142 (resolved transitively) traces every request natively whenever a global tracer provider exists — which `otel_setup` must create — and at startup adds a second OTLP exporter to that provider. NAT builds the app without FastAPI's public `telemetry=` argument, so `fastapi_worker.disable_fastapi_native_telemetry` switches it off in place before the server starts. Measured before the fix: a `GET /health` trace every few seconds, crowding the workflow traces out of `make trace-test`'s window. | NAT exposes FastAPI's constructor arguments |

`verify_trace_pipeline.py` asserts the switch is still found and still disables
FastAPI's per-request decision, so a FastAPI rename fails a check instead of
silently doubling exports.

The first two are resolved at import time, so a NAT upgrade that renames them
fails loudly at startup rather than silently producing split traces. The third
is read through `getattr(..., "nat")`, so a rename degrades to reduced
observability rather than a crash. The list is also in
`observability/__init__.py` as `NAT_PRIVATE_API_DEPENDENCIES`, and
`scripts/verify_security_sources.py` asserts it stays declared.

## Verifying it

```
make verify-trace-pipeline   # offline: context, bounds, redaction, errors
make trace-test              # + live: real requests, asserted against MLflow
make traces                  # print the span tree of recent MLflow traces
```

`verify-trace-pipeline` uses a real `TracerProvider` with an in-memory exporter,
because a no-op tracer would return the ambient context unchanged and the
parenting assertion would pass without proving anything.
