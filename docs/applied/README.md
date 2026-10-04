# Applied decision engineering

Lessons, labs and case studies for engineers who already understand how a secure
production agent is built and want to see what it takes to put one inside a
consequential decision. The front door is
[APPLIED-LEARNING-PATH.md](../APPLIED-LEARNING-PATH.md).

| If you… | Go to |
| --- | --- |
| are new to production agents | the template's [learning path](https://github.com/cognokratos/simple-agent-template/blob/main/docs/LEARNING-PATH.md) first |
| already understand the template's architecture | [APPLIED-LEARNING-PATH.md](../APPLIED-LEARNING-PATH.md) |
| want one decision end to end | [Follow one decision](DECISION-WALKTHROUGH.md) |
| want the real failures | [Case studies](CASE-STUDIES.md) |
| want to test yourself | [Challenges](CHALLENGES.md) |
| need implementation detail | the reference documents below |

## Learn

| Lesson | Stage | You will |
| --- | --- | --- |
| [01 — Policy is a program](01-policy-is-a-program.md) | A1 | Change policy without touching code, then break it and see which validator notices |
| [02 — Uncertainty is policy](02-uncertainty-is-policy.md) | A2 | Predict the renormalised score of an incomplete record, and the false claim each naïve design would make |
| [03 — Design evidence for the model](03-design-evidence-for-the-model.md) | A3 | Build the three evidence contracts the IEAC-LSE incident went through |
| [04 — Model the domain before the agent](04-model-the-domain-before-the-agent.md) | A4 | See why ranking and mutation need different identity semantics |
| [05 — Recommendation, authority and consent](05-recommendation-authority-and-consent.md) | A5 | Walk the trust matrix, and trace a model lying about the engine to a human |
| [06 — Decisions that survive policy change](06-decisions-that-survive-policy-change.md) | A6 | Commit a decision, move the mandate, and read two policy generations apart |
| [07 — Evaluate the system, not just the model](07-evaluate-the-system-not-just-the-model.md) | A6 | Classify metrics by what they can prove, using the repository's own incidents |
| [08 — Adversarial domain data](08-adversarial-domain-data.md) | A3, A5 | Poison issuer text and separate "model compromised" from "authority compromised" |

Also: [Follow one decision](DECISION-WALKTHROUGH.md) ·
[Case studies](CASE-STUDIES.md) · [Challenges](CHALLENGES.md)

## Reference

The lessons explain *why* and guide experiments. These documents are the canonical
description of *what* the system does, and the lessons link into them rather than
repeating them:

| Document | For |
| --- | --- |
| [ARCHITECTURE.md](../ARCHITECTURE.md) | Design decisions and rejected alternatives |
| [APPROVALS.md](../APPROVALS.md) | The approval boundary, token claims, transactional order |
| [SECURITY.md](../SECURITY.md) | Each control and how to check it |
| [EVALUATION.md](../EVALUATION.md) | Suites and scoring methodology |
| [EVALUATION_ANALYSIS.md](../EVALUATION_ANALYSIS.md) | The measured figures and what they mean |
| [VERIFICATION.md](../VERIFICATION.md) | Which command proves which control |
| [LIMITATIONS.md](../LIMITATIONS.md) | Known gaps |
| [DEMO.md](../DEMO.md) | Prompts to type and what should happen |

## Ground rules for the labs

* **Run labs that modify tracked files only from a clean worktree.** Check with
  `git status --short`, and commit or stash your own work first. The documented
  restore commands (`git checkout -- data/`, `git checkout -- mcp-server/`, …)
  deliberately discard the lab's local edits, and they discard any other
  uncommitted changes in the same paths along with them.
* **Most labs need no cluster.** `make rules-explain ETF=<etf_id>` prints one
  fund's evaluation and `component_evidence` from the shipped engine;
  `FACTS='<json>'` overrides scored fields in memory. It never writes anything.
* **Labs that edit `data/` say so and say how to undo it.** The undo is always
  `git checkout -- data/ evaluation/results/deterministic-etf-baseline.json`.
  `make rules-test` regenerates that baseline from whatever policy is on disk, so
  an experiment leaves it modified until you restore it.
* **The running MCP server reads `data/` at boot**, mounted read-only. A policy or
  profile edit takes effect after `docker compose restart mcp-server agent` — no
  image rebuild. Restore and restart again when you are done.
* **Labs that mutate state use funds the approval-boundary suite resets**
  (`VJPN-LSE`, `VHYL-LSE`, …), so `make verify-approvals` puts them back.
  `audit_events` is append-only by trigger; its rows stay, which is the point.
* **Model behaviour varies.** Anything quoted from `qwen3:8b` says so, with its
  date and build. Your results may differ, and finding out is part of the
  exercise.
