# Upstream template, and how to port fixes from it

This application was built on the general-purpose agent template at
[cognokratos/simple-agent-template](https://github.com/cognokratos/simple-agent-template).
It is a separate repository, not a branch of it, and that is deliberate: the two
share roughly a quarter of their files and disagree on a security default, so
neither can be the other's configuration.

Splitting them does not sever the link. The template is still upstream, and its
fixes are still worth taking — deliberately, one at a time, rather than by
replaying this whole application on top of a moving base.

## Add the remote once

```bash
git remote add upstream git@github.com:cognokratos/simple-agent-template.git
git fetch upstream
```

## Port a specific fix

Cherry-pick it when it lands only in files listed as shared below — those are
byte-identical, so it applies cleanly:

```bash
git log --oneline upstream/main          # find the commit
git cherry-pick -x <sha>                 # -x records the origin in the message
```

`-x` matters: it appends `(cherry picked from commit <sha>)`, which is the only
durable record of what has already been taken. Without it, deciding whether a
given upstream fix is present becomes archaeology.

If the fix touches a file this application rewrote, do not force the
cherry-pick. Read the upstream diff and reimplement the intent here. The
rewritten files are rewritten because the domain differs, not because they
drifted. A large upstream change — a framework upgrade, say — is usually both:
take it as a semantic port, record it under *Last upstream synchronisation*
below, and say plainly that it was adapted rather than cherry-picked, since
`-x` provenance will not exist for it.

## Last upstream synchronisation

| | |
| --- | --- |
| Template | `cognokratos/simple-agent-template` |
| Upstream commit | `af29ce0b27dce31edd020bf8c742072b69c2aadb` — *feat(agent)!: upgrade to NeMo Agent Toolkit 1.9, require identity (#1)* |
| NAT version | 1.9.0 (`nemoguardrails` 0.21.0, unchanged) |
| Porting method | Semantic/manual port of the infrastructure changes, on branch `upgrade/template-1.9`. Not a cherry-pick of the commit and not a merge: hunks were applied with a three-way merge where a file was still shared, and reimplemented where it was not. |

`upstream/main` has since advanced past `614f3fe` (*Tutorial (#2)*). That commit is
the template's learning layer — learning path, concept pages, labs, a
docs-link checker — plus four source comments pointing into it. It changes no
runtime behaviour, and its curriculum is **deliberately not ported**: this
repository links to it and carries its own applied curriculum
([APPLIED-LEARNING-PATH.md](APPLIED-LEARNING-PATH.md)), which starts where the
template's ends. The docs-link checker *was* taken, byte-identical —
`scripts/verify_docs.py` and `scripts/verify_docs_test.py`, run by
`make docs-check` — because a curriculum that points into code needs the same
drift check. The infrastructure synchronisation point is therefore still
`af29ce0`, not `upstream/main`.

This records one synchronisation, not a subscription. Later template changes are
not in this repository until someone ports them.

### What the 1.9 port took

* **Dependencies.** `nvidia-nat`, `-langchain[openai]`, `-opentelemetry`,
  `-security[guardrails]` and `-mcp` at 1.9.0, plus an explicit
  `sqlalchemy[asyncio]`. The resolved set shrank from 190 to 162 packages; no
  application code imported anything that disappeared.
* **Identity.** `general.front_end.identity_header`,
  `RequireIdentityHeaderMiddleware`, the exactly-once parser shared with
  `ResponderIdentityMiddleware`, and a synthetic `EVALUATION_PRINCIPAL` for the
  harness and its `/version` probe.
* **Observability.** `UserIdentityProcessor` and the `OTEL_TRACE_USER_ID`
  switch, off by default.
* **Guardrails.** The `GUARDRAILS_INPUT_MAX_CHARS` bound (refuse, never
  truncate) and the pinned `is_content_safe` verdict behaviour.
* **Verification.** The four-case `auth-test`, the identity wiring in
  `verify_security_sources`, and `verify_llm_config`'s unwrapping of NAT 1.9's
  `RunnableConfigurableFields`.

Every claim the upstream commit made about NAT 1.9.0 was re-checked against the
installed package, not taken from the commit message: the Guardrails pin, the
`str(chunk)` stringification, the owner-less interaction route, the ReAct
`Final Answer:` buffering, the unconditional `enable_interactive=True` on the
workflow routes, and the `configurable_fields` wrapper. All held, so no local
workaround module was deleted.

### What it deliberately did not take

| Upstream change | Why not |
| --- | --- |
| `nemoguardrails[sdd,tracing]` | This application installs `[tracing]` only. Generic NER masking corrupts ISINs and figures; see [GUARDRAILS.md](GUARDRAILS.md#why-masking-is-off-here). The 1.9 resolution was checked to pull in no Presidio or spaCy. |
| `HITL_ENABLE_INTERACTIVE` default `false`, approvals opt-in | Approvals are mandatory here; the default stays `true` and `HITL_APPROVAL_SECRET` stays required. |
| Ticket-domain wording, `EVALUATION_TOOL_NAMES` defaults | Domain vocabulary; the ETF values were kept. |
| `docs/EXTENDING.md`, `docs/TEST-SCENARIOS.md` | Not carried by this repository. Their substance — why each workaround survives 1.9, and the four auth cases — is in [LIMITATIONS.md](LIMITATIONS.md), [SECURITY.md](SECURITY.md) and [VERIFICATION.md](VERIFICATION.md). |
| The Presidio OOM note in `LIMITATIONS.md` | Presidio is not installed here, so the failure mode cannot occur. |
| `614f3fe` tutorial layer and its source comments | Educational; see above. Only the docs-link checker was taken. |

### Where this application went further than the template

Each of these was found while verifying the port and is worth offering upstream,
where the same defect exists:

* **The raw identity leaked into traces regardless of the switch.** NAT copies
  request headers into `nat.metadata`, so with `OTEL_TRACE_USER_ID=false` the
  template still exports the gateway's `x-authenticated-user-id` (the Keycloak
  subject) and `x-authenticated-username` verbatim. Measured on a live span
  here. `UserIdentityProcessor` now redacts both in every mode
  (`observability/trace_processor.py`, `verify_trace_pipeline.py`).
* **`verify_llm_config` asserted against the ambient environment.** The new
  "empty parameter is absent from the built client" check read whatever
  `LLM_REASONING_EFFORT` the deployment had, and both repositories default it
  to `none` — so it failed on the shipped configuration. It now builds the
  client with the value pinned, in both directions (`""` → absent,
  `none` → sent).
* **FastAPI's native telemetry was duplicating export.** A fresh resolution
  pulls FastAPI 0.142, which traces every request — health probes included —
  and adds a second OTLP exporter to the global provider at startup. It made
  `make trace-test` fail here (workflow traces crowded out by `GET /health`).
  `fastapi_worker.disable_fastapi_native_telemetry` switches it off; the
  template's worker would need the same once it is rebuilt against the same
  FastAPI. This is why `fastapi_worker.py` is no longer byte-identical.
* **Offline proof of the identity boundary.** `IdentityBoundaryTests` in
  `agent/verify_approval_tokens.py` drives the real middleware stack over ASGI
  for the same matrix `auth-test` covers live, plus a repeated-identity approval
  response; `DirectCallerIdentityTests` asserts the harness and `/version` probe
  each send exactly one synthetic principal. Source checks now assert middleware
  *order* and use of the exactly-once parser, not only presence.

## What is actually shared

Recomputed after the 1.9 port, against both `af29ce0` and `upstream/main` (the
two lists are the same). These files are byte-identical and can take an
upstream fix by cherry-pick:

| Area | Files |
| --- | --- |
| Agent plumbing | `llm_config.py`, `provenance.py`, `__init__.py`, `agent/.dockerignore`, `agent/verify_mcp_auth.py` |
| Trace pipeline | `observability/otlp_exporter.py`, `trace_content.py`, `trace_context.py`; `observability/otel-collector.yml` |
| Evaluation core | `evaluation/runner.py`, `evaluation/datasets.py` |
| Gateway core | `gateway/src/http.rs`, `gateway/src/state.rs` |
| UI | `ui/app/api/gateway/_proxy.ts`, `auth/{callback,login,session}/route.ts`, `ui/Dockerfile`, build config and `package-lock.json`, `ui/scripts/verify-nat-wire.mjs` |
| Checks | `scripts/verify_security_config.py`, `verify_mcp_auth.py`, `verify_evaluation_artifacts*.py`, `verify_live_evaluation_upload_condition_test.py`, `verify_pii_buffer_env_test.py`, `inspect_mlflow_traces.py`, `verify_docs.py`, `verify_docs_test.py` (the last two against `upstream/main`, not `af29ce0`) |
| MCP inspector | `mcp-server/inspector/{Dockerfile,entrypoint.sh}` |

Shared in substance but **no longer byte-identical**, so an upstream change
here needs reimplementing rather than cherry-picking:

| File | Why it differs |
| --- | --- |
| `fastapi_worker.py` | The FastAPI telemetry switch above. Otherwise it is the 1.9 template file verbatim (`upstream/main` additionally carries one tutorial comment pointing at a concept page this repository does not have). |
| `observability/trace_processor.py` | The identity-header redaction above |
| `agent/verify_llm_config.py` | The deterministic built-client check above |
| `guardrails_compat.py`, `observability/__init__.py` | Comment-only: the upstream text still names 1.8.0. Converges once upstream corrects it. |
| `interaction_guard.py` | One word ("application"), and a corrected docstring: the exactly-once refusal comes from `RequireIdentityHeaderMiddleware`, not from NAT |
| `text_guardrails.py`, `register.py`, `approval.py`, `config.yml`, the verify suites, `evaluation/client.py`, `scorers.py`, `config.py`, the Compose file, Makefile, `.env.example` | Domain divergence: ETF vocabulary, mandatory approvals, three state-changing actions, five suites |

That list will drift. Compute the current truth instead of trusting it:

```bash
# files identical to upstream (safe to cherry-pick into)
git fetch upstream
comm -12 <(git ls-tree -r --name-only HEAD | sort) \
         <(git ls-tree -r --name-only upstream/main | sort) |
while read -r f; do
  [ "$(git rev-parse HEAD:"$f")" = "$(git rev-parse upstream/main:"$f")" ] && echo "$f"
done
```

Swap `=` for `!=` to list the divergent ones — the files where an upstream fix
needs reimplementing rather than applying.

## Do not rebase this application onto upstream

It has been tried. The template and this application independently renamed the
same base identifiers — realm, cookie names, crate names, database and volume
names — so a rebase conflicts on roughly fifteen files for no semantic reason at
all, every time, in `gateway/src/config.rs`, `cookies.rs`, `oidc.rs`, both
Dockerfiles and the UI routes. The conflicts carry no information and resolving
them teaches you nothing. Cherry-pick the commit you want instead.

## The longer-term fix

Cherry-picking manages duplication; it does not remove it. If a second
application is ever built on this template, the shared core should be extracted
into versioned artifacts — a Python package for `nat_streaming_react`, a
workspace crate for the gateway core — so each application depends on a version
rather than on a diff. Until then, the table above is the duplication, and this
document is how it is kept honest.
