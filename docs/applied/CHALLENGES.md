# Challenges

Competency exercises for the applied path. They have requirements and a
definition of done, and no solutions. The template's
[challenges](https://github.com/cognokratos/simple-agent-template/blob/main/docs/CHALLENGES.md)
cover adding tools, entities and approval-gated actions; these assume you can do
that and ask a different question: can you change a **decision system** without
moving authority somewhere it should not be?

Ground rules: work on a branch, keep `make static-check` and `make rules-test`
green, and commit the regenerated baseline with any policy change so the diff is
reviewable. Where a challenge is a design exercise, the deliverable is a written
design that answers every question listed, not code.

| Challenge | Kind | Builds on |
| --- | --- | --- |
| [1. Add a new policy dimension](#challenge-1-add-a-new-policy-dimension) | implementation | [01](01-policy-is-a-program.md), [02](02-uncertainty-is-policy.md), [03](03-design-evidence-for-the-model.md) |
| [2. Add a second investor profile](#challenge-2-add-a-second-investor-profile) | implementation | [01](01-policy-is-a-program.md), [06](06-decisions-that-survive-policy-change.md) |
| [3. Build a semantic-direction metric](#challenge-3-build-a-semantic-direction-evaluation-metric) | implementation | [03](03-design-evidence-for-the-model.md), [07](07-evaluate-the-system-not-just-the-model.md) |
| [4. Design V2 fund/listing persistence](#challenge-4-design-v2-fundlisting-persistence) | design | [04](04-model-the-domain-before-the-agent.md), [06](06-decisions-that-survive-policy-change.md) |
| [5. Caller-scoped service identity](#challenge-5-caller-scoped-service-identity) | security design | [05](05-recommendation-authority-and-consent.md), template [stage 8](https://github.com/cognokratos/simple-agent-template/blob/main/docs/LEARNING-PATH.md#stage-8-authentication-identity-and-trust-boundaries) |
| [6. Port the architecture to another domain](#challenge-6-port-the-architecture-to-another-consequential-domain) | design, then implementation | all |

---

## Challenge 1: Add a new policy dimension

Score a property of the fund the engine does not score today.

**Pick it carefully.** It must be a property of the fund that is published and
checkable, not a forecast. `return_3y_annualized` is in the snapshot and is the
obvious candidate, and scoring it would break the one claim this system rests on
— that a score is quality and fit, not expected performance. If you choose a
field that is not in the snapshot yet, you are also adding it to the data.

**Requirements.**

* No ETF-specific threshold, weight or band in Rust. The schema change (a new
  scorable field) is code; how it counts is `rules_spec.json`.
* Validation: a specification that names your field wrongly, or gives it
  inconsistent weights, fails at boot and in `make rules-test`.
* Missing-data semantics, decided and written down: is the field critical? does it
  count toward completeness? What does an unrecognised value mean? The answer goes
  in `missing_data_policy`, with the reasoning beside it, as
  `critical_fields_note` does today.
* Evidence: the new rule appears in `component_evidence` with a meaningful note,
  and `required_elements` still asks for everything an explanation now owes.
* Deterministic tests: labelled cases, with rationales, that pin the decisions your
  dimension is meant to move — and at least one it is meant *not* to move.
* No LLM authority: the model reads the result; nothing it says feeds the score.

**Done when** a later band change for your dimension is a JSON-only diff that
moves the expected funds in the regenerated baseline, and removing the field from
one fund in memory (`make rules-explain FACTS=…`) renormalises and reports
exactly as your policy says.

**What makes it hard.** Every layer touches it — `EtfFacts`, `SCORABLE_FIELDS`, the
seed, the schema's `CHECK` constraints, `scripts/validate_etf_fixtures.py`, the
read models — and a weight added to one component has to come from somewhere,
which moves every score in the universe.

## Challenge 2: Add a second investor profile

Evaluate the same fund universe under two mandates.

**Requirements.**

* Clear profile provenance: every evaluation, committed decision and audit row
  says *which* profile produced it, not only which version. Today
  `audit_events` stores `profile_version` and no `profile_id`; with one profile
  that is unambiguous, and with two, `"1.0.0"` is not.
* No policy-generation ambiguity: lesson [06](06-decisions-that-survive-policy-change.md)'s
  guarantees hold across profiles, not just across versions.
* Labelled expectations per profile. Several engine tests encode the shipped
  mandate's behaviour (lesson [01](01-policy-is-a-program.md#5-change-the-mandate-not-the-engine));
  decide which are properties of the engine and which are properties of a mandate.
* A comparison: a reviewable artifact showing how each fund's decision moves
  between the two mandates, generated by the shipped engine.

**The authority question you must answer first.** Who selects the profile for a
request? If the model can choose it — a tool argument, say — the model has chosen
the decision by choosing the mandate. If the user can, how is that bound to their
identity and recorded? Write that down before writing code.

**Done when** the same fund can be committed under each profile by different
people, and its history says which mandate each decision answered to.

## Challenge 3: Build a semantic-direction evaluation metric

Detect whether an explanation says a fact helped or hurt in the direction the
deterministic evidence supports.

`research_required_facts_present` checks that "bond" appears. It passes "as a bond
fund it earned only 0.1 of the asset-class rule" and "bond exposure aligns with a
high risk tolerance" alike (lesson [07](07-evaluate-the-system-not-just-the-model.md#1-run-the-real-scorer-on-three-answers)).

**This is not trivial.** Direction is expressed in prose in open-ended ways —
"weak fit", "works against", "only 0.6 of 6", "unlike an equity fund", "a poor
match" — and negation, comparison and hedging all flip or blur it. A regex that
looks plausible on five examples will be wrong on the sixth.

**Requirements.**

* Ground truth from the engine, never from a model: `earned_fraction` in
  `component_evidence` decides which direction is correct.
* A false-positive set (correct answers your scorer must not flag) and a
  false-negative set (inversions it must flag), written *before* the scorer, as
  tests in `evaluation/tests/`.
* A replay over real captured answers, with every flag read by a person.
* A stated maturity — experimental — and a written promotion criterion: how many
  runs, over how long, with what false-positive budget, before it can become a
  diagnostic or a gate.
* No LLM judge. The project's argument is that the first model is not
  load-bearing; a second one in the measuring instrument would undo it.

**Done when** the inverted IEAC-LSE answer fails your metric, every answer in your
false-positive set passes, and the limits of what it can see are written next to
it.

## Challenge 4: Design V2 fund/listing persistence

Design the refactor of V1's listings table into `fund` → `listings`. Do not
implement it.

**Constraints.**

* External semantics preserved: the read models keep their shape, as
  [ARCHITECTURE.md](../ARCHITECTURE.md#listing-identity-versus-fund-identity)
  claims they can.
* Mutation still requires an exact, canonical resource. Decide whether that
  resource is now a fund or a listing, and defend it.
* Rankings aggregate at the level the operation needs, and say which.
* Every existing `audit_events` row stays interpretable — including its
  `etf_id`, its versions and its committed score — after the migration.
* The cross-listing agreement check in `validate_etf_fixtures.py` becomes a
  database constraint or is shown to be unnecessary.

**Deliverable.** A design document: schema, migration, the meaning of every
identifier before and after, how `search_etfs`, `get_research_summary`, the
resolver and the three approval actions change, and the questions from lesson
[04, step 5](04-model-the-domain-before-the-agent.md#5-design-exercise-v2-as-funds--listings)
answered — including the share-class and index-exposure levels.

## Challenge 5: Caller-scoped service identity

[LIMITATIONS.md](../LIMITATIONS.md#deliberate-gaps) records it: the service
credential carries the authority to assert *any* identity. NAT believes whatever
`x-authenticated-user-id` a key-holding caller sends, the approval boundary binds
to it, and two callers hold the key — the gateway and the evaluator. The evaluator
asserts `evaluation-harness`; nothing stops it asserting a real researcher.

Design:

```text
gateway credential     → may assert a human identity
evaluation credential  → may assert only the synthetic evaluation principal
```

**Answer, in the design.**

* Where the binding between credential and assertable identity lives, and why
  there rather than in the gateway or the evaluator (`RequireIdentityHeaderMiddleware`
  in `agent/src/nat_streaming_react/fastapi_worker.py` is where the identity
  requirement is enforced today).
* What an approval minted on behalf of the synthetic principal must be refused
  for, and where.
* How `make auth-test` and `IdentityBoundaryTests` change: the new negative cases,
  stated as tests.
* Rotation, and how two credentials are configured without a half-configured
  deployment starting cleanly — the failure `scripts/verify_approval_surface.py`
  exists to catch for the approval secret.
* What workload identity would replace, and what it would not.

Implementing it is optional. Asserting it — with the negative cases — is the part
that matters.

## Challenge 6: Port the architecture to another consequential domain

Choose a domain where a decision has consequences for someone other than the
person asking: Swiss real-estate research, credit underwriting, AML case review,
insurance claims triage, vendor-risk assessment.

**Before writing the agent**, write the table:

| | Your domain |
| --- | --- |
| **Facts** — what is observed, from which source, as of when, with what provenance | |
| **Policy** — what is specified deterministically, as data, interpreted by code | |
| **Mandate** — the per-user or per-client inputs the policy is evaluated for | |
| **Uncertainty** — which facts are routinely missing, which are critical, and what absence means | |
| **Identity** — every entity identity, and which one each operation needs | |
| **Decision authority** — what computes the default decision | |
| **Advisory model role** — what the model may explain, recommend, compare | |
| **Human role** — who may confirm or override, in which directions, with what record | |
| **Non-bypassable constraints** — what no actor may override | |
| **Audit and version semantics** — what a decision record must carry to stay interpretable after the policy changes | |

Then find your domain's equivalents of this repository's three hard cases: a
record that is *excellent and wrong for this mandate* (IEAC-LSE), a record with a
*missing critical fact that renormalisation would flatter* (AGGH-XETRA), and an
*identifier that names more than one thing* (VUSA).

**Done when** your engine computes every decision with no model in the loop and
passes deterministic tests, your agent can explain a capped decision with the
facts and their direction, and a human override in your domain is recorded with
the actor, the rationale, the engine's decision and the policy generation.

---

## Open problems

Observations about current behaviour, found while writing this curriculum and
deliberately not changed by it. Each is a design question before it is a code
change.

| Observation | Where it shows | The question |
| --- | --- | --- |
| With no scorable profile-fit weight, `profile_fit.fraction` is reported as `0.0` and `CAP-PROFILE-FIT` fires with a message claiming the fund "earns less than half" of weight that does not exist. Reachable on its own only with preferences switched off; the outcome is conservative, the explanation is not true | lesson [02, step 6](02-uncertainty-is-policy.md#6-an-unknown-that-the-engine-still-reports-as-a-zero) | What should "fit" be when nothing about fit is known, and which cap should say so? See [LIMITATIONS.md](../LIMITATIONS.md#a-profile-fit-cap-can-fire-with-an-inaccurate-explanation-when-no-fit-could-be-evaluated) |
| The approval prompt displays the model-supplied `rules_decision` under the label "Deterministic engine (authoritative)". The mutation is safe — the backend recomputes — but a human can consent to a false premise | lesson [05, step 3](05-recommendation-authority-and-consent.md#3-what-the-boundary-did-not-protect) | Should the approval function fetch the engine's decision itself, and what then is `expected_choice`? See [LIMITATIONS.md](../LIMITATIONS.md#approval-prompts-can-display-a-model-misreported-deterministic-decision) |
| `rules_version` and `profile_version` are hand-maintained labels; a policy edit that does not bump them is indistinguishable in history | lesson [06](06-decisions-that-survive-policy-change.md#two-things-the-versions-do-not-cover) | Identify generations by content hash? Refuse to boot on an unchanged version with changed content? |
| Committed decisions record the policy and mandate generation, not the fact snapshot (`data_as_of`) they were made on | lesson [06](06-decisions-that-survive-policy-change.md#two-things-the-versions-do-not-cover) | What is the smallest record that makes a decision reproducible? |
| `RulesSpec::validate` does not require band fractions to be monotonic; a non-monotonic band passes both validators and is caught only by labelled cases | lesson [01, step 4](01-policy-is-a-program.md#4-break-it-validly) | Which semantic properties belong in validation, and which in expectations? |
| No metric checks the *direction* of an explanation, or flags an absence claim made without a tool call, or an authority claim from untrusted text repeated as fact | lessons [07](07-evaluate-the-system-not-just-the-model.md), [08](08-adversarial-domain-data.md) | Challenge 3, and its siblings |
| `audit_events` records `profile_version` but not `profile_id` | challenge 2 | Unambiguous today, ambiguous with a second profile |
