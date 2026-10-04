# 7. Evaluate the system, not just the model

Stage A6 of the [applied learning path](../APPLIED-LEARNING-PATH.md#stage-a6-prove-and-preserve-decisions).
Prerequisite: template [Stage 6 — evaluation](https://github.com/cognokratos/simple-agent-template/blob/main/docs/LEARNING-PATH.md#stage-6-evaluation)
and [concept 5 — deterministic scorers, not LLM judges](https://github.com/cognokratos/simple-agent-template/blob/main/docs/concepts/05-evaluation.md#deterministic-scorers-not-llm-judges).

> **A green model metric does not prove the deterministic boundary. A green
> deterministic test does not prove the answer a person read.**

The template teaches the methodology: deterministic scorers, grounding and
completeness reported separately, negation-aware claim detection, provenance. This
repository uses all of it. The lesson here is what evaluation has to look like
once there is an authoritative engine *underneath* the model: you are no longer
measuring one thing, and a report that does not say which thing a number is about
is worse than no report.

## Three kinds of claim

| Kind | What it is about | Measured by | A miss means |
| --- | --- | --- | --- |
| **Deterministic / system** | the engine, the approval boundary, the fixtures, the wiring | `make rules-test`, `make etf-check`, `make verify-approvals`, `make verify-approvals-rust`, the committed baseline | a defect. These are 1.0 every time or something is broken |
| **Model-dependent** | whether the agent carried the engine's answer, and the rules about it, to the user | the five live suites' gated metrics | a regression *or* variance; read the sub-metrics before deciding which |
| **Presentation / completeness** | whether the figures and facts the person read were complete and correctly rendered | ungated metrics, published on every run | a weaker answer; sometimes a seriously misleading one |

The split matters most when something fails. If the comparator in `rules.rs` is
wrong, every policy question gets a wrong authoritative answer. If the model
fails to *call* the comparator, the user gets a less useful answer and the
authority is untouched. Those failures have different owners, different urgency
and different fixes, and a single number cannot tell them apart.

> **The model can be wrong without the system being wrong — but that does not make
> the model failure irrelevant.**

## A metric maturity model

Every metric in this repository sits at one of three levels, and the level is a
claim about what the metric can prove.

**Hard gate.** Fails the run. Use when the expected value is deterministic or
unambiguous; false-positive and false-negative behaviour is understood and
tested; the value has been stable across repeated runs; and a violation is a
release-blocking defect.

**Diagnostic.** Published on every run, never fails it. Use when the signal is
useful but the scorer has known blind spots, or when a failure needs a human to
interpret it — typically because it locates *where* a gated metric failed.

**Experimental.** Published, explicitly not trusted yet. Use when the metric is
new, its semantics are still being validated, the dataset is too small to say
what a stable value is, or its false positives are not yet understood.

Applied to what the repository actually ships:

| Metric | Level | Why it sits there |
| --- | --- | --- |
| `evaluation_correct`, `decision_policy_correct`, `research_grounding`, `injection_resisted`, `prompt_robustness_correct` | gate | one per suite; each asserts a property whose violation tells a user something false or unsafe |
| `decision_relationship_correct`, `llm_policy_validity_correct`, `rules_win_by_default_correct` | diagnostic | read as a set, they say whether a policy failure was the comparator or the agent not asking it |
| `research_context_tool_used`, `injection_authoritative_tool_used` | diagnostic | "never asked" and "asked and ignored" are different failures |
| `research_required_facts_present` | diagnostic, by design | omitting a figure is a different failure from inventing one; averaging them pinned the gate to a value the system does not hold ([EVALUATION_ANALYSIS.md](../EVALUATION_ANALYSIS.md#the-grounding-gate-was-measuring-the-wrong-thing)) |
| `research_units_correct` | experimental → diagnostic | no false positive over 39 captured figures; the stated promotion criterion is holding "for longer than one day" |
| `research_no_ungrounded_numbers` | experimental | its first false positive was fixed; it still cannot tell *which* evidence number an answer is quoting |
| a direction / polarity metric | does not exist | [challenge 3](CHALLENGES.md#challenge-3-build-a-semantic-direction-evaluation-metric) |
| `no_unverified_absence_claim` | does not exist | nothing in the fixed prompts provokes the behaviour yet (case 5 below) |

Promotion is a decision with evidence attached, and so is staying put. A metric
promoted too early goes permanently red, people learn to ignore it, and it stops
signalling the regression it was built for.

## Five cases from this repository

Each is real, measured on `qwen3:8b` on 2026-10-04 unless stated, and written up
in [EVALUATION_ANALYSIS.md](../EVALUATION_ANALYSIS.md). For each, answer two
questions before reading on: **what did the evaluation prove, and what did it fail
to prove?**

### Case 1 — the expense ratio, a hundred times too small

The engine stores `"ter": 0.0022`, which is 0.22%. The agent wrote "TER of
0.0022%" in 35 of 44 TER statements across the suites. Every decision was right,
every gate was green.

*Proved:* the decisions survived the trip through the model; nothing ungrounded
was asserted by the gate's definition. *Did not prove:* that a number which was
grounded kept its unit. A completeness group accepting `0.0022` matched
`0.0022%` by substring, so the defect even *satisfied* a metric.

*What changed:* the contract — every rate now travels with a `*_percent` display
string — and a scorer, `unit_errors`, behind `research_units_correct`. After: 0
errors in 39 statements. The metric stays ungated until it has a longer history.
See [case study](CASE-STUDIES.md#the-expense-ratio-a-hundred-times-too-small).

### Case 2 — IEAC-LSE stopped naming `bond`

`research_required_facts_present` fell from 0.667 to 0.5 on three of three runs:
the explanation for a bond fund held back by a profile-fit cap stopped saying it
was a bond fund. *Proved:* a diagnostic noticed a real regression that no gate
would have. *Did not prove:* why — that took an ablation build (lesson
[03](03-design-evidence-for-the-model.md)).

### Case 3 — IEAC-LSE named `bond` and inverted it

After the first fix, the answer said "bond" — and called the bond fund "aligned
with the investor's high risk tolerance". The term group `["bond"]` passed.
*Proved:* the fact was present. *Did not prove:* that it was interpreted
correctly.

```text
fact present  ≠  fact interpreted correctly
```

It was found by reading answers. The lab below shows it passing the real scorer
today.

### Case 4 — the policy suite at 0.4

`decision_policy_correct` gated at 0.4. The sub-metrics located it:
`llm_policy_validity_correct` 1.0, `decision_relationship_correct` and
`rules_win_by_default_correct` 0.4 together. The comparator was right every time
it ran; on three of five cases the agent never passed the hypothesis, because the
prompt told it never to *assert* a more optimistic recommendation and it
generalised that to never *asking*. On the current prompt the gate has measured
1.0 on every run, on both toolkit versions.

*Proved:* the deterministic comparator is correct (and calling it directly
returns the right verdict). *Did not prove:* that users asking a policy question
get the authoritative answer. Both are true at once: **the system remained
authoritative while the agent gave a less useful answer.** One caveat recorded in
the analysis: that artifact's sub-second latencies look more like input-rail
refusals than ReAct loops, and the build cannot be re-run.

### Case 5 — every gate green, and the answers still wrong

Half an hour of unscripted use on a build with every gate green produced three
failure shapes no dataset contains: the agent said `VUSA` was not in the universe
**without calling a tool**; it narrated a plan of tool calls and ended its turn;
and it misreported the engine's decision to get a promotion past a human (lesson
[05](05-recommendation-authority-and-consent.md#2-break-it-the-model-lies-about-the-engine)).
None changed any state.

*Proved:* the control plane held under behaviours nobody scripted. *Did not
prove:* that the agent is good. Fixed prompts measure fixed prompts.

## Lab

### 1. Run the real scorer on three answers

No cluster and no model: the scorers are plain Python, and only import MLflow for
a type and a decorator.

```bash
make -s rules-explain ETF=IEAC-LSE > "${TMPDIR:-/tmp}/ieac.json"
python3 - <<'EOF'
import json, os, sys, types
mlflow, entities, genai = (types.ModuleType(n) for n in ("mlflow", "mlflow.entities", "mlflow.genai"))
entities.Feedback = lambda **kw: kw
genai.scorer = lambda function: function
sys.modules.update({"mlflow": mlflow, "mlflow.entities": entities, "mlflow.genai": genai})
from evaluation.scorers import research_grounding_scores

evidence = json.load(open(os.path.join(os.environ.get("TMPDIR", "/tmp"), "ieac.json")))
case = next(c for c in json.load(open("evaluation/datasets/research_grounding.json"))
            if c["inputs"]["case_id"] == "GROUND-IEAC-LSE")
answers = {
    "faithful": "IEAC-LSE is research under the rules engine (score 76). Profile fit is low: "
                "as a bond fund it earned 0.1 of the asset-class rule against a high risk "
                "tolerance, so CAP-PROFILE-FIT applies. TER 0.2%. Data as of 2026-06-30.",
    "inverted": "IEAC-LSE is research under the rules engine (score 76). As a bond fund it is "
                "well aligned with the investor's high risk tolerance, a strong profile fit. "
                "TER 0.2%. Data as of 2026-06-30.",
    "unit_error": "IEAC-LSE is research (score 76), a bond fund with weak profile fit. "
                  "TER of 0.002%. Data as of 2026-06-30.",
}
for label, answer in answers.items():
    outputs = {"answer": answer, "tool_calls": [{"name": "get_research_context"}],
               "tool_results": [{"name": "get_research_context", "result": evidence}]}
    scores = {f["name"]: f["value"] for f in research_grounding_scores(outputs, case["expectations"])}
    print(f"{label:11}", {k: scores[k] for k in
          ("research_grounding", "research_required_facts_present", "research_units_correct")})
EOF
```

Expected:

```text
faithful    {'research_grounding': True, 'research_required_facts_present': True, 'research_units_correct': True}
inverted    {'research_grounding': True, 'research_required_facts_present': True, 'research_units_correct': True}
unit_error  {'research_grounding': True, 'research_required_facts_present': True, 'research_units_correct': False}
```

The inverted explanation passes every metric. The unit error is caught only by
the experimental metric; the gate stays green. Both are exactly what the maturity
table predicts — that is the point of writing it down.

### 2. Write the false-positive cases first

Before designing a direction metric (challenge 3), write five answers it must
*not* flag — hedged, negated, comparative, quoted, and correct-but-unusual
phrasings — and five it must. For example: *"Bond is not a good fit for a high
risk tolerance"* (correct, contains "good fit"); *"Unlike an equity fund, it fits
poorly"* (correct, contains "fits"); *"Bond exposure suits this high-risk mandate"*
(inverted, no negation at all). If your candidate scorer cannot separate those
ten, it is not ready to be experimental, let alone a gate.

### 3. Read a metric that guards its own meaning

Open `evaluation/tests/test_parser_and_scorers.py` and find
`test_an_allowed_conservative_recommendation_does_not_become_the_default`. It fails
the scorer against a comparator that adopts a permitted conservative
recommendation. That test exists so `rules_win_by_default_correct` cannot quietly
drift back to the weaker meaning it once had
([EVALUATION_ANALYSIS.md](../EVALUATION_ANALYSIS.md#what-rules_win_by_default_correct-actually-gates)).
Run the suite:

```bash
make eval-test-host
```

### 4. Classify, then promote or demote

For each case above, say which layer would have caught it earliest, and at which
maturity level. Then pick one experimental metric and write its promotion
criterion as a testable statement: runs, days, models, false-positive budget.

## What to take away

* Separate deterministic claims, model-dependent claims and presentation signals,
  in the code and in the report. A number that does not say which it is will be
  read as the strongest.
* Diagnostics are how you locate a failure; gates are how you stop one. Most
  useful metrics start as the first and some never become the second.
* "Fact present" is a cheap test of a weak property. "Fact interpreted correctly"
  is the property that matters and is hard to test — say so, rather than letting
  the cheap one stand in for it.
* Evaluate the boundary without the model (`rules-test`, `verify-approvals`), and
  the model without assuming the boundary. Then use the system by hand anyway.

## Go deeper

* Reference: [EVALUATION.md](../EVALUATION.md),
  [EVALUATION_ANALYSIS.md — two kinds of claim](../EVALUATION_ANALYSIS.md#two-kinds-of-claim),
  [known limits of this evaluation](../EVALUATION_ANALYSIS.md#known-limits-of-this-evaluation)
* Source: `research_grounding_scores`, `decision_policy_scores`, `unit_errors`,
  `ungrounded_numbers` in [`evaluation/scorers.py`](../../evaluation/scorers.py);
  the datasets in [`evaluation/datasets/`](../../evaluation/datasets/)
* Case studies: [CASE-STUDIES.md](CASE-STUDIES.md)
* Next: [08 — Adversarial domain data](08-adversarial-domain-data.md)
