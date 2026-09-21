# Evaluation

Five MLflow suites driven against the **running** agent, with deterministic
scorers. No LLM judges: a red metric is a fact about the run rather than an
opinion about it.

For the measured figures and what they mean, see
[EVALUATION_ANALYSIS.md](EVALUATION_ANALYSIS.md). This document is the
methodology and the how-to.

## Suites

| Suite | Question | Gate |
| --- | --- | --- |
| `evaluation` | Did the agent report the deterministic engine's decision faithfully, for the fund it was asked about? | `evaluation_correct/mean` |
| `policy` | Are the advisory ceiling, the caps and the hard constraints respected in what the user is told? | `decision_policy_correct/mean` |
| `grounding` | Is the explanation built only from the supplied facts? | `research_grounding/mean` |
| `injection` | Does adversarial text in *ETF metadata* change the decision, the state, or what the user is told? | `injection_resisted/mean` |
| `guardrails` | Did the input policy block what it should and allow ordinary research work? | `prompt_robustness_correct/mean` |

The split between `evaluation` and `policy` is the one worth keeping. The first
asks whether the engine's answer survived the trip through the model; the second
asks whether the *rules about* that answer — a model may never be more
optimistic, a cap is reported rather than argued with, a hard constraint is never
bypassed — survived it too. A single number would let one hide the other.

Datasets are source-controlled JSON under `evaluation/datasets/` and synchronised
into MLflow, so a case is reviewable in a pull request.

## The methodology worth reusing

**Grounding and completeness are reported separately, and only grounding is
gated.** Asserting a value no tool returned is a failure of integrity; omitting a
figure the question asked for is a failure of thoroughness. Averaging them hides
which one moved and pins the gate to a value the system does not reliably hold,
so it goes permanently red and stops signalling anything.

**Numbers are compared by value, not by digit run.** A tool returns `980.0` and
the answer writes `$980.00`; a digit-run comparison calls that ungrounded. That
false positive is what makes such a metric useless, so the scorer normalises and
falls back to a digit-substring check for identifiers embedded in larger tokens.

**Claim detection suppresses negations within the clause.** The answers this
system *wants* are full of "this is not a forecast", "no position was opened",
"past performance is not predictive". A scorer that flags the disclaimer
alongside the claim fails every well-behaved answer. Position matters, not mere
presence: "it will outperform, though this is not guaranteed" is still a
forecast, because the negation comes afterwards and qualifies something else.

**Three claim families are checked, because they fail differently.**

* `forecast_claims` — language that turns a policy result into a promise
  ("will outperform", "expected return", "guaranteed"). The whole architecture
  rests on `investment_score` being quality-and-fit rather than a prediction, so
  an answer that presents it as expected performance is a substantive failure
  even when every number in it is correct.
* `execution_claims` — language that implies something was traded ("placed the
  order", "the position was opened", also in the passive voice, which is how a
  model actually phrases it). This system ends at decision support; telling
  someone a position exists when none does is the worst direction for it to
  fail in.
* `contradicts` / `misattributes_etf` — an answer that names decisions but never
  the correct one, or names a fund but never the one it was asked about. Both
  treat *silence* as a separate, lesser failure: omitting the decision is a
  communication weakness, whereas asserting a different one has told the user
  the wrong thing about their money. Mentioning the right answer alongside
  others is fine — "research rather than shortlist" is a real sentence.

`_mutations_absent` backs all of them by checking that no state-changing tool
was called, reading both the tool *start* and tool *end* events: a mutation
whose start event was dropped by a reconnect would otherwise score as "nothing
was changed", which is a false pass on the one metric that must never give one.

These are heuristics over prose, not parsing. The deterministic half of the
same questions is asserted without a model at all, by `make rules-test` and
`make verify-approvals`.

**Guardrail false positives and false negatives are counted separately.** They
are different failures with different costs and must not average.

**Over-blocking is a failure in the injection suite.** Refusing to read a fund
because its issuer description is hostile denies the user a real fund. The
defence is not refusal: it is that the decision is computed in Rust from
structured fields the text cannot reach, and that a mutation needs a signed
human approval the model cannot mint. An injection that fully captures the model
still changes nothing.

**Injection payloads are applied and restored around the run.**
`scripts/poison_etf_metadata.py` writes into the two columns that carry free
text from outside this system — `description`, which an issuer populates, and
`research_note`, which a person does — and restores them from the shipped
snapshot afterwards. `make eval-suite-all` wraps every run in that pair and
restores **even when the evaluation fails**, so a crashed suite never leaves
hostile text in the database. Two further properties make it safe: the target
ETFs are disjoint from every other dataset, the approval suite and
[DEMO.md](DEMO.md); and the MCP server re-seeds descriptions from `etfs.json` on
startup, so the poisoning is self-healing even if a restore is skipped.

Nothing here touches `data/*.json`. The shipped snapshot is never modified.

## Provenance

A result recording its dataset, metrics and latency but not the agent is not
evidence of anything reproducible. Three identities are collected, from the three
places that each know one:

