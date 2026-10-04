# 4. Model the domain before the agent

Stage A4 of the [applied learning path](../APPLIED-LEARNING-PATH.md#stage-a4-model-the-domain-before-the-agent).
Prerequisite: template [Stage 3 — MCP and capability boundaries](https://github.com/cognokratos/simple-agent-template/blob/main/docs/LEARNING-PATH.md#stage-3-mcp-and-capability-boundaries).

> Agent failures often begin as domain-modelling failures.

When an agent ranks one fund twice, or acts on the wrong record, the trace points
at the model. Usually the model did exactly what the data model allowed. This
lesson is about deciding *what the entity is* before deciding what the agent may
do with it — and about the uncomfortable result that the right answer depends on
the operation.

## Two identities, one row

```text
fund identity     = the economic product            → ISIN         IE00B3XXRP09
listing identity  = one tradable line on one venue  → ticker+venue VUSA on LSE, VUSA on Xetra
```

The shipped snapshot keeps exactly one cross-listed fund on purpose:

| `etf_id` | ISIN | Exchange | Score | Decision |
| --- | --- | --- | --- | --- |
| `VUSA-LSE` | `IE00B3XXRP09` | London Stock Exchange | 80 | `shortlist` |
| `VUSA-XETRA` | `IE00B3XXRP09` | Xetra | 80 | `shortlist` |

Same share class, same ISIN, same ticker, every scored fact identical. V1 stores
**listings**: `etf_id` is the primary key, and every read model names both
identities — `identity.fund_identity` (the ISIN) and `identity.listing`
(exchange and ticker). See `fund_identity()` in
[`domain.rs`](../../mcp-server/src/domain.rs).

## The operation decides the abstraction

| Operation | Correct identity | Why | Implementation |
| --- | --- | --- | --- |
| "Top five candidates" | fund | one economic candidate must not take two slots and hide a fifth | `collapse_listings` in [`server.rs`](../../mcp-server/src/server.rs): first listing in ranked order represents the fund; the others are *named* in `other_listings_of_this_fund`, never dropped silently |
| "How many shortlist?" | both, labelled | over listings one fund counts twice; over funds it does not; a reader needs to know which | `get_research_summary`: `universe.listings`, `universe.distinct_funds`, `by_deterministic_decision` and `by_deterministic_decision_per_fund` |
| "Tell me about VUSA" | resolve, or refuse | a ticker names two rows; picking one would make the canonical id optional in practice | `resolve` in `server.rs`: one match → the row; several → `'VUSA' matches 2 listings: VUSA-LSE, VUSA-XETRA. Use the exact etf_id.` |
| "Shortlist VUSA" | exactly one listing | a mutation that guesses is a mutation on a record nobody chose | the mutation tools take an exact `etf_id`, lock it with `WHERE etf_id = $1 FOR UPDATE`, and the approval token binds that `resource_id` |

> **Aggregation may collapse identities. Mutation must resolve one exact,
> canonical resource.**

Those are not two settings of one "dedupe" flag. Collapsing a ranking is a
*presentation* decision with a recoverable failure: the other listing is named,
and `include_all_listings` shows every row. Guessing a mutation target is an
*authority* decision with an unrecoverable one: the audit trail would record a
human approving a change to a record they never named. A system that uses one
mechanism for both gets one of them wrong.

## Integrity across listings

If two listings of one share class could disagree on a scored field, the engine
would return two evaluations for one candidate, and collapsing them would *hide*
the discrepancy instead of exposing it. So
[`scripts/validate_etf_fixtures.py`](../../scripts/validate_etf_fixtures.py)
asserts that cross-listed rows agree on all fourteen economic fields, and that
each names a distinct exchange. This is the integrity constraint a V1 listings
table cannot express in SQL, enforced at the fixture boundary instead.

## Where the model still fails, and why that is fine

From the unscripted walkthrough in
[EVALUATION_ANALYSIS.md](../EVALUATION_ANALYSIS.md#what-the-suites-do-not-catch-an-unscripted-walkthrough)
(`qwen3:8b`, 2026-10-04, one session): asked *"Tell me about VUSA"*, the agent
replied that VUSA "is not currently in the research universe" — **without calling
any tool**. The domain model was right and the resolver would have said so; the
agent did not ask. That is a tool-use failure in the layer the architecture
assumes is unreliable, and it produced a wrong *answer*, not a wrong *state*:
nothing the model says can reach a mutation without an exact `etf_id`.

## Lab

### 1. See the identities in the data

```bash
python3 - <<'EOF'
import json
from collections import defaultdict
funds = defaultdict(list)
for etf in json.load(open("data/etfs.json")):
    funds[etf["isin"]].append(f'{etf["etf_id"]} ({etf["exchange"]})')
print(f"{sum(map(len, funds.values()))} listings, {len(funds)} distinct ISINs")
for isin, listings in funds.items():
    if len(listings) > 1:
        print(isin, "->", ", ".join(listings))
EOF
```

### 2. Rank with and without the collapse

The four United States equity listings the engine shortlists are `CSPX-LSE` (86),
`VUSA-LSE` (80), `VUSA-XETRA` (80) and `SPXS-LSE` (77). Predict the top three for
each grouping, then check. Either ask the agent:

```text
Show the three highest-scoring United States ETFs the engine would shortlist.
Now the same, but list every exchange listing separately.
```

or call `search_etfs` directly in the MCP Inspector (`make inspector`, then
`make open-inspector`) with
`{"region": "united_states", "decision": "shortlist", "limit": 3}`, and again with
`"include_all_listings": true` added.

Grouped by fund you should get `CSPX-LSE`, `VUSA-LSE` (naming `VUSA-XETRA` in
`other_listings_of_this_fund`) and `SPXS-LSE`. By listing you get `CSPX-LSE`,
`VUSA-LSE`, `VUSA-XETRA` — and `truncated: true`, because `SPXS-LSE`, a
genuinely different fund, fell off the end. That is the failure the collapse
exists to prevent. Note the response's `grouping` and `distinct_funds_matched`
fields: the result says which abstraction it used.

If you asked the agent, check whether it actually passed `include_all_listings`
the second time. Whether it does is a model behaviour; what the tool returns for
each argument is not.

The same properties are asserted without a cluster by
`cross_listings_collapse_to_one_candidate_by_default` and
`a_top_n_ranking_spends_one_slot_per_fund` in
[`server/tests.rs`](../../mcp-server/src/server/tests.rs):

```bash
cd mcp-server && cargo test cross_listings && cargo test top_n
```

### 3. Resolve something ambiguous

Ask the agent *"Tell me about VUSA"*, or call `get_etf` with `{"etf": "VUSA"}` and
then with the ISIN `{"etf": "IE00B3XXRP09"}`. Both are refused with the two
candidate `etf_id`s. Then `{"etf": "vusa-xetra"}`: an exact `etf_id` match wins,
case-insensitively, before any ticker match is considered (`resolve_etf_ids` in
[`store.rs`](../../mcp-server/src/store.rs)).

Now read the mutation tools' argument schemas in `server.rs`: `etf_id` is
documented as *"Exact etf_id"* and goes straight to `lock_etf`, with no resolver
in between. A mutation cannot be handed a ticker at all.

**Question.** Why is it right for `get_etf` to resolve a unique ticker for you,
but wrong for `commit_evaluation` to do the same — even when the ticker is
unique?

### 4. Make the listings disagree

Change `ter` on `VUSA-XETRA` alone in `data/etfs.json` to `0.0009` and run
`make etf-check`:

```text
ETF fixture integrity: FAILED
  listings of IE00B3XXRP09 disagree on ter: {0.0007, 0.0009}. Two listings of one share class must score identically.
```

Restore with `git checkout -- data/`. Then consider what would happen *without*
that check: two evaluations for one candidate, and a ranking that shows whichever
listing happened to score higher, labelled as the fund.

### 5. Design exercise: V2 as `funds` + `listings`

Do not implement this; design it. [ARCHITECTURE.md](../ARCHITECTURE.md#listing-identity-versus-fund-identity)
calls a fund table with listings hanging off it "the correct long-term model",
and claims nothing in the current read models would have to change shape. Test
that claim:

* Which columns move to `funds`, which stay on `listings`, and which constraint
  replaces the fixture check in step 4?
* The engine scores `EtfFacts`. Is the evaluation keyed by fund or by listing?
  What does `search_etfs` return when the caller passes `include_all_listings`?
* Approvals bind `resource_id = etf_id`. Should a decision be committed per fund
  or per listing? What does `audit_events.etf_id` mean afterwards, and how do you
  keep every *existing* audit row interpretable?
* The ISIN identifies a share class, not an exposure. `VWCE-XETRA` (accumulating)
  and `VWRL-LSE` (distributing) are two share classes of the same FTSE All-World
  fund; they have different ISINs and score 87 and 82 here, because this mandate
  prefers accumulating. Three S&P 500 trackers from three issuers sit in the US
  shortlist above. At which of those levels — listing, share class, sub-fund,
  index exposure — should "top five candidates" deduplicate, and is the answer the
  same for every mandate?

The point of the last question: there is no universally correct entity. There is
a correct entity *for each operation*, and the data model has to be able to name
all of the ones you need.

## What to take away

* Name every identity the domain has before choosing a primary key. Put the ones
  you did not choose in the read model anyway.
* Every aggregation should say which identity it aggregates over — in the
  response, not only in the documentation.
* Presentation may collapse; authority must resolve. Never let one code path do
  both.
* An agent that fails to ask a resolver produces a wrong answer. An agent that is
  *given* a resolver for mutations produces a wrong state. Only one of those is
  the model's fault.

## Go deeper

* Reference: [ARCHITECTURE.md — listing identity versus fund identity](../ARCHITECTURE.md#listing-identity-versus-fund-identity),
  [VERIFICATION.md — listing identity](../VERIFICATION.md#listing-identity),
  [DEMO.md — ambiguity is reported, not guessed](../DEMO.md#ambiguity-is-reported-not-guessed)
* Source: `collapse_listings`, `resolve` and `research_summary` in
  [`server.rs`](../../mcp-server/src/server.rs); `resolve_etf_ids` and `lock_etf` in
  [`store.rs`](../../mcp-server/src/store.rs)
* Challenge: [design V2 fund/listing persistence](CHALLENGES.md#challenge-4-design-v2-fundlisting-persistence)
* Next: [05 — Recommendation, authority and consent](05-recommendation-authority-and-consent.md)
