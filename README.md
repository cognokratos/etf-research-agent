# ETF Research Agent

A deterministic ETF evaluation engine behind a guarded LLM agent, where every
state change requires a signed human approval and lands in an append-only
history.

```text
Browser / assistant-ui
    ↓ Keycloak login; opaque HttpOnly BFF session; CSRF
Rust authentication gateway
    ↓ static service credential; gateway-minted identity headers
NeMo Agent Toolkit ReAct workflow      ← NeMo Guardrails input/output rails
    ↓ static service credential        ← NAT interaction pause for human approval
Rust MCP server                        ← deterministic rules engine; approval verifier
    ↓ parameterized SQLx queries
PostgreSQL                             ← append-only audit, single-use approval nonces

NAT + Guardrails spans ──OTLP──▶ OpenTelemetry Collector ──▶ MLflow
```

assistant-ui is the only application service reachable from the browser. The
gateway, NAT, MCP and the database publish no host ports and sit on segmented
networks. Any OpenAI-compatible model endpoint works; the defaults target a
local Ollama.

**The model does not make the decision.** A deterministic engine in Rust scores
each fund against a configured investor profile and a versioned rules
specification, and that result is the default put to the investor. The model
searches, explains and proposes; it may recommend the engine's decision or a
more conservative one, never a more optimistic one. Moving a decision at all
takes an authenticated human, a typed rationale, and a signed token the model
never sees.

**This system ends at decision support.** There is no brokerage connection, no
market-data feed and no positions. "Shortlisted" means recorded as a candidate.
Scores measure quality and fit against a dated snapshot; they are not forecasts,
not advice, and not trade recommendations.

Built on the general-purpose template in this repository's `main` branch —
authentication, guardrails, approvals, tracing, evaluation harness and
deployment are shared with it; the rules engine, the ETF universe, the tools,
the prompts and the suites are this application's.

## Start

```bash
make env          # create .env from .env.example
make pull-models  # no-op unless LLM_BASE_URL is an Ollama endpoint
make dev          # build and start everything
make wait         # readiness
make open-ui      # http://localhost:3000
```

Sign in with `researcher` / `researcher`. Then try:

```
Show me the highest-rated ETF candidates
Evaluate VWCE-XETRA and explain every score component
Why is AGGH-XETRA marked research instead of shortlist?
Show the decision history for VWCE-XETRA
```

