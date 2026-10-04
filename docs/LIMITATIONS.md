# Known limitations and untested behaviour

Stated rather than implied. A control that is documented but unverified is worse
than one that is absent, because it is believed.

## Not tested automatically

| Behaviour | Why not | How to check by hand |
| --- | --- | --- |
| Nonce conflict under real concurrency | Needs a live PostgreSQL; the constraint is a primary key, enforced by the database | Two concurrent spends of one approval token against a running cluster |
| Rollback of a failed audit insert | Same | Break the audit insert and confirm the status is unchanged and the nonce free |
| End-to-end approval through a real browser | Needs a cluster, a model, and a human at the keyboard | `make dev`, then follow [DEMO.md](DEMO.md). `make verify-hitl` drives the same path with scripted answers and no browser |
| Keycloak login through a real browser | Needs the cluster | `make dev`, then sign in |
| The live evaluation suites' actual scores | Non-deterministic and model-dependent | `make eval-all` with a model available; the figures last measured are in [EVALUATION_ANALYSIS.md](EVALUATION_ANALYSIS.md) |
| The *deterministic* engine's scores | — | Fully tested and gated: `make rules-test` asserts every shipped fund and every labelled case, with no model involved |
| Trace export reaching MLflow | Needs the cluster and a model | `make trace-test` |

The `make` targets above exist and are documented; they are simply not part of
any automated gate.

## Deliberate gaps

**A fabricated prior *user* turn is not screened.** The input rail screens the
latest turn and any client-supplied *assistant* turn. It does not re-screen prior
user turns, because doing so made one refusal poison the rest of a conversation.
The same caller can send that text as the latest turn, where the full rail does
screen it. See [GUARDRAILS.md](GUARDRAILS.md).

**There is no PII protection on the output rail.** This application enables
deterministic secret-leakage patterns only, and does not install Presidio. That
is a deliberate domain decision — generic NER masking corrupts the ISINs,
expense ratios, fund sizes and scores that *are* the answer — and it is the right
trade only as long as the ETF snapshot carries no personal data. It ships with
none. If yours does, this decision has to be revisited; see *Why masking is off
here* in [GUARDRAILS.md](GUARDRAILS.md) for the three coordinated changes that
turn it on.

The masking path itself remains implemented and tested, and
`verify_output_guardrails.py` skips those checks cleanly when Presidio is absent
rather than passing them vacuously.

**Header redaction is not content redaction.** The telemetry processor removes
credential-bearing headers. A secret inside a tool result or a model answer is
not reached by it. See [OBSERVABILITY.md](OBSERVABILITY.md).

