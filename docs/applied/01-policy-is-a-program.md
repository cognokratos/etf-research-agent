# 1. Policy is a program

Stage A1 of the [applied learning path](../APPLIED-LEARNING-PATH.md#stage-a1-policy-is-a-program).
Prerequisite: the template's
[deterministic/probabilistic split](https://github.com/cognokratos/simple-agent-template/blob/main/docs/LEARNING-PATH.md#the-path-at-a-glance)
and [overusing agents for deterministic workflows](https://github.com/cognokratos/simple-agent-template/blob/main/docs/concepts/ANTI-PATTERNS.md#overusing-agents-for-deterministic-workflows).

> If a decision can be specified deterministically, encode that specification as
> reviewable, executable policy rather than delegating it to the model.

The template makes the case for computing a ranking in code rather than asking the
model. This lesson is about what comes next: once the decision lives in code,
*which* code, and how a policy change gets the same review, validation and
traceability as a code change without being one.

## Three artifacts, three responsibilities

| Artifact | Is | Owns |
| --- | --- | --- |
| [`mcp-server/src/rules.rs`](../../mcp-server/src/rules.rs) | the interpreter | execution semantics: how a band is selected, what a cap may do, how absent weight is treated, how points are rounded and distributed |
| [`data/rules_spec.json`](../../data/rules_spec.json) | the policy | every weight, band, fraction, matrix cell, cap, threshold, critical-field list and hard constraint |
| [`data/investor_profile.json`](../../data/investor_profile.json) | the mandate | the inputs a policy is evaluated *for*: risk tolerance, which constraints are switched on, which preferences count |

> **Application code defines how policy is interpreted. Policy determines the
> decision.**

The two must not blur. `rules.rs` contains no ETF threshold, no weight and no
preference logic. Search it for `0.002` or `20000000000` and you will not find
them; they are in the specification:

```json
{ "code": "COST-B", "threshold": 0.002, "fraction": 0.85,
  "note": "TER above 0.10% and at or below 0.20%." }
```

What `rules.rs` *does* own is worth stating precisely, because it is where the
line actually sits:

* **The decision vocabulary and its order**, `reject < research < shortlist`.
  `decision_rank` is load-bearing for the override policy (lesson
  [05](05-recommendation-authority-and-consent.md)), so `validate` refuses any
  specification that reorders it.
* **The schema of scorable facts.** `SCORABLE_FIELDS` and `EtfFacts` name the
  fields a policy may read. A typo in the specification fails at boot rather than
  silently withdrawing a component's weight from every fund.
* **The metric kinds and their semantics.** `numeric` bands select by the tightest
  satisfied bound, never by array position; `categorical` scores from an explicit
  vocabulary; `profile_matrix` picks its row from the mandate; `preference`
  contributes nothing in either direction when switched off.
* **Cap and constraint semantics.** A cap can only lower a decision
  (`cap_decision`); a hard constraint replaces it outright.
* **The arithmetic.** Renormalisation, half-up rounding, and the largest-remainder
  distribution that makes component contributions sum exactly to the score.

So the boundary is: *how evidence of a given kind counts* is data; *which kinds of
evidence exist, and what the operators mean* is code. Changing a cost band is a
JSON diff. Scoring a field the engine has never heard of is a code change, because
it extends the schema — that is [challenge 1](CHALLENGES.md#challenge-1-add-a-new-policy-dimension).

## What policy-as-data buys

| Property | Mechanism in this repository |
| --- | --- |
| **Reviewable diffs** | A policy change is a pull request against a JSON file, reviewable by someone who does not read Rust |
| **Validation** | `RulesSpec::parse` → `validate` runs at boot and in every test loader; an invalid policy cannot reach an evaluation from either direction |
| **Reproducibility** | `rules_version` and `profile_version` travel with every evaluation and every audit row; the baseline artifact records the SHA-256 of each fixture that produced it |
| **No rebuild for a policy change** | `data/` is mounted read-only into the MCP container and read at boot; a restart applies it |
| **Another mandate, same engine** | Swap `investor_profile.json` and every evaluation changes with no code path aware of it |
| **One implementation** | Search, summaries, the read models, the mutation path and the published baseline all call `rules::evaluate`; there is no SQL or Python copy of the policy to drift |

The last row is easy to undervalue. [ARCHITECTURE.md](../ARCHITECTURE.md#what-is-stored-and-what-is-recomputed)
records an earlier revision that stored the engine's score in a column so SQL
could sort on it. It went stale on the first policy edit, because nothing reseeds
on a policy change. Policy-as-data only holds if the data has exactly one
interpreter.

## Lab

Everything here runs offline. Steps 2–5 edit files under `data/`; the undo is at
the end and is the same for every step.

> Requires a clean worktree; the restore command discards local edits in these paths. See the [ground rules](README.md#ground-rules-for-the-labs).

### 1. Read the policy as the engine applies it

```bash
make rules-explain ETF=IWDA-AMS
```

Find `cost_efficiency` under `component_evidence`. You should see the observed
TER (`0.002`, with `observed_percent` `"0.2%"`), `earned_fraction` `0.85`, and the
note of the band that matched — `COST-B`. Now open `data/rules_spec.json`, find
`COST-B`, and confirm the two say the same thing. You have just traced one
contribution to the score from the policy to the output with no Rust in between.

### 2. Change a band on purpose

Predict before you edit: if `COST-B`'s threshold moves from `0.002` to `0.0015`,
which funds change band? (Those with a TER above 0.15% and at most 0.20%.) Then:

```bash
python3 - <<'EOF'
import json
path = "data/rules_spec.json"
spec = json.load(open(path))
bands = spec["score_components"][0]["metrics"][0]["bands"]
assert bands[1]["code"] == "COST-B"
bands[1]["threshold"] = 0.0015
json.dump(spec, open(path, "w"), indent=2)
EOF
make rules-test
```

`make rules-test` regenerates `evaluation/results/deterministic-etf-baseline.json`
from whatever policy is on disk. Compare it with the committed one:

```bash
python3 - <<'EOF'
import json, subprocess
committed = json.loads(subprocess.run(
    ["git", "show", "HEAD:evaluation/results/deterministic-etf-baseline.json"],
    capture_output=True, text=True, check=True).stdout)
current = json.load(open("evaluation/results/deterministic-etf-baseline.json"))
before = {e["etf_id"]: e for e in committed["evaluations"]}
for e in current["evaluations"]:
    b = before[e["etf_id"]]
    if (b["investment_score"], b["decision"]) != (e["investment_score"], e["decision"]):
        print(f'{e["etf_id"]:12} {b["investment_score"]:>3} {b["decision"]:9} -> '
              f'{e["investment_score"]:>3} {e["decision"]}')
EOF
```

On the shipped fixtures, five funds move — `IWDA-AMS` 92 → 87, `XDWD-XETRA`
87 → 83, `EIMI-LSE` 86 → 81, `IEAC-LSE` 76 → 71, `QQQ-NASDAQ` 70 → 66 — no
decision changes, and **every test passes**.

That last part is the observation. The tests assert invariants and the labelled
*decisions*; they deliberately do not pin per-fund scores
([EVALUATION_ANALYSIS.md](../EVALUATION_ANALYSIS.md#what-the-labelled-cases-are-and-are-not)
explains why). A score change is reviewed through the baseline diff, which CI
fails on if it is not committed alongside the policy change. Restore before the
next step:

```bash
git checkout -- data/ evaluation/results/deterministic-etf-baseline.json
```

### 3. Break the specification

Make each edit below, run both commands, and restore before the next one:

```bash
make etf-check        # the structural Python validator
make rules-explain    # the engine's own validator, as at boot
git checkout -- data/
```

| Break | `etf-check` | `RulesSpec::validate` (engine, boot) |
| --- | --- | --- |
| `cost_efficiency.weight` 20 → 21, metric unchanged | `component 'cost_efficiency' weight 21 != metrics 20` | `component "cost_efficiency" declares weight 21 but its metrics sum to 20` |
| …and the `ter` metric weight 20 → 21 too | `score component weights sum to 101, not 100` | `score component weights must sum to 100, not 101` |
| `research.min_score` 50 → 45 (overlap) | `threshold gap or overlap at 50` | `decision_thresholds leave a gap or overlap at score 50` |
| `research.min_score` 50 → 51 (gap) | same | same |
| `ter` metric `field` → `"expected_return"` | **passes** | `metric "ter" scores on unknown ETF field "expected_return"` |
| `COST-A.threshold` → `null` (two fall-throughs) | **passes** | `… must have exactly one fall-through band with threshold null, not 2` |
| `COST-F.threshold` → `0.02` (no fall-through) | **passes** | `… not 0` |
| a cap's `max_decision` → `"watchlist"` | `CAP-CRITICAL-DATA names an unknown decision` | `decision cap "CAP-CRITICAL-DATA" names unknown decision "watchlist"` |

Two things to notice. The Python validator is structural and deliberately does not
reimplement the engine, so it misses everything that needs the engine's schema;
`RulesSpec::validate` is the authority, and it is the one the server runs before
it accepts a connection. And every failure is *loud*: none of these edits produces
a slightly different score.

> **Invalid policy should fail at boot or in deterministic verification, never
> silently alter decisions.**

To see the boot failure on a running cluster, make one of the engine-only breaks
and restart:

```bash
docker compose restart mcp-server agent
docker compose logs --tail=5 mcp-server
# ETF research MCP server failed: metric "ter" scores on unknown ETF field "expected_return"
git checkout -- data/
docker compose restart mcp-server agent
```

### 4. Break it validly

Validation proves the specification is *well-formed*, not that it is *right*. Make
a band non-monotonic: set `COST-D`'s `fraction` from `0.4` to `0.95`, so a TER of
0.30–0.50% earns more than one of 0.10–0.20%.

Both validators accept it. `make rules-test` fails — but only
`labelled_test_cases_all_hold` (two of twenty labelled cases), and the baseline it
regenerates shows `CW8-EPA` crossing from `research` 66 to `shortlist` 78. The
policy was caught by labelled expectations written with a rationale, not by
validation. Restore with `git checkout -- data/ evaluation/results/deterministic-etf-baseline.json`.

**Question.** Should `validate` require band fractions to be monotonic in the
direction the metric declares? What legitimate policy would that forbid, and is
that a price worth paying? There is no answer key; it is a policy-language design
decision.

### 5. Change the mandate, not the engine

Set `risk_tolerance` to `"low"` in `data/investor_profile.json` and bump its
`version` to `"1.1.0-lab"`. Run `make rules-test` and the comparison script from
step 2.

Every one of the 31 listings moves. Seven shortlisted listings drop to `research`
(both `VUSA` listings, `SPXS-LSE`, `VEUR-LSE`, `EIMI-LSE`, `IUSN-XETRA`,
`VHYL-LSE`) and three research candidates drop to `reject`. The bond funds rise —
`AGGH-XETRA` 84 → 90, `IEAC-LSE` 76 → 81 — and *stay* at `research`, because the
critical-data cap does not care about the mandate. `git diff --stat` shows only
the profile and the baseline: no code moved.

Several engine tests fail too, for example `quality_without_fit_is_capped_at_research`.
That is not a defect. Those tests specify the *shipped mandate's* behaviour; a
second mandate needs its own labelled expectations, which is
[challenge 2](CHALLENGES.md#challenge-2-add-a-second-investor-profile).

Restore:

```bash
git checkout -- data/ evaluation/results/deterministic-etf-baseline.json
```

## What to take away

* The specification is the policy, the engine is its interpreter, the profile is
  its input. A reviewer can tell which one a change touches from the file list.
* The engine owns semantics and schema; it owns no numbers. Where that line sits
  decides which changes are data and which are code.
* Validation turns an invalid policy into a boot failure. It cannot turn a wrong
  policy into one; labelled expectations and a reviewed baseline diff do that.
* A version string is only useful if it travels with every result. Lesson
  [06](06-decisions-that-survive-policy-change.md) is about what happens when it
  does.

## Go deeper

* Reference: [ARCHITECTURE.md — policy is data, not code](../ARCHITECTURE.md#policy-is-data-not-code),
  [order must not be load-bearing](../ARCHITECTURE.md#order-must-not-be-load-bearing),
  [VERIFICATION.md — deterministic policy](../VERIFICATION.md#deterministic-policy)
* Source: `RulesSpec::validate`, `band_for` and `evaluate` in
  [`rules.rs`](../../mcp-server/src/rules.rs); `band_matching_does_not_depend_on_file_order`
  and `switching_off_a_preference_removes_its_weight_rather_than_penalising_every_fund`
  in its tests
* Next: [02 — Uncertainty is policy](02-uncertainty-is-policy.md)
