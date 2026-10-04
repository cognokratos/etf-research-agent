# Follow one decision

The template's [request walkthrough](https://github.com/cognokratos/simple-agent-template/blob/main/docs/tutorials/REQUEST-WALKTHROUGH.md)
follows one request across the network: browser, gateway, agent, MCP, database.
That path is identical here and is not repeated. This walkthrough follows something
else — **one decision** — and asks at every step who owns it, whether it is
computed or asserted, whether the model can move it, and whether it is written
down.

The fund is `IEAC-LSE`, a euro corporate bond fund, because one record exercises
most of the policy: a score in the shortlist band, a missing critical metric, a
mandate mismatch, two caps, and an explanation that has gone wrong twice in this
repository's history. Every number below is from the shipped fixtures — rules
`1.1.0`, profile `1.0.0` — and `make rules-explain ETF=IEAC-LSE` reproduces all
of them.

`IEAC-LSE` appears in the evaluation datasets, so do not commit it on a cluster
you measure with. To watch steps 16–22 live, use a fund the approval suite
resets, as lesson [06](06-decisions-that-survive-policy-change.md#lab) does.

## Authority at a glance

"Deterministic" means: the same inputs always produce the same value. A human's
choice is not computed — it is *asserted* — but once made it is a fixed, signed
value, and everything downstream of it is deterministic again. That is marked
"asserted".

| # | Step | Value for IEAC-LSE | Authority | Deterministic? | Model can change it? | Persisted? |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Fund facts | bond, europe, UCITS, TER 0.2%, top-10 unknown | data (dated snapshot) | yes | no | source + `etfs` columns |
| 2 | Investor profile | `default` 1.0.0: high risk, UCITS required | policy input | yes | no | file, versioned |
| 3 | Rules specification | 1.1.0, validated at boot | policy | yes | no | file, versioned |
| 4 | Rule matching | ten matched rules | engine | yes | no | no |
| 5 | Raw component points | 65.45 of 86 | engine | yes | no | no |
| 6 | Unavailable metrics | `top_10_concentration` (8, critical), `tracking_difference_3y` (6) | engine | yes | no | no |
| 7 | Available weight | 86 of 100 | engine | yes | no | no |
| 8 | Renormalisation | ×1.1628 → 76 | engine | yes | no | committed score only |
| 9 | Score decision | `shortlist` (75–100) | engine | yes | no | in audit `details` |
| 10 | Profile fit | 6.8 / 20 = 0.34 | engine | yes | no | no |
| 11 | Caps and constraints | `CAP-CRITICAL-DATA`, `CAP-PROFILE-FIT`; `HC-UCITS` passes | engine | yes | no | in audit `details` |
| 12 | `rules_decision` | `research` | **engine — authoritative** | yes | no | at commit |
| 13 | `component_evidence` | facts grouped by component, with `earned_fraction` | engine (regrouping) | yes | no | no |
| 14 | Evidence to the model | `get_research_context` result | engine → model | yes | no | trace only |
| 15 | Explanation, recommendation | prose; `llm_recommendation` ≤ `research` | model — **advisory** | no | yes | only inside an approved record |
| 16 | Human choice | confirm `research`, or override with a rationale | human — asserted | asserted | no | at commit |
| 17 | Approval token | binds choice, displayed decision, payload, actor, request | application | yes | no | nonce only |
| 18 | Row lock | `SELECT … FOR UPDATE` | backend | yes | no | — |
| 19 | Recomputation | 76, `research` again | **engine — authoritative** | yes | no | at commit |
| 20 | Policy recheck | binding, reconciliation, rationale, constraints | backend | yes | no | — |
| 21 | Nonce | consumed, single use | backend | yes | no | `consumed_approval_tokens` |
| 22 | Mutation + audit | one transaction | backend | yes | no | `etfs`, `audit_events` |

The column to read is the fifth. The model can change exactly one row, and that
row is labelled advisory everywhere it travels.

---

## Compute: steps 1–13

All of this happens in `rules::evaluate` in
[`mcp-server/src/rules.rs`](../../mcp-server/src/rules.rs), a pure function of
three inputs. It runs on every read and again inside the mutation transaction;
its result is never stored as the current answer.

### 1. Load the verified fund facts

The row comes from `data/etfs.json`, seeded into PostgreSQL at MCP startup after
`validate_sources` checks that every citation means what it claims. The engine
does not see the row: it sees `EtfFacts`, exactly the twelve fields it may score.

```text
asset_class bond   region europe   ucits true   distribution_policy distributing
replication sampled   ter 0.002   aum_usd 12000000000   fund_age_years 17.3
holdings_count 3500   top_10_concentration null   tracking_difference_3y null
```

The issuer description and any research note are on the row and **not** in
`EtfFacts`. That omission is the injection defence of lesson
[08](08-adversarial-domain-data.md).

*Authority: data · deterministic · model cannot change it · persisted as the
source snapshot and the typed columns.*

### 2. Load the investor profile

`data/investor_profile.json`, `default` v1.0.0: `risk_tolerance: high`,
`require_ucits: true`, all four preferences on. Read once at boot.

*Authority: policy input · deterministic · model cannot change it · versioned
file.*

### 3. Load the rules specification

`data/rules_spec.json` v1.1.0, through `RulesSpec::parse`. An invalid
specification would have stopped the server here (lesson
[01](01-policy-is-a-program.md#3-break-the-specification)).

*Authority: policy · deterministic · model cannot change it · versioned file.*

### 4. Match the deterministic rules

`score_metric` resolves every metric to one outcome — scored, missing, or not
applicable:

| Component | Rule | Observed | Earned | Points |
| --- | --- | --- | --- | --- |
| `cost_efficiency` | `COST-B` | TER 0.2% | 0.85 × 20 | 17.0 |
| `diversification` | `DIV-H-A` | 3,500 holdings | 1.0 × 12 | 12.0 |
| `fund_scale` | `SCALE-B` | 12 bn USD | 0.8 × 15 | 12.0 |
| `fund_structure` | `STRUCT-R-SAMPLED` | sampled | 0.85 × 9 | 7.65 |
| `fund_maturity` | `AGE-A` | 17.3 years | 1.0 × 10 | 10.0 |
| `risk_fit` | `FIT-ASSET-HIGH-BOND` | bond | 0.1 × 6 | 0.6 |
| `risk_fit` | `FIT-REGION-HIGH-REGIONAL` | europe | 0.8 × 4 | 3.2 |
| `investor_fit` | `PREF-ACC-UNMET` | distributing | 0 × 4 | 0 |
| `investor_fit` | `PREF-PHYS-MET` | sampled | 1 × 3 | 3.0 |
| `investor_fit` | `PREF-BROAD-UNMET` | europe | 0 × 3 | 0 |

Bands are selected by bound, not position, so the order of `rules_spec.json`
cannot change this table. (The serialised `points` for `fund_structure` is
`7.6499999999999995`; a model that writes `7.65` is right, and an earlier
`ungrounded_numbers` scorer called that a fabrication —
[EVALUATION_ANALYSIS.md](../EVALUATION_ANALYSIS.md#research_no_ungrounded_numbers-the-first-live-baseline).)

*Authority: engine · deterministic · model cannot change it · not persisted.*

### 5. Compute component raw points

Summed per component: cost 17.0, diversification 12.0, scale 12.0, structure
7.65, maturity 10.0, risk fit 3.8, investor fit 3.0 — **65.45**.

### 6. Identify unavailable metrics

`top_10_concentration` (weight 8, **critical**) and `tracking_difference_3y`
(weight 6, not critical; null for the entire universe). Both reported in
`missing_data` with the weight they removed. `tracking_quality` has no scorable
metric left and is marked `unavailable: true` — no data, not "scored zero".

### 7. Calculate the available weight

100 − 8 − 6 = **86**. Completeness is a different number, over the ten declared
fields: 8 of 10 present, **0.8**.

### 8. Renormalise

`factor = 100 / 86 = 1.1628`; `65.45 × 1.1628 = 76.1` → **76** (half-up).
`distribute` then assigns integer contributions that sum exactly to 76: cost 20,
diversification 14, scale 14, structure 9, maturity 12, risk fit 4, investor fit
3, tracking 0. Cost contributes 20 against a nominal weight of 20 having earned
17; the `normalization` block publishes the arithmetic so that reads as
renormalisation rather than a bug.

*Steps 5–8: engine · deterministic · model cannot change them · only the final
score is persisted, and only at commit.*

### 9. Compute the score decision

76 falls in `shortlist` (75–100). This is `score_decision`: what the number alone
would mean. It is published precisely so the next two steps are explainable rather
than merely asserted.

### 10. Evaluate profile fit

`profile_fit_components` are `risk_fit` and `investor_fit`: (3.8 + 3.0) / 20 =
**0.34**. All 20 points of fit weight were scorable; 6.8 were earned, most of the
loss from the bond asset class (0.1 of its rule) and the two unmet preferences.

### 11. Apply policy caps and hard constraints

| Check | Condition | Result |
| --- | --- | --- |
| `CAP-CRITICAL-DATA` | a critical field is missing | **fires** — `top_10_concentration` |
| `CAP-COMPLETENESS` | completeness < 0.7 | 0.8, does not fire |
| `CAP-PROFILE-FIT` | profile fit < 0.5 | **fires** — 0.34 |
| `HC-UCITS` | profile requires UCITS and the fund is not | UCITS, does not fire |

Each cap can only lower a decision. Either one alone would hold IEAC-LSE at
`research`; both are published.

### 12. Produce `rules_decision`

**`research`.** The `explanation` field says so in lines a reader can check:
"Score band: shortlist", "Scored on 86 of 100 weight…", "Missing critical
fields: top_10_concentration", "Policy cap CAP-CRITICAL-DATA: at most research",
"Policy cap CAP-PROFILE-FIT: at most research", "Effective decision: research".

*Steps 9–12: engine · deterministic · model cannot change them · persisted only
when a human commits a decision, as `rules_decision` on the audit row.*

### 13. Produce `component_evidence`

`component_evidence` in [`domain.rs`](../../mcp-server/src/domain.rs) regroups
the matched rules from step 4 under their components, each with `field`,
`observed`, `earned_fraction` and `note`; `annotate_rates` adds
`observed_percent: "0.2%"` to the TER. A regrouping, so it can explain the
decision and has no way to change it — lesson
[03](03-design-evidence-for-the-model.md) is why it exists.

*Authority: engine · deterministic · model cannot change it · not persisted.*

---

## Explain: steps 14–15

### 14. The model receives the evidence

Asked *"Why is IEAC-LSE marked research instead of shortlist?"*, the agent calls
`get_research_context`. The result separates what the model may state as fact
(`verified_metrics`, `deterministic_conclusions`), what it must treat as data
(`untrusted_free_text`, with its provenance label), and what it owes the reader
(`required_elements`: components with their facts and direction, caps with the
facts that triggered them, missing metrics and their effect, rates from
`*_percent`, the `data_as_of` date).

*Authority: engine output, handed to the model · deterministic · the model cannot
change what it received · recorded in the trace, not in the database.*

### 15. The model explains, and may recommend

The explanation is the model's. So is any recommendation: `llm_recommendation`
may be `research` or `reject` for this fund — equal or more conservative — and
never `shortlist`. The model may also *ask* the engine about a hypothetical
(`evaluate_etf` with `llm_recommendation: shortlist` returns `more_optimistic`,
`allowed: false`, `default_decision: research`), which is a read, not a
recommendation.

Whether the explanation names the bond asset class with the right direction is
model behaviour: measured at five of five runs on the current build
(`qwen3:8b`, 2026-10-04), not guaranteed, and not checked by any metric today
(lesson [07](07-evaluate-the-system-not-just-the-model.md)).

*Authority: model — advisory · not deterministic · the only step the model owns ·
persisted only if a human approves a record that carries it.*

---

## Consent: steps 16–17

### 16. The human chooses

If the user asks to commit a decision, the model calls the approval-gated
`commit_evaluation` function in
[`approval.py`](../../agent/src/nat_streaming_react/approval.py), passing
`rules_decision: research` from step 12. The card shows three labelled roles —
*Deterministic engine (authoritative)*, *Model recommendation (advisory only)*,
*Default decision* — and offers every decision plus Cancel:

* **Confirm — research**: no rationale, not an override;
* **Override — shortlist**: requires a rationale and a grounded research note;
* **Override — reject**: requires a rationale.

Note what step 16 displays: the `rules_decision` *the model passed*. Here it is
the truth. Lesson [05](05-recommendation-authority-and-consent.md#2-break-it-the-model-lies-about-the-engine)
traces the case where it was not.

*Authority: human — asserted · the model cannot choose for them · persisted at
commit, with the person's identity.*

### 17. The approval binds what was displayed

After the human answers, the function — not the model — mints an HMAC token:
`action: commit`, `resource_id: IEAC-LSE`, `actor_id` from the gateway header,
`request_id`, `choice`, `expected_choice` (the `rules_decision` displayed in
step 16), `override_requested`, `rationale`, `payload` (`llm_recommendation`,
`research_note`) with its digest, `exp`, `nonce`. Token mechanics are the
template's [concept 8](https://github.com/cognokratos/simple-agent-template/blob/main/docs/concepts/08-human-in-the-loop.md#the-shape-of-a-safe-mutation);
what matters here is that the displayed premise is *inside* the signature. The
function then calls the MCP's approval endpoint itself; the model never sees the
token.

*Authority: application · deterministic given the human's answer · the model
cannot alter or replay it · only the nonce is persisted.*

---

## Commit: steps 18–22

Inside `commit_evaluation` in [`server.rs`](../../mcp-server/src/server.rs), one
transaction.

### 18. Lock the resource

`lock_etf`: `SELECT … WHERE etf_id = 'IEAC-LSE' FOR UPDATE`. The exact `etf_id`
from the token — no resolver, no ticker (lesson
[04](04-model-the-domain-before-the-agent.md)). It must still be `UNREVIEWED`; an
initial decision is valid exactly once.

### 19. Recompute the evaluation

`rules::evaluate` again, from the locked row and the policy loaded at boot: 76,
`research`. Identical to step 12 because the function is pure — unless the row,
the rules or the profile moved since the human looked, in which case it is not,
and the next step notices.

### 20. Recheck policy

In order, each refusing with a readable reason and rolling everything back:

1. `ApprovalVerifier::verify` — signature, expiry, action, resource, request,
   payload digest, and `expected_choice == research`. A token minted against a
   different engine decision is void here.
2. `reconcile_decision` — the model's recommendation may not exceed `research`;
   `override_applied = choice ≠ research`, and must equal the token's flag.
3. An override needs a rationale; a non-override must not carry one; a shortlist
   needs a research note.
4. `blocking_hard_constraint` — nothing for a UCITS fund, but it runs on every
   path.

### 21. Consume the nonce

`INSERT INTO consumed_approval_tokens … ON CONFLICT (nonce) DO NOTHING`; zero rows
affected means a replay, refused.

### 22. Commit the mutation and the audit record

`apply_evaluation` writes `review_state = RESEARCH`, `decision`, `investment_score
= 76`, `decided_rules_version = 1.1.0`, `decided_profile_version = 1.0.0`.
`write_audit` appends `EVALUATION_COMMITTED` with `actor_type = human`, the
actor, `rules_decision = research`, `llm_recommendation`, `final_decision`,
`override_applied`, `override_rationale`, both versions, and `details` carrying
`score_decision: shortlist`, both applied caps, the nonce and the full
`decision_authority`. Then `COMMIT`. Any failure before it rolls back all of it,
nonce included.

*Steps 18–22: backend · deterministic · the model cannot reach them · steps
21–22 are the only writes in the whole walkthrough, and `audit_events` refuses
`UPDATE` and `DELETE` by trigger.*

---

## What to take away

Read the "Model can change it?" column once more. Authority enters as data
(steps 1–3), is computed once by code (4–13), passes *through* the model without
being owned by it (14–15), is consented to by a person (16), is bound to what that
person was shown (17), and is re-derived before anything persists (18–22). The
model's contribution is real — it is the only reason a person can ask a question
in prose and get an explanation back — and it is confined to the one row labelled
advisory.

## Go deeper

* Each phase in depth: compute — [01](01-policy-is-a-program.md),
  [02](02-uncertainty-is-policy.md); explain — [03](03-design-evidence-for-the-model.md);
  consent and commit — [05](05-recommendation-authority-and-consent.md); after the
  policy moves — [06](06-decisions-that-survive-policy-change.md)
* Reference: [ARCHITECTURE.md](../ARCHITECTURE.md), [APPROVALS.md](../APPROVALS.md),
  [DEMO.md — a cap in action](../DEMO.md#a-cap-in-action)
* The network path of the same request: the template's
  [request walkthrough](https://github.com/cognokratos/simple-agent-template/blob/main/docs/tutorials/REQUEST-WALKTHROUGH.md)