**NAT's own `identity_header` refusal is advisory on the workflow routes.**
Configured, NAT 1.9 raises `IdentityHeaderError` for a missing, empty or
repeated identity header, but on the workflow routes the interactive runner
catches it into a `200` response body. `RequireIdentityHeaderMiddleware` in
`fastapi_worker.py` is what actually enforces the requirement; `make auth-test`
and `IdentityBoundaryTests` assert it. See
[SECURITY.md](SECURITY.md#the-agent-requires-an-asserted-identity).

**The service credential carries the authority to assert any identity.** NAT
believes whatever `x-authenticated-user-id` a key-holding caller sends, and the
approval boundary binds to it. The gateway is one key holder; the evaluator,
which receives the same `AGENT_API_KEY`, is the other, and could in principle
assert a real researcher's identity rather than `evaluation-harness`. This
predates the 1.9 upgrade — 1.9 only made the assertion mandatory — and is bounded
by the evaluator being an opt-in profile on an internal network. Separate
per-caller credentials, or workload identity, would close it.

**Per-user trace attribution is off by default, and a pseudonym when on.** NAT
1.9 stamps every span with the user; `OTEL_TRACE_USER_ID=true` exports it. The
value is NAT's `uuid5` of the gateway subject under a *public* namespace, so it
is not anonymity: anyone who knows a subject can recompute it and link that
person's traces. The raw subject and username are redacted from span metadata in
both modes. See [OBSERVABILITY.md](OBSERVABILITY.md#per-user-attribution).

**Answers misstate the expense ratio by a factor of a hundred.** The MCP read
model returns rates as bare fractions (`"ter": 0.0022` is a 0.22% TER) and nothing
tells the model so; it routinely writes "0.0022%". Measured on 2026-10-04: four of
six grounding answers on every run, 11 of 12 TER statements in the `evaluation`
suite, 5 of 7 in `injection`. Decisions are unaffected — the engine scores the
fraction — but the stated figure is wrong. `research_units_correct` measures it;
the fix belongs in what the tools return. See
[EVALUATION_ANALYSIS.md](EVALUATION_ANALYSIS.md).

**Sessions are in memory.** One gateway instance, and a restart logs everyone
out.

**An interaction with no recorded owner is allowed through** unless
`HITL_STRICT_INTERACTION_OWNERSHIP=true`, so NAT's own OAuth consent flow keeps
working. Every interaction the three approval functions create *is* recorded, so
this affects nothing in the shipped configuration.

**The advisory ceiling is enforced, not the model's honesty.** A model may never
assert a recommendation more optimistic than the engine's decision, and the MCP
refuses a token that does. What no layer can check is whether the model reported
the engine's decision faithfully in its *prose* — so the decision the mutation
applies is read from the signed claims and re-derived from a recomputed
evaluation, never from what the answer said. The `evaluation` suite measures the
prose; the boundary does not depend on it.

## Resource requirements

Not an issue in the shipped configuration: Presidio and spaCy's
`en_core_web_lg` are not installed, so `make verify-output-guardrails` skips the
masking checks and stays small.

It becomes one if you enable masking. The analyzer pulls roughly 600 MB into
memory on top of the agent's own footprint, and on a Docker VM already near
capacity the kernel kills it — surfacing as a bare `exit 137` rather than a
failing assertion. The script warns before that point. Give Docker headroom, or
run it on the host.

## Dependency constraints

`nvidia-nat-security[guardrails]==1.9.0` pins `nemoguardrails>=0.11,<0.22`, so
0.23.0 — which fixes three streaming rail defects — cannot be installed.
`guardrails_compat.py` works around them from application code and self-disables
once the installed release is correct. Delete it when the pin allows `>=0.23`.

**The 1.9 upgrade did not relax this, and made no local workaround deletable.**
Checked against the installed 1.9.0 rather than its release notes: the
Guardrails requirement is unchanged; NAT's Guardrails middleware still
stringifies streamed chunks (`text_guardrails.py`); the interaction-response
route still resolves with no owner check (`interaction_guard.py`); the ReAct
`_stream_fn` still buffers until `Final Answer:` (`register.py`); and YAML
interpolation still cannot express an absent parameter (`llm_config.py`).

The observability package relies on three private NAT attributes, each listed
with its removal condition in `observability/__init__.py` and
[OBSERVABILITY.md](OBSERVABILITY.md). This is **not** a purely public-API
implementation, and all three are still private in 1.9.0.

NAT 1.9 split the LangChain plugin's provider integrations into extras, so the
former full-plugin install is gone: `nvidia-nat-langchain[openai]` only, 29
fewer packages. `sqlalchemy[asyncio]` is declared explicitly because NAT's
execution store needs `greenlet` and nothing else in the set declares it.

Two consequences of resolving the 1.9 set that are not NAT changes, both
pinned down rather than worked around blindly:

* **A harmless Guardrails warning at rail load.** The set resolves
  `langchain-community` 0.4.2, which no longer exports `GoogleSearchAPIWrapper`,
  so nemoguardrails 0.21 logs that it could not register its optional LangChain
  search actions ("The langchain_community module is not installed"). No rail
  here uses them, and `verify-rails` exercises the real runtime. Not pinned
  away: adding a constraint for an unused feature would be a dependency for
  nothing.
* **FastAPI's native telemetry is switched off.** FastAPI 0.142 instruments
  requests on its own and adds a duplicate OTLP exporter; the agent worker
  disables it through a FastAPI-private attribute. See
  [OBSERVABILITY.md](OBSERVABILITY.md#reliance-on-private-nat-attributes).

## Before production

This is a local demonstration. Add:

* authorization and tenant/user scoping in every SQL query — the MCP tools
  currently return any row the query matches;
* secrets management instead of the demo credentials in `docker-compose.yml`;
* database migrations rather than a one-time init script;
* pagination and response-size limits for history-heavy records;
* access controls, retention and redaction for OpenTelemetry and MLflow data;
* a dedicated low-latency guard model rather than sharing the application LLM;
* explicit image digest pinning and vulnerability scanning;
* a session store that survives a restart and supports more than one instance.

## Licensing

The repository declares **Apache-2.0** in `gateway/Cargo.toml` and in the SPDX
headers of the Python sources under `agent/src/`. There is no root `LICENSE`
file.

An earlier revision of this branch added an **MIT** `LICENSE` at the root while
leaving those Apache-2.0 declarations in place, which is a conflict rather than a
choice. It is not carried here. Adding a root licence is the right thing to do —
but it has to match the in-source declarations, or those have to change
deliberately; not both at once, and not silently.
