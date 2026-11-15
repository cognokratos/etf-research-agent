# contracts/nat-1.9

What the NeMo Agent Toolkit 1.9 agent actually did at
`87daaa3139849a77fe83cb3905d3aee8c53b14e7`, recorded so the Rust/Rig agent can
be held to it. Read [docs/NAT-FREEZE.md](../../docs/NAT-FREEZE.md) for what
each record means and how strictly it binds, and
[docs/AGENT-SERVICE-CONTRACT.md](../../docs/AGENT-SERVICE-CONTRACT.md) for the
contract written out.

| Record | What | Asserted by |
| --- | --- | --- |
| `manifest.json` | pinned revisions, historical provenance, byte-for-byte digests, prompts, domain invariants, suite gates | `scripts/verify_nat_freeze.py` |
| `sse/*.sse` | raw `/v1/workflow/full` bodies, byte for byte | `evaluation/tests/test_nat_contract_corpus.py`, `ui/scripts/verify-nat-corpus.mjs` |
| `sse/*.json` | per stream: request, headers sent, interactions and the status of every response | same, and `gateway/src/proxy.rs` tests |
| `model-tools.json` | the `tools` NAT sent the model | `mcp-server/src/contract.rs`, `agent/verify_nat_contract.py` |
| `mcp-tools.json` | the MCP server's `tools/list` | `mcp-server/src/contract.rs` |
| `approval-vectors.json` | tokens from the real Python minter, fixed `now` and nonces | `mcp-server/src/contract.rs`, `agent/verify_nat_contract.py` |
| `approval-presentation.json` | prompt options and the rationale/note rules | corpus test, `agent/verify_nat_contract.py` |
| `approval-http.json` | interaction-route, disconnect and `/approvals/execute` outcomes | prose evidence; partly re-run by `agent/verify_nat_contract.py` |
| `agent-routes.json` | served routes, auth matrix, `/version`, NAT's own request validation | prose evidence; `/version` fields by `agent/verify_nat_contract.py` |
| `prompts/*.txt` | the system and input-rail prompts, verbatim | `scripts/verify_nat_freeze.py` |

## How the streams were recorded

Not hand-written. The real agent (`nat serve --config_file agent/config.yml`,
the real middleware, interaction guard and approval functions) ran against the
real `mcp-server` binary on a throwaway PostgreSQL seeded by `db/init.sql`. The
only stand-in was the model: `recording/scripted_llm.py`, an OpenAI-compatible
endpoint that plays both agent and guard model from a fixed script, so every
stream is reproducible. It is used to record, never by a test.

```bash
# PostgreSQL with db/init.sql, then mcp-server with DATABASE_URL, MCP_API_KEY,
# HITL_APPROVAL_SECRET, MCP_BIND_ADDRESS=127.0.0.1:58080 and the data/ paths
python3 contracts/nat-1.9/recording/scripted_llm.py 58111 &
LLM_BASE_URL=http://127.0.0.1:58111/v1 LLM_MODEL=scripted LLM_GUARD_MODEL=scripted \
  ETF_MCP_URL=http://127.0.0.1:58080/mcp NAT_GATEWAY_API_KEY=freeze-agent-key \
  MCP_API_KEY=… HITL_APPROVAL_SECRET=… nat serve --config_file agent/config.yml --port 58000 &
python3 contracts/nat-1.9/recording/record.py <out-dir>       # sse/
python3 contracts/nat-1.9/recording/routes.py                  # agent-routes.json (+ validation.py)
python3 contracts/nat-1.9/recording/disconnect.py              # cancellation row of approval-http.json
python contracts/nat-1.9/recording/interaction_status.py       # needs NAT importable
python contracts/nat-1.9/recording/execute_matrix.py           # needs NAT importable
python contracts/nat-1.9/recording/approval_vectors.py contracts/nat-1.9
python contracts/nat-1.9/recording/session_error_frame.py contracts/nat-1.9/sse
```

`sse/session-error-frame.sse` is the one synthetic stream: NAT can emit that
framing, but no shipped path reaches it. Its `.json` says so.

Credentials in the records are the throwaway values above. UUIDs, timestamps
and request ids are as recorded; tests never depend on their values.

## Changing a record

Only for an intended behaviour change, in the same commit as the change, with
`python3 scripts/verify_nat_freeze.py --update-digests` and a reviewed diff.
`mcp-tools.json` regenerates with `UPDATE_CONTRACTS=1 cargo test contract::`
in `mcp-server/`. Once the NAT agent is gone these records stop being
re-recordable — that is the point of keeping them.
