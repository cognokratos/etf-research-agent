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

`nvidia-nat-security[guardrails]==1.8.0` pins `nemoguardrails>=0.11,<0.22`, so
0.23.0 — which fixes three streaming rail defects — cannot be installed.
`guardrails_compat.py` works around them from application code and self-disables
once the installed release is correct. Delete it when the pin allows `>=0.23`.

The observability package relies on three private NAT attributes, each listed
with its removal condition in `observability/__init__.py` and
[OBSERVABILITY.md](OBSERVABILITY.md). This is **not** a purely public-API
implementation.

`nvidia-nat[langchain]` pulls the full NAT LangChain dependency set, because
NAT 1.8's supported `react_agent` lives there and exposes no OpenAI-only extra.

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