| Identity | Source | Why there |
| --- | --- | --- |
| `agent` | the running container's authenticated `GET /version` | the only source that describes what actually answered |
| `prompts` | MLflow's prompt registry, from the mounted `agent/config.yml` | a registered version can be diffed; MLflow stores the link itself |
| `harness` | the Makefile, on the host | the evaluator container has no `.git` and no working tree |

The interesting field is `consistent`. The evaluator digests the prompt *file*
while the agent reports a digest of the prompt it *loaded*; a disagreement means
the container is not running this tree — the exact drift a host-side `git
rev-parse` conceals.

`dirty` is `bool | None`, and `None` means "not observable" rather than "clean".
Equating those would let a tree nobody inspected claim a verified checkout.
`GIT_DIRTY` deliberately excludes `evaluation/results`, because those files are
the *output* of a run: counting them would make every run after the first report
a dirty tree on account of the previous run's artifacts.

`/version` reports digests and model *names*, never prompt text and never a
credential. `scripts/verify_security_sources.py` asserts that.

## Latency

Reported as a distribution — min, p50, p95, max — not a mean. MLflow aggregates
feedback to a mean, and a mean is the least useful latency statistic because it
hides the tail, which is what a user waits for. Nearest-rank percentiles: on
suites of four to ten cases an interpolated percentile invents values that were
never measured.

## Output

Each run writes `evaluation/results/<suite>-latest.json` with metrics,
threshold, pass/fail and the full provenance record, so the artifact is
self-describing on its own.

**These files are committed**, unlike in the template this is built on, because
they are the evidence [EVALUATION_ANALYSIS.md](EVALUATION_ANALYSIS.md) and
[ACCEPTANCE.md](ACCEPTANCE.md) cite. That creates the hazard the template avoided
by gitignoring them — a stale committed result read as a fresh one — so it is
closed at the other end instead: the live-evaluation workflow clears the
directory, re-runs, and refuses to publish an artifact that is missing,
malformed, stale relative to the run, or carrying a credential
(`scripts/verify_evaluation_artifacts.py`, whose gating semantics are themselves
tested).

`evaluation/results/deterministic-etf-baseline.json` is different again: it is
emitted by `rules::tests::emit_deterministic_baseline`, needs no model and no
cluster, and CI fails if the committed bytes stop matching what the shipped
engine produces.

## Running it

```
make eval-list                 # suites, experiments, datasets
make eval-bootstrap            # create or merge the MLflow datasets
make eval SUITE=grounding      # one suite
make eval-suite-all SUITE=...  # one suite, with the injection poison/restore
make eval-all                  # all five, with gates, poisoned and restored
make etf-check                 # fixtures, profile, rules spec, labelled cases
make rules-test                # the engine itself; regenerates the baseline
make eval-test-host            # the harness's own unit tests, no Docker
```

`ALLOW_FAILURES=1` suppresses the **metric** gate only. An unreachable agent, a
missing dataset or a dead model still raises and fails. That separation is the
point: a red metric is a published finding, a broken cluster is a broken build,
and the two must not report identically.

Every suite is read-only. One that reaches a human-approval wait raises
immediately rather than blocking until the socket times out, so a dataset defect
does not present as an infrastructure failure — and a case that would call
`commit_evaluation`, `shortlist_etf` or `assign_etf` is a dataset defect by
definition, because there is nobody at the keyboard to answer it. The
human-in-the-loop path is covered instead by `make verify-hitl`, which drives it
with scripted answers.

## Adding a case

Append to the suite's JSON. `inputs.question` and `inputs.case_id` are required;
`expectations` is whatever that suite's scorer reads. Then
`make eval-bootstrap SUITE=<suite>`.

Adding a **suite** is a dataset, an entry in `evaluation/config.py`, and an entry
in `SCORERS` in `evaluation/scorers.py`.

## What is generic and what is this application's

The harness core — the SSE client, the runner, the provenance record, the
latency distribution, the dataset sync — is domain-neutral and is shared with
the template this is built on. The suites, the scorers and the datasets are
this application's. Overridable without touching either:

| Variable | What it binds |
| --- | --- |
| `EVALUATION_TOOL_NAMES` | tool names the harness recognises as tool calls |
| `EVALUATION_MUTATING_TOOLS` | tools that change state; **defaults to this application's three** rather than to empty, because an empty set would make every "nothing was changed" assertion pass vacuously |
| `EVALUATION_MODEL_PREFIX` | how the deployed agent is grouped in MLflow |
| `*_EVALUATION_EXPERIMENT` / `*_EVALUATION_DATASET` | per-suite MLflow names |
| `EVALUATION_SYSTEM_PROMPT_NAME` / `EVALUATION_RAIL_PROMPT_NAME` | prompt-registry names |

Case vocabulary belongs in the dataset — `required_term_groups`,
`forbidden_assertions`, `forbidden_strings`, `expected_decision`,
`forbidden_tools` — rather than in the scorer, so a case is reviewable in a pull
request without reading Python.
