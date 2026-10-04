# Human approval for state-changing actions

**Required, and on in the shipped configuration.** Recording an approved
research decision is what this application does, so unlike the template it is
built on — where the sample is read-only and approvals are an opt-in
demonstration — the boundary here is not optional. The MCP server refuses to
start without the shared secret, and the three state-changing functions are
registered unconditionally.

## The three actions

| Function | Action | Choice | Carries |
| --- | --- | --- | --- |
| `commit_evaluation` | `commit` | the decision, from all three | the advisory recommendation and a grounded note |
| `shortlist_etf` | `shortlist` | `shortlist` only | a grounded note |
| `assign_etf` | `assign` | none | the research owner |

Each pauses for a human and then applies the change itself. There is no separate
approval step and no token for the model to carry.

## What must be configured

1. `HITL_APPROVAL_SECRET` — ≥ 24 characters, **identical** for the agent and the
   MCP server. Both enforce the length independently.
2. `HITL_ENABLE_INTERACTIVE=true` so NAT mounts its interaction endpoints.

Both default to working values in `docker-compose.yml`. What CI asserts is not
that the surface is absent but that it is *consistent* — see
`scripts/verify_approval_surface.py`. A half-configured boundary is the failure
worth catching: two different secrets, or a secret on one side only, starts
cleanly, serves reads, and then fails after a human has already decided.

## The flow

```
model calls the approval function with its proposal
  → NAT pauses the workflow and emits `event: interaction_required`
  → the UI renders an approval card in the thread
  → the human chooses; a change requires them to type a reason
  → the response is proxied: authenticated, CSRF-checked
  → the interaction guard checks ownership and the offered choice
  → the workflow resumes and mints a signed token
  → the MCP server verifies it and applies the change in one transaction
  → the model is told what happened; it never restates the payload
```

Authorization and effect are one step. Nothing between the human's confirmation
and the state change depends on further model output, so a request can never end
up approved but unapplied — and the model never gets an opportunity to alter
what was approved.

## Four layers, none trusted alone

| Layer | Checks | Does not check |
| --- | --- | --- |
| **Gateway** | shape, size, encoding, UUID form, protocol-level confirm/cancel consistency | which choices are legitimate — it cannot know, for an arbitrary application |
| **Interaction guard** | the responder owns the execution; the submitted id **and** value, together, are one *this* prompt actually offered as a pair; the response type matches the prompt type | anything about the resulting mutation |
| **Agent** | mints a token binding action, resource, actor, request, the engine decision the prompt displayed, exact payload | whether that displayed decision is true — it is the model's report of the engine — and anything about current state, which has moved by the time it is applied |
| **MCP server** | signature, every binding, lifetime ceiling, re-derived state under a row lock, transition policy, single use | — |

### The gap the interaction guard closes

NAT's `POST /executions/{e}/interactions/{i}/response` calls
`ExecutionStore.resolve_interaction` and nothing else. It does not consider who
is asking, and `ExecutionRecord` carries no owner. In stock NAT, **knowing two
UUIDs is sufficient authority to answer somebody else's approval prompt**, with
any choice the schema permits.

`OwnerAwareExecutionStore` substitutes for NAT's store — a supported extension
point, since the worker assigns `self._execution_store` in `__init__` — and
checks both properties before resolution. Ownership is captured where each side
can see it: the prompt's actor from the workflow task's inherited contextvars,
the responder's from a pure-ASGI middleware on the response request.

