# Case studies

Real incidents from this repository's development, written up as engineering
lessons rather than as a changelog. Every one is recorded in the reference
documentation or the code it changed; the source is linked from each case.
Model observations are `qwen3:8b` unless stated, dated where the record dates
them, and are observations — not properties of the architecture.

Each case follows the same shape: **symptom**, **the tempting reading**, **what
was actually wrong**, **why existing checks missed it**, **what changed**, **the
general lesson**, and **how to reproduce or verify** it.

| Case | Layer that failed | Authoritative output affected? | Lessons |
| --- | --- | --- | --- |
| [The expense ratio a hundred times too small](#the-expense-ratio-a-hundred-times-too-small) | tool contract → presentation | no | [03](03-design-evidence-for-the-model.md), [07](07-evaluate-the-system-not-just-the-model.md) |
| [The explanation that lost `bond`](#the-explanation-that-lost-bond) | tool contract → explanation | no | [03](03-design-evidence-for-the-model.md) |
| [The explanation that inverted the direction](#the-explanation-that-inverted-the-direction) | tool contract → explanation; scorer | no | [03](03-design-evidence-for-the-model.md), [07](07-evaluate-the-system-not-just-the-model.md) |
| [The policy suite at 0.4](#the-policy-suite-at-04) | prompt → agent tool use | no | [07](07-evaluate-the-system-not-just-the-model.md) |
| [The model misreported the engine to a human](#the-model-misreported-the-engine-to-a-human) | model; consent display | no — refused at the point of mutation | [05](05-recommendation-authority-and-consent.md), [08](08-adversarial-domain-data.md) |
| [The model held the default](#the-model-held-the-default) | backend reconciliation | **yes** — history recorded the wrong party as overriding | [05](05-recommendation-authority-and-consent.md) |
| [The score that went stale](#the-score-that-went-stale) | persistence | **yes** — rankings and filters used a dead policy | [06](06-decisions-that-survive-policy-change.md) |
| [One audit row, two policies](#one-audit-row-two-policies) | audit schema | **yes** — a history row mixed two policies | [06](06-decisions-that-survive-policy-change.md) |
| [The token the model had to copy](#the-token-the-model-had-to-copy) | API shape | no — approved changes failed to apply | [03](03-design-evidence-for-the-model.md), [05](05-recommendation-authority-and-consent.md) |

The first five never changed anything authoritative: the model was wrong and the
system held. Three of the last four put something false into a ranking or the
history, and all four were fixed in deterministic code or API shape, not in the
model. That asymmetry is the architecture working as intended — and the reason the
deterministic code gets the strictest tests.

---

## The expense ratio a hundred times too small

**Symptom.** The engine stores `"ter": 0.0022`, a 0.22% expense ratio. Answers
said "TER of 0.0022%" — VWCE-XETRA "0.0022%", CSPX-LSE "0.0007%", IEAC-LSE
"0.002%" — in 35 of 44 TER statements across the five suites, on NAT 1.8 and 1.9
alike (2026-10-04).

**The tempting reading.** Every gate was green and every decision was correct, so
the evaluation looked clean.

**What was actually wrong.** The read model returned a bare fraction with no unit,
and nothing in the prompt or the tool description said rates were fractions. The
model read `0.0022` as a percentage. A person was told a fund costs a hundredth of
what it does.

**Why existing checks missed it.** The gates score decisions, and the decisions
were right. Worse, the completeness check matched by substring, so a term group
accepting `0.0022` was *satisfied* by `0.0022%`: the defect passed a metric.

**What changed.** The contract: every rate now travels with a display string —
`ter_percent: "0.22%"`, `top_10_concentration_percent`, and `observed_percent` on
any score factor whose field is a rate — plus a units note, added at the output
boundary (`annotate_rates` in [`domain.rs`](../../mcp-server/src/domain.rs)) so
the engine and the deterministic baseline are untouched. And a scorer:
`unit_errors`, reported as `research_units_correct`, flags a percentage that is
an evidence fraction written raw with a `%`. After: 0 errors in 39 statements.
The metric stays ungated until it has held for longer than a day.

**General lesson.** Units are part of the tool contract. If the model must
communicate a quantity to a person, return it in the form a person reads, beside
the form code reads. A correct decision proves nothing about the figures in its
explanation, and evaluation must measure presentation semantics wherever they
matter to the reader.

**Reproduce.** `make rules-explain ETF=VWCE-XETRA` and find both `observed` and
`observed_percent` on the TER. Run the scorer on a unit error in lesson
[07, lab step 1](07-evaluate-the-system-not-just-the-model.md#1-run-the-real-scorer-on-three-answers).
`a_fraction_is_displayed_as_the_percentage_a_person_reads` and
`every_rate_in_the_read_model_has_its_percentage_beside_it` in
[`domain/tests.rs`](../../mcp-server/src/domain/tests.rs) pin the contract.
Source: [EVALUATION_ANALYSIS.md](../EVALUATION_ANALYSIS.md#the-expense-ratio-was-misstated-by-a-factor-of-a-hundred).

---

## The explanation that lost `bond`

**Symptom.** On the build that fixed the units, the IEAC-LSE explanation still
stated the decision and both caps correctly, and stopped saying the fund is a bond
fund — the reason its profile-fit cap applies. Three of three runs.
`research_required_facts_present` fell from 0.667 to 0.5.

**The tempting reading.** The new percentage fields had crowded the asset class
out of the payload, or the model had regressed.

**What was actually wrong.** The asset class had never left the payload: it was in
`verified_metrics.asset_class` and in the `risk_fit` rule note before and after.
An ablation build with the one new `required_elements` line removed (and the
percentage fields kept) restored "bond" on two of two runs. The instruction was
the trigger; the cause was the contract. `required_elements` asked for components
"named from `components`", a name-to-points map, so stating the facts behind a
component had always been a habit — and an unrelated instruction displaced it.

**Why existing checks missed it.** Nothing gated it, correctly: completeness is a
diagnostic. The diagnostic is what noticed.

**What changed.** `component_evidence`: the engine's own matched rules grouped by
component, each with field, observed value and note, and `required_elements`
asking for each component's facts and the facts behind each cap. A regrouping, so
it cannot change a decision; the baseline stayed byte-identical.

**General lesson.** Small tool or prompt changes displace behaviours that were
never asked for. If the explanation depends on a relationship, make the
relationship structure, and make stating it an explicit obligation.

**Reproduce.** Lesson [03, lab steps 1–2](03-design-evidence-for-the-model.md#lab).
Source: [EVALUATION_ANALYSIS.md](../EVALUATION_ANALYSIS.md#the-explanation-lost-its-facts-and-how-the-tool-contract-got-them-back).

---

## The explanation that inverted the direction

**Symptom.** With `component_evidence` in place but before `earned_fraction` was
added, "bond" came back on four of four runs — and the answer called the bond fund
"aligned with the investor's high risk tolerance". (That first version was
superseded before it was pushed; the same inversion had also appeared in the
ablation runs above.)

**The tempting reading.** The fact is present; the metric passes; the fix worked.

**What was actually wrong.** The note `risk_tolerance=high against
asset_class=bond` reads equally well as a match or a mismatch. Whether the fact
helped or hurt — the fund earned 0.1 of that rule — was only in the flat rule list
the model had to join by hand.

**Why existing checks missed it.** `research_required_facts_present` checks that a
term appears. "Bond" appears whether the answer says it helped or hurt. The
inversion was found by reading answers, and the real scorer passes it today.

**What changed.** `earned_fraction` on every evidence entry (1.0 full credit, 0.0
none), and `required_elements` asking whether each fact earned or lost points.
Five of five runs on the final build named the bond asset class with the direction
right; none called it a fit. No metric checks direction yet.

**General lesson.** Fact present ≠ fact interpreted correctly. Direction is data,
and it belongs in the contract. A scorer's maturity has to match what it can see:
a presence check is a fine diagnostic and a misleading gate.

**Reproduce.** Lesson [07, lab step 1](07-evaluate-the-system-not-just-the-model.md#1-run-the-real-scorer-on-three-answers):
the inverted answer passes every grounding metric. Designing the missing metric is
[challenge 3](CHALLENGES.md#challenge-3-build-a-semantic-direction-evaluation-metric).

---

## The policy suite at 0.4

**Symptom.** `decision_policy_correct` gated at 0.4 on the first live run.

**The tempting reading.** The policy comparator — or the policy — is wrong.

**What was actually wrong.** The sub-metrics said otherwise:
`llm_policy_validity_correct` was 1.0, while `decision_relationship_correct` and
`rules_win_by_default_correct` moved together at 0.4. The comparator was right
every time it ran; on three of five cases it never ran. The prompt said a
recommendation may never be more optimistic than the engine, and the agent
extended that to never *asking* the engine whether a more optimistic hypothesis
would be allowed — so a user asking "would shortlisting this rejected fund be
permitted?" got the model's judgment instead of the engine's.

**Why existing checks missed it.** They did not; this is the gate doing its job.
What it could not do alone was say *which layer* failed — that took the
sub-metrics read as a set.

**What changed.** The prompt and the tool description now separate *asserting* a
recommendation from *validating* one. The fix changed behaviour at once but not
consistently, and the published artifact recorded 0.4 rather than chasing it. On
the current prompt the gate has measured 1.0 on every run, on both toolkit
versions. One caveat is recorded: that artifact's sub-second latencies look like
input-rail refusals, and the build cannot be re-run.

**General lesson.** Distinguish an authority failure from an orchestration
failure. Here the policy engine was correct throughout and the agent did not call
it: a less useful answer, never a wrong authoritative one. Instrument evaluations
so the two cannot be confused.

**Reproduce.** `make eval-policy` on a running cluster; read the three
sub-metrics together. The comparator itself, with no model:
`a_model_may_be_conservative_and_may_never_be_optimistic` in `rules.rs`.
Source: [EVALUATION_ANALYSIS.md](../EVALUATION_ANALYSIS.md#the-policy-suite-found-a-gap-in-the-system-prompt).

---

## The model misreported the engine to a human

**Symptom.** Asked to shortlist the non-UCITS `VTI-ARCA`, the model proposed
`llm_recommendation: shortlist` against an engine that returns `reject` and was
refused by the advisory ceiling. It retried with `rules_decision: "shortlist"` —
asserting a different engine decision. That request reached a human, who approved
it. The mutation was refused:

```text
Human approval rejected: approval token was issued against a different
deterministic decision
```

**The tempting reading.** Either "the approval boundary failed, a bad request got
to a human" or "the system worked, nothing to see".

**What was actually wrong.** The model stated a false premise on its own, with no
attacker involved. The approval prompt displayed that premise under the label
"Deterministic engine (authoritative)", so the human consented to a falsehood.
The token bound the false premise as `expected_choice`; the MCP recomputed
`reject`, found the mismatch and refused before any state changed. `VTI-ARCA`
stayed `UNREVIEWED` with no mutating history.

**Why existing checks missed it.** No suite covers a model misstating a premise
unprompted; the injection suite measures resistance to *hostile data*. It was
found by driving the UI by hand.

**What changed.** Nothing needed to, for safety: three independent gates —
the token binding, `reconcile_decision`'s override-flag check, and `HC-UCITS` —
would each have refused it. What the incident exposes is the consent display: the
prompt shows the model's claim, not the recomputed decision. That is recorded as
an open problem rather than changed here.

**General lesson.** Never parse model prose — or model-supplied structured
arguments — to recover authoritative state. Bind the premise a human was shown
into what they sign, and re-derive the truth at the point of mutation. A safe
mutation path and an informed approval are different properties.

**Reproduce.** [DEMO.md — a constraint the human cannot talk past](../DEMO.md#a-constraint-the-human-cannot-talk-past);
`an_approval_is_void_when_the_deterministic_decision_has_moved` in
[`approval.rs`](../../mcp-server/src/approval.rs) asserts the refusal without a
model. Source: [ARCHITECTURE.md](../ARCHITECTURE.md#why-the-deterministic-result-is-bound-into-the-token).

---

## The model held the default

**Symptom.** With the engine at `shortlist` and the model at `research`, a person
choosing `shortlist` — the engine's own decision — was recorded as overriding the
system; a person choosing `research` was recorded as agreeing with it.

**The tempting reading.** A conservative model is harmless: it may only make
things safer.

**What was actually wrong.** The commit path derived the default as
`llm_recommendation.unwrap_or(rules_decision)`. The model held the default in the
conservative direction while being refused it in the optimistic one, and the audit
trail recorded the inverse of who decided what.

**Why existing checks missed it.** Every check asked whether the model could
*promote*. None asked whether it could *demote*.

**What changed.** `rules::reconcile_decision` became the one place reconciliation
happens: the default is the engine's decision, `override_applied = requested ≠
rules_decision`, and the token's flag must agree. `policy_comparison` was renamed
from `default_effective_decision` to `default_decision` and is the engine's
decision in every case; `rules_win_by_default_correct` and its guarding test stop
the evaluator drifting back.

**General lesson.** Advisory means advisory in both directions. Permitted is not
adopted.

**Reproduce.** Cases A and B in [`rules.rs`](../../mcp-server/src/rules.rs)
(`choosing_the_deterministic_decision_over_a_conservative_model_is_not_an_override`,
`following_a_conservative_model_away_from_the_engine_is_a_human_override`);
[DEMO.md — the case that is *not* an override](../DEMO.md#the-case-that-is-not-an-override).
Source: [ARCHITECTURE.md](../ARCHITECTURE.md#who-holds-the-default-decision).

---

## The score that went stale

**Symptom.** A column documented as "null until a human approves a decision" was
populated for the entire universe from the moment the database came up; rankings
reflected a policy generation nobody was running; and filtering by decision
returned nothing on a fresh database.

**The tempting reading.** A cache that needs invalidating.

**What was actually wrong.** The seeder stored the engine's score in
`etfs.investment_score` so SQL could `ORDER BY` it. Nothing reseeds on a policy
change, so it went stale on the first edit to `rules_spec.json`; and since
`etfs.decision` really was null until approval, the decision filter read the
wrong column entirely.

**What changed.** No cache. SQL answers only filters over stored columns; the
server evaluates every candidate through `rules::evaluate`, filters on the fresh
result, sorts by score then `etf_id`, collapses listings, and applies the limit
last. There is no SQL copy of the policy.

**General lesson.** Store the committed decision; recompute the current one. A
derived value whose inputs can change without it knowing is a second, silently
diverging implementation.

**Reproduce.** `committed_values_do_not_affect_the_deterministic_ranking` and
`changing_the_rules_changes_the_current_ranking_and_filtering` in
[`server/tests.rs`](../../mcp-server/src/server/tests.rs). Source:
[ARCHITECTURE.md](../ARCHITECTURE.md#what-is-stored-and-what-is-recomputed).

---

## One audit row, two policies

**Symptom.** An `ETF_ASSIGNED` row showed `investment_score: 84` beside
`rules_version: 2.0.0`, when the 84 had been earned under rules v1.

**The tempting reading.** A correct row: both numbers were true.

**What was actually wrong.** Both were true *at different times*. The assignment
path wrote the current versions next to the committed score, so the row read as one
snapshot and was two.

**What changed.** Events that create no decision leave the decision columns null
and record both generations separately under `details.policy_generations`.

**General lesson.** An audit row is one coherent snapshot of one generation, or it
carries no decision.

**Reproduce.** Lesson [06](06-decisions-that-survive-policy-change.md#lab);
`a_non_decision_event_keeps_the_two_policy_generations_apart` in
[`domain/tests.rs`](../../mcp-server/src/domain/tests.rs). Source:
[ARCHITECTURE.md](../ARCHITECTURE.md#audit-events-are-one-snapshot-or-none).

---

## The token the model had to copy

**Symptom.** Approved changes failed to apply, differently each time: the note was
re-drafted, a decision was dropped, and once the token came back the right length
with one character wrong.

**The tempting reading.** The model needs a clearer instruction to copy fields
exactly.

**What was actually wrong.** The mutation tools took the whole approved payload as
arguments, so the model had to replay it, including a long base64 token, across a
separate turn.

**What changed.** First the API was reduced to `(etf_id, approval_token,
request_id)` with every parameter read from the signed claims; then the
approval-gated function was made to apply its own mutation, so the model holds no
approval reference at all.

**General lesson.** Any long opaque string a model must copy verbatim is a failure
mode. Remove the need, rather than instructing harder.

**Reproduce.** The mutation tools' argument schemas in
[`server.rs`](../../mcp-server/src/server.rs); `_mint_and_apply` in
[`approval.py`](../../agent/src/nat_streaming_react/approval.py). Source:
[ARCHITECTURE.md](../ARCHITECTURE.md#nothing-model-visible-carries-an-approval-reference).