Those are single-tool or two-tool questions and answer reliably on the shipped
default model, `qwen3:8b`. The longer trajectories — comparing two funds, or
committing a decision — ask more of a small model's tool orchestration; see
[docs/CONFIGURATION.md#qwen38b-and-multi-tool-trajectories](docs/CONFIGURATION.md)
for what that does and does not tell you, and note that a weak *answer* can
never become a wrong *decision*.

### Recording a decision

Ask to commit one — "Commit the evaluation for ESPO-XETRA" — and the agent
evaluates first, then an approval card appears offering all three decisions with
the engine's own answer labelled *Confirm* and the others labelled *Override*.
Choosing anything other than the engine's decision requires a typed rationale;
choosing `shortlist` also requires a grounded research note, and you are asked
for one if the model did not draft it. The choice is bound to a signed token
carrying the authenticated actor, the originating request, the deterministic
decision you were shown and the exact note — verified independently by the MCP
server, which re-derives the evaluation under a row lock, re-checks the hard
constraints, and applies the mutation, the nonce and the history insert in one
transaction.

A refusal after approval is the control working, not a gap: a non-UCITS fund
cannot be shortlisted however anyone votes. The agent is told plainly that
nothing was applied.

[docs/DEMO.md](docs/DEMO.md) is a full walkthrough.
[docs/APPROVALS.md](docs/APPROVALS.md) is the trust boundary.

## What this gives you

| | |
| --- | --- |
| **Deterministic decisions** | A versioned rules engine in Rust: weighted score components with explicit renormalisation for missing data, decision bands, non-bypassable hard constraints, and policy caps. Validated at boot; asserted against every shipped fund and 20 labelled cases with no model involved |
| **Authentication** | Keycloak OIDC with PKCE, server-side tokens, opaque sessions, CSRF, strict cookie attributes |
| **Isolation** | Seven Compose networks, one per trust relationship, asserted statically *and* at runtime |
| **Guardrails** | NeMo input self-check with deterministic override layers; streaming secret blocking. PII masking is implemented but deliberately off — see [docs/GUARDRAILS.md](docs/GUARDRAILS.md) |
| **Observability** | One trace per request covering the agent run *and* the guardrail decisions, with readable question/answer and credential redaction |
| **Evaluation** | Five MLflow suites with deterministic scorers, latency distributions, and provenance linking every result to the agent that produced it |
| **Approvals** | A signed-approval boundary on all three state-changing actions, with the human holding the initiative: every decision is offered, not just the one the model proposed |
| **Audit** | Append-only by database trigger, with the rules and profile versions in force at the time, so an old decision stays interpretable after the policy moves |

## Documentation

| Document | For |
| --- | --- |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | The request path, trust boundaries, network segmentation, where the model is and is not trusted |
| [SECURITY.md](docs/SECURITY.md) | Each control, why it exists, and how to check it |
| [CONFIGURATION.md](docs/CONFIGURATION.md) | Every setting, and what the shipped model does and does not handle |
| [GUARDRAILS.md](docs/GUARDRAILS.md) | Input and output rails, and what the pinned Guardrails release actually does |
| [OBSERVABILITY.md](docs/OBSERVABILITY.md) | The trace pipeline, content capture policy, and what redaction does not cover |
| [EVALUATION.md](docs/EVALUATION.md) | The suites, the scoring methodology, and provenance |
| [APPROVALS.md](docs/APPROVALS.md) | The human-approval boundary and its four layers |
| [VERIFICATION.md](docs/VERIFICATION.md) | What you can check, what it needs, what it proves |
| [LIMITATIONS.md](docs/LIMITATIONS.md) | Known gaps, untested behaviour, and production prerequisites |
| [DEMO.md](docs/DEMO.md) | A full walkthrough: prompts to type, and what should happen |
| [ACCEPTANCE.md](docs/ACCEPTANCE.md) | What was claimed, and what establishes it |
| [EVALUATION_ANALYSIS.md](docs/EVALUATION_ANALYSIS.md) | The measured figures, and how to read them |

## Verify it

```bash
make static-check   # no Docker, no cluster, no model
make etf-check      # fixtures, investor profile, rules spec, labelled cases
make rules-test     # the deterministic engine itself
make test           # everything, with the cluster up
make security-test  # authentication and topology boundaries
make verify-hitl    # a human INITIATES an override, end to end
make eval-all       # the five evaluation suites; needs a model
```

The first three need nothing but `python3`, `node` and `cargo`. They are also
the ones that establish the claims about the *decision*: `rules-test` asserts
every shipped fund and every labelled case against the shipped engine, and
regenerates `evaluation/results/deterministic-etf-baseline.json`, which CI
fails on if the committed bytes stop matching.

`make help` lists every target.

## Repository layout

| Path | |
| --- | --- |
| `ui/` | assistant-ui on Next.js |
| `gateway/` | Rust backend-for-frontend: OIDC, sessions, CSRF, proxying |
| `agent/` | NAT workflow, guardrail middleware, observability, the three approval functions |
| `mcp-server/` | The rules engine (`rules.rs`), the read models (`domain.rs`), the tools (`server.rs`), SQL (`store.rs`), and the approval verifier (`approval.rs`) |
| `data/` | The ETF universe, the investor profile, the rules specification, and the labelled cases |
| `evaluation/` | MLflow suites, deterministic scorers, provenance, published results |
| `db/`, `keycloak/`, `observability/` | Schema, realm generation, collector config |
| `scripts/` | Checks that run without the cluster, fixture validation, injection payloads, trace tooling |

## Notes

**NAT ReAct prompt compatibility.** The custom `system_prompt` must contain the
`{tools}` and `{tool_names}` placeholders. NAT replaces them at startup with the
discovered MCP tool descriptions and names.

**No site-packages are modified.** Earlier revisions patched installed NAT and
Guardrails code at image-build time. That is now application code reached
through supported extension points, with regression suites proving the behaviour
it replaced. Where private NAT attributes are still relied on, they are named
with their removal conditions in
[OBSERVABILITY.md](docs/OBSERVABILITY.md).

**Dependency trade-off.** NAT 1.8's supported `react_agent` lives in the NAT
LangChain plugin and exposes no OpenAI-only extra, so this installs the full
LangChain dependency set. The expensive layer is cached, and the compiler needed
by `annoy` stays in the builder stage.

**Licensing.** Source files under `agent/src/` and `gateway/Cargo.toml` declare
Apache-2.0. There is no root `LICENSE` file; see
[LIMITATIONS.md](docs/LIMITATIONS.md#licensing).