An interaction this guard never saw created (NAT's OAuth consent flow) has no
recorded owner; those are allowed through and logged, because refusing them
would break a NAT feature. `HITL_STRICT_INTERACTION_OWNERSHIP=true` makes even
that case fail closed, for a deployment where approvals are the only interaction
type.

## The token

HMAC-SHA256 over a base64url claim set. Claims:

| Claim | Meaning |
| --- | --- |
| `action`, `resource_id` | what, to which record |
| `actor_id` | the authenticated human, from the gateway header — never the model |
| `request_id` | the one authenticated request this approval belongs to |
| `choice`, `expected_choice` | what the human picked, and the engine decision the prompt displayed — as the model reported it, not fetched by the approval layer |
| `override_requested` | recorded, **never trusted**: re-derived at the point of mutation |
| `rationale` | required for an override |
| `payload`, `payload_sha256` | application-owned fields, carried inside the signature |
| `exp`, `nonce` | lifetime and single-use identity |

The token **is** the payload. Every mutation parameter is read from the signed
claims rather than from tool arguments, so the model cannot alter, drop or
re-draft any part of what the human approved.

Every `payload` field that originates with the model, like `note`, is displayed
to the human, labelled as model-supplied and not verified, in the same prompt
where they approve or cancel. One model-originated claim is *not* labelled that
way: `expected_choice`, which the prompt presents as the engine's decision — see
the next section. The prompt-building code normalizes each such field exactly
once and reuses that value for display, signing and persistence, so what the
human read is provably what got signed: there is no second read of the raw
request that display and signing could disagree on. Signing content nobody
showed the approver would not be a human approval of it.

### The displayed premise is a claim; the recomputation is the check

Four values must not be confused:

| Value | Where it comes from | Trusted? |
| --- | --- | --- |
| **The engine's decision** | `rules::evaluate` over the row, the rules and the profile | yes — it is the decision |
| **The model-reported decision** | the `rules_decision` argument of the model's call to `commit_evaluation` or `shortlist_etf` | no |
| **The displayed premise** | the prompt's *"Deterministic engine (authoritative)"* line; today it *is* the model-reported decision, and it decides which option is labelled *Confirm* and whether a rationale is requested | no |
| **`expected_choice`** | the displayed premise, signed into the token | no — signed, not verified |

At the point of mutation the MCP server locks the row, recomputes the engine's
decision, and refuses the token if `expected_choice` differs from it. That catches
both ways a premise can be wrong: the resource or the policy moved between display
and approval, or the model misreported the engine in the first place.

```text
the human saw a premise   ≠  the premise is authoritative
a premise was signed      ≠  the premise is true
backend recomputation     =  the authoritative check
```

The result is that a false premise can never be applied — mutation integrity
holds — while a human can still be *shown* one before deciding. That consent gap
is a known limitation, not a solved problem; see
[LIMITATIONS.md](LIMITATIONS.md#approval-prompts-can-display-a-model-misreported-deterministic-decision).

The minter caps its own TTL at 30 minutes, and the verifier enforces its own
independent ceiling — the minter is not the trust boundary. Expiry is strict;
the 60-second skew tolerance applies only to the lifetime ceiling, because
leniency on expiry would extend the window an approval stays spendable.

## Transactional integrity

One transaction, in this order (`commit_evaluation`, `shortlist_etf` and
`assign_etf` in `mcp-server/src/server.rs`):

1. lock the resource row (`SELECT … FOR UPDATE`) and re-derive the
   authoritative state from it;
2. verify the token against that state, and re-validate the transition against
   backend policy;
3. consume the nonce (primary key, so a second spend conflicts);
4. apply the mutation;
5. append the audit record.

Any failure rolls all of it back, **including the nonce**. That matters in both
directions: the row lock serialises concurrent spends against one resource and
the nonce's primary key refuses the second one, and rolling back on failure means
a refused approval is not silently burned. The human's decision is either applied
and recorded, or nothing happened at all.

A refusal is a `200` with `ok: false`, not an error. A legitimately approved
change can still be refused by policy, and the caller must be able to tell the
user plainly that nothing was applied. The model is told so explicitly —
reporting success either way is how an agent ends up telling a user a refused
change was applied.

## The audit trail

`audit_events` is append-only by **trigger**, not by convention. A decision
record that can be edited or deleted is not an audit trail.

* typed facts (`etf_id`, `actor_type`, `actor_id`, `previous_state`,
  `new_state`, `rules_decision`, `llm_recommendation`, `final_decision`,
  `investment_score`, `request_id`) are structurally separate from untrusted free
  text (`override_rationale`, `justification`, `details`), so the boundary is
  visible in the schema;
* `rules_version` and `profile_version` record the policy in force when the
  decision was taken, so an old row stays interpretable after the rules or the
  investor profile change. The decision columns are one coherent snapshot or
  they are all null — an assignment creates no decision and leaves them empty
  rather than restating a decision from a different policy generation;
* the `etfs` row is the *current* state and these rows are the committed
  decisions that produced it. Reading one is never a substitute for the other;
* `consumed_approval_tokens` makes an approval spendable exactly once, and the
  nonce is inserted in the same transaction as the mutation, so a replay fails
  atomically.

## Domain-neutral claims, ETF meanings

The claim names are shared verbatim with `mcp-server/src/approval.rs`, which has
no opinion about ETFs:

| claim | ETF meaning |
| --- | --- |
| `resource_id` | canonical `etf_id` |
| `choice` | the decision the human approved |
| `expected_choice` | the engine decision displayed when they chose, as the model reported it; refused unless it equals the recomputation |
| `rationale` | the override rationale they typed |
| `payload` | `llm_recommendation`, `research_note`, `assignee` |

Adding an action: a request model and a registered function in
`agent/src/nat_streaming_react/approval.py`, an entry in `approval::ACTIONS` on
the MCP side, and the mutation itself. Nothing in the token format or the
verification changes.

The action registry is a fixed list rather than configuration: the set of things
a human can authorize is a security property of the deployment. A choice outside
an action's `allowed_choices` is refused even with a valid signature — which is
why `shortlist` accepts only `shortlist`, while `commit` accepts all three.

## Verifying it

```
make verify-approvals        # agent-side checks, offline
make verify-approvals-rust   # MCP-side verifier, decision and engine policy
make verify-hitl             # a human INITIATES an override, end to end
```

Between them: forged and tampered tokens, expiry, the lifetime ceiling and its
skew tolerance, wrong action/resource/request, a displayed decision that differs from the recomputed one,
payload-digest disagreement, missing identity, replay, cancellation, invalid and
unoffered choices, unauthorized interaction responses, every transition rule, and
that a token minted by the Python agent is accepted by the Rust verifier —
including a non-ASCII payload, which proves the two canonical JSON encoders
agree.

## What is not covered

Replay and rollback are tested at the level of the policy and the verifier.
The transactional behaviour itself — nonce conflict under concurrency, rollback
on a failed audit insert — is enforced by the database and is **not** covered by
an automated test, because it needs a live PostgreSQL. See
[LIMITATIONS.md](LIMITATIONS.md).
