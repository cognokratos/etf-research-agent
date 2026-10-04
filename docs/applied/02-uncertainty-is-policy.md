# 2. Uncertainty is policy

Stage A2 of the [applied learning path](../APPLIED-LEARNING-PATH.md#stage-a2-uncertainty-is-policy).
Prerequisite: template [Stage 4 — grounding](https://github.com/cognokratos/simple-agent-template/blob/main/docs/LEARNING-PATH.md#stage-4-grounding-in-authoritative-systems)
and [grounded is not the same as correct](https://github.com/cognokratos/simple-agent-template/blob/main/docs/concepts/03-grounding-and-authoritative-state.md#grounded-is-not-the-same-as-correct).

> Grounded data can still be incomplete. What incomplete data *means* is a domain
> policy question.

The template's grounding lesson ends at "the system of record is the only source of
facts". In a real domain the system of record regularly answers `null`.
`AGGH-XETRA`'s issuer does not publish a top-ten concentration figure for an index
of ten thousand bonds; no fund in the shipped snapshot has a realised three-year
tracking difference. The grounded fact is *"unknown"*, and the engine still has to
produce a decision. Whatever it does with the unknown is policy — whether or not
anyone wrote it down.

## Three designs for an absent metric

| Design | What it computes | The claim it makes | Why that claim is false |
| --- | --- | --- | --- |
| **A. Missing = zero** | the metric earns 0 of its weight | "we measured this fund and it performed badly here" | nobody measured it |
| **B. Ignore silently** | the weight disappears; nothing is reported | "this score is comparable with every other score" | it was computed on less evidence than its neighbours |
| **This repository** | the weight leaves the denominator, the absence is published with the weight it removed, and caps bound what an incomplete record can reach | "this is the score on what is known, here is what is not, and here is the ceiling that follows" | — |

Design A is the default in most scoring code, because `None` becomes `0.0` at the
first arithmetic operation. Design B is what you get when someone notices A is
unfair and fixes only the arithmetic. Neither is a neutral choice; each is a
policy that nobody reviewed as one.

## The semantics the engine implements

All of it is declared in `missing_data_policy` and `decision_caps` in
[`rules_spec.json`](../../data/rules_spec.json) and interpreted in `evaluate` in
[`rules.rs`](../../mcp-server/src/rules.rs):

| Situation | Outcome | Where |
| --- | --- | --- |
| A metric's field has no value | `MetricOutcome::Missing`: weight withdrawn, reported in `missing_data` with `weight_removed`, `reason` and `critical` | `score_metric` |
| A categorical value outside the vocabulary (`replication: "quantum"`) | also `Missing` — an unrecognised value is a data defect, not evidence of zero quality | `an_unrecognised_categorical_value_is_reported_not_scored` |
| A preference the mandate switches off | `NotApplicable`: weight withdrawn, reported in `not_applicable`; it is about the *mandate*, so it does not count against completeness and cannot trigger a cap | `switching_off_a_preference_removes_its_weight_rather_than_penalising_every_fund` |
| Score | `round_half_up(100 × raw_earned_points / available_weight)` | `Normalization` |
| Completeness | present fields / the ten declared `completeness_fields` — a property of the *record*, not of the weight | `data_completeness` |
| A critical field missing | `CAP-CRITICAL-DATA`: at most `research`, whatever the score | `decision_caps` |
| Completeness strictly below 0.7 | `CAP-COMPLETENESS`: at most `research` | `decision_caps` |
| A component with no scorable metric | `unavailable: true`, named in the explanation — a zero contribution that does *not* mean "scored zero" | `ComponentBreakdown` |

Two policy decisions in that table deserve to be read as decisions:

**The cap is the counterpart of renormalisation, not a safety net.**
Renormalising flatters exactly the funds whose missing metric they would have
scored badly on. `AGGH-XETRA` renormalises to 84 — inside the shortlist band — and
the critical-data cap holds it at `research`. Remove the cap and design B is back.

**`tracking_difference_3y` is deliberately not critical.** It is null for all 31
listings, so making it critical would cap the entire universe and the cap would
stop discriminating; zero-filling it would penalise every fund six points for a
figure nobody published. The reasoning is in `critical_fields_note`, next to the
list, because the next contributor will otherwise "fix" it.

## What the consumer is told

Uncertainty only helps if it reaches the reader. Every evaluation carries it in
four places, and none of them is prose the model must infer from:

* `missing_data[]` — field, component, metric, `weight_removed`, `reason`,
  `critical`;
* `component_breakdown[]` — `nominal_weight`, `available_weight`,
  `raw_earned_points`, `normalized_contribution`, `unavailable`;
* `normalization` — total weight, available weight, raw points, and the `factor`
  every point was multiplied by, so the score can be reconciled by hand;
* `explanation` — "Scored on 86 of 100 weight; the rest had no data and was
  renormalised away rather than scored as zero", "Missing critical fields: …".

`get_research_context` additionally lists, among its `required_elements`, *"Any
missing metric, and the effect it had on the decision."* Lesson
[03](03-design-evidence-for-the-model.md) is about why that line has to exist.

## Lab

No cluster. Steps 1–4 make no edits: `FACTS` overrides scored fields in memory,
`null` for a numeric field, `""` for a text field. The optional code exercise in
step 4 and steps 5–6 edit tracked files and restore them with `git checkout`.

> Requires a clean worktree; the restore command discards local edits in these paths. See the [ground rules](README.md#ground-rules-for-the-labs).

Start from a complete record:

```bash
make rules-explain ETF=VWCE-XETRA
```

Read `evaluation.normalization`: total 100, available 94 (the six points of
`tracking_quality` are already withdrawn — no fund has the data), raw 81.8, factor
1.0638, score 87. Read `missing_data`: one entry, `tracking_difference_3y`,
`critical: false`.

For each experiment below, **predict before you run it**: the available weight,
the factor (`100 / available`), the score, completeness, and the decision. The
weights you need are in `rules_spec.json`; the earned points per metric are in the
baseline's `component_evidence` (`earned_fraction × metric weight`).

### 1. A non-critical metric

```bash
make rules-explain ETF=VWCE-XETRA FACTS='{"replication": ""}'
```

`replication` feeds two metrics: `fund_structure` (9) and the
`physical_replication` preference (3). Both leave: available 82, raw 69.8, factor
1.2195, **score 85**, completeness 0.8, no cap, `shortlist`. Note that
`missing_data` has two entries for one field — one per metric — and that
`fund_structure` is now `unavailable`.

### 2. A critical metric

```bash
make rules-explain ETF=VWCE-XETRA FACTS='{"holdings_count": null}'
make rules-explain ETF=VWCE-XETRA FACTS='{"top_10_concentration": null}'
```

The first: available 82, score 85, `CAP-CRITICAL-DATA`, `research`. The second is
the instructive one: available 86, raw 75.0, **score 87 — unchanged** — and the
decision drops to `research`. The fund earned full marks on what is known, so
removing an unknown did not move the number. It moved the *decision*, because the
policy says a record missing a critical input is not complete enough to
shortlist. The score and the decision answer different questions.

### 3. Crossing the completeness threshold

```bash
make rules-explain ETF=VWCE-XETRA FACTS='{"replication": "", "distribution_policy": ""}'
make rules-explain ETF=VWCE-XETRA FACTS='{"replication": "", "distribution_policy": "", "asset_class": ""}'
```

The first lands at completeness **0.7** exactly — available 78, score 84, no cap,
`shortlist`. The condition is "below 0.7", and 0.7 is not below it. The second
reaches 0.6: available 72, factor 1.3889, score 83, `CAP-COMPLETENESS`,
`research`. None of the removed fields is critical; it is the accumulation that
caps.

Boundary semantics (`<` versus `≤`) are policy too. Someone chose them, and a test
should pin them.

### 4. Name the false claims

Now compute what the two naïve designs would have said, by hand. Design A divides
the earned points by all 100 declared points; once an absence has become a zero,
nothing records that it was ever missing, so no cap can fire. Design B
renormalises like the engine but publishes nothing and caps nothing.

| Record | Engine | A: missing = zero (`raw / 100`) | B: ignore silently |
| --- | --- | --- | --- |
| `VWCE-XETRA` as shipped | 87, `shortlist` | 81.8 → **82** | 87, `shortlist` |
| `replication` absent | 85, `shortlist` | 69.8 → **70, `research`** | 85, `shortlist`, `fund_structure` invisible |
| `holdings_count` absent | 85, `research` (capped) | 70, `research` | **85, `shortlist`** |
| `AGGH-XETRA` as shipped | 84, `research` (capped) | 72.25 → 72, `research` | **84, `shortlist`** |

For each cell in the A and B columns, write the one sentence a user would read,
and mark the part that is false.

Then look at the pattern. Design A charges *every* fund six points for the
tracking figure nobody publishes, and turns a shortlist candidate into `research`
because one descriptive field is unknown. Where it does land on the engine's
decision — rows three and four — it gets there by a different *claim*: "mediocre
diversification", not "unknown diversification". The decision matches; what a
human should do next (look elsewhere, or go and find the figure) does not. Design
B shortlists both records the engine holds back, next to complete records whose
scores look exactly as comparable.

**Optional, with code.** Implement design A in the engine — replace
`total_scored_weight` with `100` in the `investment_score` expression in
`rules::evaluate` — and run `make rules-test`. On the shipped fixtures seven tests
fail, including 9 of the 20 labelled decisions. Most of that damage comes from
`tracking_difference_3y`: a field null across the *whole* universe, silently
costing every fund six points. Revert with
`git checkout -- mcp-server/src/rules.rs evaluation/results/deterministic-etf-baseline.json`.

### 5. Missing versus not applicable

Set `"accumulating": false` under `preferences` in `data/investor_profile.json`
and run `make rules-explain ETF=VWRL-LSE`. The preference appears under
`not_applicable`, not `missing_data`; completeness is unchanged; no cap can fire
from it. A mandate that does not care about something is not uncertain about it.
Restore with `git checkout -- data/`.

### 6. An unknown that the engine still reports as a zero

One edge of the current policy collapses "unknown" into "no fit". Switch off all
four `preferences` in the profile, then:

```bash
make rules-explain ETF=VWCE-XETRA FACTS='{"asset_class": "", "region": ""}'
```

Completeness is 0.7, so `CAP-COMPLETENESS` does not fire. Both profile-fit
components are now unavailable — `profile_fit.available_weight` is `0` — and the
engine reports `profile_fit.fraction` as `0.0`. `CAP-PROFILE-FIT` fires, and its
message says the fund *"earns less than half of the available profile-fit
weight"*. There is no available weight. The outcome (`research`) is conservative;
the explanation is design A, one level up. Restore with `git checkout -- data/`.

**Question.** What should `fraction` be when nothing could be measured, and which
cap — if any — should fire? Write the policy before you write the code. This is
current behaviour, deliberately not fixed in this repository; it is documented
in
[LIMITATIONS.md](../LIMITATIONS.md#a-profile-fit-cap-can-fire-with-an-inaccurate-explanation-when-no-fit-could-be-evaluated)
and [CHALLENGES.md](CHALLENGES.md#open-problems).

## What to take away

* "Grounded" and "complete" are different properties. A tool can return only
  real facts and still not return enough of them.
* Every scoring system already has a missing-data policy. The only choice is
  whether it is written down, versioned and tested, or emerges from `None → 0.0`.
* Renormalisation and caps are one design: the first keeps the score honest about
  what is known; the second keeps the decision honest about what is not.
* Uncertainty has to be *in the payload*, typed, with the weight it cost. A model
  cannot disclose an absence it was never told about.

## Go deeper

* Reference: [ARCHITECTURE.md — missing data is a policy](../ARCHITECTURE.md#missing-data-is-a-policy-not-an-accident),
  [renormalisation is published, not implied](../ARCHITECTURE.md#renormalisation-is-published-not-implied),
  [VERIFICATION.md — missing data](../VERIFICATION.md#missing-data)
* Source: `missing_data_policy` and `decision_caps` in
  [`rules_spec.json`](../../data/rules_spec.json); `score_metric`, `evaluate` and
  the `missing data` tests in [`rules.rs`](../../mcp-server/src/rules.rs)
* Next: [03 — Design evidence for the model](03-design-evidence-for-the-model.md)
