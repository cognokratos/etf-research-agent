# 3. Design evidence for a probabilistic consumer

Stage A3 of the [applied learning path](../APPLIED-LEARNING-PATH.md#stage-a3-design-evidence-for-the-model).
Prerequisite: template concept 2 —
[when agent problems are API-design problems](https://github.com/cognokratos/simple-agent-template/blob/main/docs/concepts/02-tools-and-mcp.md#when-agent-problems-are-api-design-problems)
and [tool descriptions are prompts](https://github.com/cognokratos/simple-agent-template/blob/main/docs/concepts/02-tools-and-mcp.md#tool-descriptions-are-prompts).

> Returning correct data is not enough. The model needs an evidence structure
> that exposes the relationships it is expected to explain.

The template shows that a tool's *input* contract and its *description* shape what
a model does. This lesson is about the *output* contract, in the situation a
decision system cares about most: the backend's answer is right, the decision is
right, and the explanation a person reads is still wrong.

> **Tool design is information architecture for a probabilistic consumer.**

## The ladder

There are four distinct properties between a fact existing and a person being told
it correctly. Each one has failed separately in this repository:

```text
data available somewhere
  ≠ data reachable through the tool the model chose
    ≠ the relationship explicit in the tool result
      ≠ a correct explanation
```

| Rung | Failure in this repository | Fixed by |
| --- | --- | --- |
| Available, not reachable | `evaluate_etf` once returned the decision without the fund facts it was derived from; the model assembled a justification from whatever related text was in scope ([ARCHITECTURE.md](../ARCHITECTURE.md#context-minimisation-and-its-floor)) | `etf_facts` in the result: *a decision record must carry its own inputs* |
| Reachable, not explicit | The IEAC-LSE explanation stopped naming "bond", although `asset_class` was in the payload throughout (below) | `component_evidence`: facts grouped under the component they scored |
| Explicit fact, implicit direction | The explanation named "bond" and called it a *fit* for a high risk tolerance | `earned_fraction` on every evidence entry |
| Explicit, still not guaranteed | The model can still get it wrong; no metric checks direction today | Lesson [07](07-evaluate-the-system-not-just-the-model.md) |

Units are the same problem at the bottom rung: `"ter": 0.0022` was reachable and
correct, and the model read it as `0.0022%`. That incident is in
[CASE-STUDIES.md](CASE-STUDIES.md#the-expense-ratio-a-hundred-times-too-small).

## The IEAC-LSE incident, as measured

Everything in this section was observed on `qwen3:8b`, NAT 1.9, on 2026-10-04, and
is recorded in
[EVALUATION_ANALYSIS.md](../EVALUATION_ANALYSIS.md#the-explanation-lost-its-facts-and-how-the-tool-contract-got-them-back).
Run counts are small and stated; none of it is a guarantee about the model.

`IEAC-LSE` is a euro corporate bond fund. The engine scores it 76 — inside the
shortlist band — and holds it at `research` with two caps, one of them
`CAP-PROFILE-FIT`: it earns 6.8 of 20 profile-fit points against a high-risk,
twenty-year growth mandate. The *reason* the profile-fit cap applies is that it is
a bond fund.

1. **The backend was right throughout.** Decision, score and caps never changed.
2. **`asset_class = bond` was in the payload throughout** — in
   `verified_metrics.asset_class` and in the `risk_fit` rule note.
3. **The model stopped mentioning it.** On the build that added percentage fields
   for rates, the grounding answer for IEAC-LSE stated the decision and both caps
   and no longer named the asset class, on three of three runs.
4. **A new instruction displaced an implicit behaviour.** The payload diff was the
   new `*_percent` fields plus one `required_elements` line asking for rates to be
   quoted from them. Rebuilding with that one line removed and the fields kept
   restored "bond" on two of two runs. Nothing had ever *asked* for the facts behind
   a component: `required_elements` asked for components "named from `components`",
   a bare name-to-points map. Naming the facts had been a habit, and habits are
   what an unrelated instruction displaces.
5. **Grouping evidence by component restored the explanation.**
   `component_evidence` puts each fact under the component it scored, and
   `required_elements` asks for it. "Bond" came back on four of four runs…
6. **…with the direction wrong on all four.** The note reads
   `risk_tolerance=high against asset_class=bond` — which reads equally well as a
   match or a mismatch. The answers called the bond fund "aligned with the
   investor's high risk tolerance".
7. **`earned_fraction` made the meaning explicit.** With the share of the rule's
   weight each fact earned (`0.1` for bond against high), five of five runs named
   the bond asset class with the direction right, and none called it a fit.

> **A model cannot reliably explain a causal relationship the tool contract does
> not make visible.**

## This is API design, not prompt tuning

Look at what the fix is and is not:

* It is in [`domain.rs`](../../mcp-server/src/domain.rs) (`component_evidence`) and
  [`server.rs`](../../mcp-server/src/server.rs)
  (`RESEARCH_CONTEXT_REQUIRED_ELEMENTS`), not in the system prompt.
* Nothing in it names a fund, an asset class, a cap or a test case.
* It is a regrouping of the engine's own `matched_rules`, so it can explain a
  decision and has no way to change one.
  `component_evidence_is_the_engines_own_rules_regrouped_for_every_fund` asserts
  exactly that for every fund, and the deterministic baseline is byte-identical.
* It serves every MCP client, not just this agent and not just this model.

Prompt tuning is fitted to one model's current habits and silently decays.
An evidence contract states the relationship once, in the data, and any consumer —
model, UI, auditor — reads the same thing.

`required_elements` is the contract's other half. The explanation constraints in
`get_research_context` were all prohibitions; a model that obeys every
prohibition perfectly still produces an incomplete answer, because nothing asked
for the deterministic result. Stating the obligations is the counterpart to
stating the forbidden.

## Lab

### 1. Build the three contracts from the engine's real output

```bash
make -s rules-explain ETF=IEAC-LSE > "${TMPDIR:-/tmp}/ieac.json"
python3 - <<'EOF'
import json, os
r = json.load(open(os.path.join(os.environ.get("TMPDIR", "/tmp"), "ieac.json")))
e, evidence = r["evaluation"], r["component_evidence"]
fit = e["profile_fit"]["components"]
points = {"etf_id": r["etf_id"], "decision": e["decision"],
          "applied_caps": [c["code"] for c in e["applied_caps"]],
          "components": {k: e["components"][k] for k in fit}}
facts = dict(points, evidence={k: [{"field": x["field"], "observed": x["observed"]}
                                   for x in evidence[k]] for k in fit})
full = dict(points, evidence={k: evidence[k] for k in fit})
for name, payload in [("1: points", points), ("2: facts", facts), ("3: evidence", full)]:
    print(f"--- contract {name}")
    print(json.dumps(payload, indent=2))
EOF
```

Contract 1 is `"risk_fit": 4, "investor_fit": 3`. Contract 2 adds, per component,
which field was read and what it held — `asset_class: "bond"`, `region:
"europe"`. Contract 3 is the shipped `component_evidence`: the same entries with
`earned_fraction` and the rule note.

### 2. Decide what each contract can support

For each sentence, mark the *first* contract under which it is supported by the
payload rather than by the model's general knowledge:

| | Sentence |
| --- | --- |
| a | "IEAC-LSE is held at research by a profile-fit cap." |
| b | "Its profile fit is low because it is a bond fund." |
| c | "As a bond fund it earned 0.1 of the asset-class rule against a high risk tolerance." |
| d | "Its European focus suits the investor." |
| e | "Its European focus helped one rule and hurt another." |

Sentence (b) is the instructive one: under contract 2 a model *can* produce it,
but it would be producing a plausible causal story, not reading one. Contract 2
supports (d) and its opposite equally well. Sentence (e) is true and only
contract 3 shows it: `region = europe` earned 0.8 of `region_fit` under
`risk_fit` and 0.0 of the `broad_diversification` preference under
`investor_fit`. One fact, two rules, opposite directions. No amount of model
capability recovers that from contract 2.

### 3. Optional: put a model in front of each contract

Give each payload to any model you have access to, with the same instruction:
*"Using only this payload, explain why IEAC-LSE is research rather than
shortlist."* Record what it says about bond, Europe and direction. This is a
model-dependent observation; record the model, the date and the number of runs,
and do not generalise from one.

### 4. The real path

On a running cluster, ask:

```text
Why is IEAC-LSE marked research instead of shortlist? Explain the profile fit.
```

The agent should call `get_research_context`. Expand the tool result, find
`deterministic_conclusions.component_evidence`, and check every factual claim in
the answer against it: does it name the asset class, the direction, the cap, and
the missing concentration figure? Then reread `required_elements` in the same
result: each line is an obligation the answer should discharge.

### 5. Break the contract

On a branch, remove `"earned_fraction": rule.fraction,` from
`component_evidence` in `domain.rs`, run `make rules-test`, then
`make rebuild-mcp` and ask the question from step 4 several times.

`make rules-test` fails in exactly two places —
`component_evidence_is_the_engines_own_rules_regrouped_for_every_fund` and
`the_profile_fit_evidence_for_a_bond_fund_names_its_asset_class` pin the
contract — and nowhere else: no decision, score or cap moves, and the evaluation
suites will not reliably notice (lesson [07](07-evaluate-the-system-not-just-the-model.md)
explains why). Whether the model inverts the direction on your build is exactly
the kind of observation to record, not assume. Revert with
`git checkout -- mcp-server/` and `make rebuild-mcp`.

## What to take away

* Treat the model as a consumer with no access to your source code and no
  obligation to make the join you had in mind. If a relationship matters to the
  explanation, put it in the payload as structure.
* Direction is data. "Observed bond" is a fact; "bond earned 0.1 of this rule" is
  the relationship. Only the second is explainable without guessing.
* State obligations, not only prohibitions. `required_elements` is part of the
  contract.
* Fix the contract, not the prompt, when the information is missing from the
  contract. Prompt fixes are fitted to one model's habits; contracts serve every
  consumer.
* A correct decision proves nothing about the explanation. They need different
  evidence and different tests.

## Go deeper

* Reference: [EVALUATION_ANALYSIS.md — the explanation lost its facts](../EVALUATION_ANALYSIS.md#the-explanation-lost-its-facts-and-how-the-tool-contract-got-them-back),
  [ARCHITECTURE.md — context minimisation, and its floor](../ARCHITECTURE.md#context-minimisation-and-its-floor),
  [naming that does not overclaim](../ARCHITECTURE.md#naming-that-does-not-overclaim)
* Source: `component_evidence` and `annotate_rates` in
  [`domain.rs`](../../mcp-server/src/domain.rs); `get_research_context` in
  [`server.rs`](../../mcp-server/src/server.rs); the tests named in step 5
* Case studies: [IEAC lost `bond`](CASE-STUDIES.md#the-explanation-that-lost-bond),
  [IEAC inverted the direction](CASE-STUDIES.md#the-explanation-that-inverted-the-direction)
* Next: [04 — Model the domain before the agent](04-model-the-domain-before-the-agent.md)
