# 5. Recommendation, authority and consent are different things

Stage A5 of the [applied learning path](../APPLIED-LEARNING-PATH.md#stage-a5-recommendation-authority-and-consent).
Prerequisite: template [Stage 9 — human-in-the-loop mutation](https://github.com/cognokratos/simple-agent-template/blob/main/docs/LEARNING-PATH.md#stage-9-human-in-the-loop-and-controlled-mutations)
and [concept 8 — model advice versus authoritative policy](https://github.com/cognokratos/simple-agent-template/blob/main/docs/concepts/08-human-in-the-loop.md#model-advice-versus-authoritative-policy).

> **Never sign the model's claim about authoritative state and assume that makes
> it authoritative.**

The template teaches the mechanics of a safe mutation: proposal, prompt, signed
token, point-of-mutation verification, one transaction. They are not repeated
here. This lesson is about the *domain semantics* those mechanics carry when the
thing being approved is a decision with a computed default: who produces each
value, who may move it, in which direction, and what the backend believes.

## Four values, four owners

| Value | Produced by | Authority | Persisted as |
| --- | --- | --- | --- |
| `rules_decision` | the deterministic engine, recomputed | **authoritative, and the default — always** | `audit_events.rules_decision` |
| `llm_recommendation` | the model | advisory only; may equal or be more conservative than `rules_decision`, never more optimistic | `audit_events.llm_recommendation`, only inside an approved record |
| the human's choice | the authenticated person | final, within constraints; a rationale is required whenever it differs from `rules_decision`, in either direction | the token's `choice`; `override_applied`, `override_rationale` |
| `final_decision` | the backend, after reconciliation and constraints | what actually happened | `etfs.decision`, `audit_events.final_decision`, with `rules_version` and `profile_version` |

They are named separately everywhere they appear — the approval prompt, the token,
the MCP validation, the audit row, the tool result — because the moment two of
them share a field, one silently becomes the other.

That is not hypothetical. The commit path once derived the default as
`llm_recommendation.unwrap_or(rules_decision)`. With the engine at `shortlist` and
the model at `research`, a person choosing `shortlist` — the engine's own answer —
was recorded as *overriding the system*, and a person choosing `research` was
recorded as agreeing with it. The model held the default in the conservative
direction while being refused it in the optimistic one
([ARCHITECTURE.md](../ARCHITECTURE.md#who-holds-the-default-decision)).

### What each party may do

| | May | May not |
| --- | --- | --- |
| **Model** | explain; recommend the engine's decision or a more conservative one; ask the engine whether a *hypothetical* recommendation would be permitted (`evaluate_etf` with `llm_recommendation`, read-only) | define the authoritative result; recommend above it; turn a recommendation into a state change; present its own recollection of the rules as the rules |
| **Human** | confirm; override in either direction with a rationale; initiate an override the model never proposed; supply the research note a shortlist requires | bypass a non-bypassable constraint, by any rationale |
| **Backend** | — | trust anybody's claim about the engine's decision, including one a human approved |

and the backend **must**, at the point of mutation: lock the row, recompute the
evaluation, verify the token against the recomputed decision, reconcile the
choice, re-check hard constraints, and write the mutation, the nonce and the audit
record in one transaction — or refuse and roll all of it back.

## Where each rule is enforced

| Rule | First enforced | Authoritatively enforced |
| --- | --- | --- |
| Advisory ceiling on the model | `commit_evaluation` request check in [`approval.py`](../../agent/src/nat_streaming_react/approval.py) — before a human is asked | `rules::reconcile_decision` in [`rules.rs`](../../mcp-server/src/rules.rs), after the row lock |
| The default is the engine's decision | the approval prompt labels the *model-reported* engine decision *Confirm* and every other *Override* | `reconcile_decision`: `override_applied = requested != rules_decision` |
| An override declares itself | the token's `override_requested`, derived from the choice | `reconcile_decision` refuses a flag that disagrees, in either direction |
| Overrides carry a rationale | the prompt requires one | `commit_evaluation` / `shortlist_etf` in [`server.rs`](../../mcp-server/src/server.rs) |
| Hard constraints hold | — (all options are offered on purpose) | `rules::blocking_hard_constraint`, called in every mutation body |
| The displayed premise was true | the token binds it as `expected_choice` — signed, not verified | `ApprovalVerifier::verify` against the decision recomputed under the lock |

The left column is convenience; the right column is the boundary. The agent-side
checks make a bad request fail early and readably, but nothing in the system
depends on them.

## Lab

### 1. The trust matrix

`reconcile_decision(rules_decision, llm_recommendation, requested_decision,
override_requested)` is pure. Predict the outcome of each row before reading the
answer column, using only the rules above:

| Rules | Model | Human | Outcome in this repository | Asserted by |
| --- | --- | --- | --- | --- |
| shortlist | shortlist | shortlist | committed; `override_applied = false`; a research note is required for a shortlist | `verify-approvals` |
| shortlist | research | shortlist | committed; **not an override** — confirming the engine never is, whatever the model said | case A, `choosing_the_deterministic_decision_over_a_conservative_model_is_not_an_override` |
| shortlist | research | research | committed as a **human** override, with a rationale, though the model suggested it | case B, `following_a_conservative_model_away_from_the_engine_is_a_human_override` |
| research | shortlist | shortlist | **refused** — the model's promotion is refused before the human is asked, and again by `reconcile_decision` whatever the human chose | case C, `a_more_optimistic_model_recommendation_is_refused_before_anything_else` |
| research | none / research | shortlist | committed as a human promotion, with rationale and a research note | case D, `a_human_may_move_above_the_deterministic_decision`; `make verify-hitl` end to end |
| reject (non-UCITS) | none | shortlist | representable as an override, then **refused** by `HC-UCITS`; so is `research` | case E, `a_human_override_cannot_reach_past_a_non_bypassable_constraint` |
| shortlist | research | shortlist, token claims override | **refused** — the flag disagrees with the decision | case A′ |

Note row four against row five. The human's *outcome* is reachable either way; what
is refused is the *model* owning it. A promotion exists only as a human act with
the person's name and reason on it — which is why `make verify-hitl-audit` checks
that `llm_recommendation` is **not** `shortlist` on the row it inspects.

Run the deterministic half:

```bash
make verify-approvals-rust
```

Then add a row of your own as a scratch test next to the cases in `rules.rs`:
engine `shortlist`, model `reject`, human `research`. Predict `override_applied`,
whether a rationale is required, and what `human_override_decision` holds. Delete
the test afterwards.

### 2. Break it: the model lies about the engine

This was observed with `qwen3:8b` and is recorded in
[ARCHITECTURE.md](../ARCHITECTURE.md#why-the-deterministic-result-is-bound-into-the-token)
and [DEMO.md](../DEMO.md#a-constraint-the-human-cannot-talk-past). Asked to
shortlist the non-UCITS `VTI-ARCA`, the model first proposed
`llm_recommendation: shortlist` against an engine that returns `reject`, and was
refused by the ceiling. It then retried with `rules_decision: "shortlist"` —
asserting a different engine decision — and that request reached a human, who
approved it.

Trace it through the code and answer each question before reading the answer.

**What did the human see?** Both decision-carrying approval functions in
`approval.py` (`etf_commit_evaluation`, `etf_shortlist_etf`) build the prompt from
the model's request. It read *"Deterministic engine (authoritative): shortlist"*.
On the commit path the card then offers *"Confirm — shortlist"* as the default and
labels `reject` an *Override*; on the shortlist path a shortlist that matches the
claimed engine decision is a plain confirmation. Either way, the premise was false
and it was displayed under the label "authoritative".

**What was signed?** `choice = shortlist`, `expected_choice = shortlist` (the
model's claim), `override_requested = false` (because the choice equalled the
claimed engine decision), the gateway-asserted `actor_id`, the `request_id`, and
the payload. A genuine HMAC over a false premise. Note also what the lie *avoided*:
on the true premise this is an upward override and the human would have been made
to type a rationale.

**What did the backend re-derive?** The mutation tool (`commit_evaluation` and
`shortlist_etf` behave identically here) locked `VTI-ARCA`, recomputed the
evaluation — `reject` — and called `verify` with that as the expected choice:

```text
Human approval rejected: approval token was issued against a different
deterministic decision
```

Nothing after that point ran. The transaction rolled back, the nonce was not
consumed, and `VTI-ARCA` stayed `UNREVIEWED` with no mutating history event.

**Could the wrong claim have become the authoritative mutation?** No, and not
because of one check. Had the binding somehow passed, `reconcile_decision` would
have found `shortlist ≠ reject` with `override_requested = false` and refused;
had that passed, `HC-UCITS` would have refused any decision above `reject`. Three
independent gates, and the one that fired first depended only on what the model
got wrong.

### 3. What the boundary did not protect

The mutation was safe. The *consent* was not well-informed: a person approved a
request whose premise was false, labelled authoritative by the system's own UI.
The token binding turns that into a refusal rather than a wrong state — that is
its job — but the human still made a decision on a falsehood.

**Question.** The approval function already runs below the model, holds the
authenticated identity and can reach the MCP server. What would it take for the
prompt to *display* the engine's recomputed decision rather than the model's claim,
keeping the model's value only as a cross-check? What does the token's
`expected_choice` then mean, and which refusal becomes impossible? (This is a
documented limitation of the current design —
[LIMITATIONS.md](../LIMITATIONS.md#approval-prompts-can-display-a-model-misreported-deterministic-decision) — and an open problem in
[CHALLENGES.md](CHALLENGES.md#open-problems), not something this repository
changes.)

### 4. Read the point of mutation

Open `commit_evaluation` in `server.rs` and list, in order, everything that
happens between `pool.begin()` and `tx.commit()`. Mark which steps read
caller-supplied data and which read state recomputed under the lock. The only
caller-supplied inputs are `etf_id`, the token and `request_id`; every decision
parameter comes from the signed claims, and every claim about state is checked
against the recomputation.

## What to take away

* Name the engine's result, the model's opinion, the human's choice and the
  persisted outcome as four values with four owners, and never let two share a
  field.
* Advisory means advisory in **both** directions. A model that cannot promote must
  not be able to demote either; permitted is not adopted.
* A signature proves that a human agreed to a statement. It does not make the
  statement true. Bind the premise into the signature so that a false one is
  detectable, and recompute the truth at the point of mutation.
* The point-of-mutation backend is the only component that knows the decision.
  The UI, the token and the model all carry claims about it.
* Consent is only as good as the premise it was shown. **Mutation integrity and
  informed consent are separate properties**: this repository guarantees the
  first and, today, not the second.

## Go deeper

* Reference: [ARCHITECTURE.md — who holds the default decision](../ARCHITECTURE.md#who-holds-the-default-decision),
  [why the deterministic result is bound into the token](../ARCHITECTURE.md#why-the-deterministic-result-is-bound-into-the-token),
  [APPROVALS.md](../APPROVALS.md),
  [VERIFICATION.md — decision authority](../VERIFICATION.md#decision-authority),
  [SECURITY.md — why the model must not hold the default either way](../SECURITY.md#why-the-model-must-not-hold-the-default-either-way)
* Source: `reconcile_decision`, `compare_recommendation`, `blocking_hard_constraint`
  in [`rules.rs`](../../mcp-server/src/rules.rs); `verify` in
  [`approval.rs`](../../mcp-server/src/approval.rs); `commit_evaluation` in
  [`server.rs`](../../mcp-server/src/server.rs); `decision_options` and
  `etf_commit_evaluation` in [`approval.py`](../../agent/src/nat_streaming_react/approval.py)
* Case study: [model claim versus rules decision](CASE-STUDIES.md#the-model-misreported-the-engine-to-a-human)
* Next: [06 — Decisions that survive policy change](06-decisions-that-survive-policy-change.md),
  or [08 — Adversarial domain data](08-adversarial-domain-data.md) for the same
  authority question asked about text
