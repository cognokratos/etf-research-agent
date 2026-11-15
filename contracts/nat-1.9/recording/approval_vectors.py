"""Regenerate approval-vectors.json and approval-presentation.json from the real minter.

Needs the agent package and NAT 1.9 importable (the agent image, or a venv with
agent/requirements.txt). Output is deterministic: `now` and every nonce are fixed.

    python contracts/nat-1.9/recording/approval_vectors.py contracts/nat-1.9
"""
import json
import os
import pathlib
import sys

SECRET = "a-test-secret-of-at-least-24-characters"  # the Rust tests' TEST_SECRET
os.environ["HITL_APPROVAL_SECRET"] = SECRET

from nat_streaming_react.approval import (  # noqa: E402
    DECISIONS, MAX_TOKEN_TTL_SECONDS, action_payload, build_claims, canonical_json, decision_options,
    mint_token, payload_hash, prompt_text, required_prompts,
)

NOW = 1_794_700_000
REQUEST = "11111111-1111-4111-8111-111111111111"


def vector(name, nonce, **kwargs):
    claims = build_claims(resource_id=kwargs.pop("resource_id", "VWCE-XETRA"), actor_id="researcher-1",
                          request_id=REQUEST, ttl_seconds=600, now=NOW, **kwargs)
    claims["nonce"] = nonce
    return {
        "name": name,
        "claims": claims,
        "canonical_claims": canonical_json(claims),
        "canonical_payload": canonical_json(claims["payload"]),
        "payload_sha256": payload_hash(claims["payload"]),
        "token": mint_token(SECRET.encode(), claims),
    }


VECTORS = [
    vector("commit-human-promotion-non-ascii", "00000000-0000-4000-8000-000000000001",
           action="commit", choice="shortlist", expected_choice="research",
           rationale="Accepting the tracking-difference gap deliberately — Zoë ✓",
           payload=action_payload(llm_recommendation="research",
                                  research_note="Reviewed with Zoë ✓ — broad developed-market exposure at 0.12% TER.")),
    vector("commit-confirm-engine-decision", "00000000-0000-4000-8000-000000000002",
           action="commit", choice="research", expected_choice="research", rationale=None,
           payload=action_payload(llm_recommendation="reject", research_note="   ")),
    vector("commit-human-demotion", "00000000-0000-4000-8000-000000000003",
           action="commit", choice="reject", expected_choice="shortlist", rationale="Concentration too high for me.",
           payload=action_payload()),
    vector("shortlist-promotion", "00000000-0000-4000-8000-000000000004",
           action="shortlist", choice="shortlist", expected_choice="research", rationale="Human promotion.",
           payload=action_payload(research_note="Grounded note.")),
    vector("shortlist-confirm", "00000000-0000-4000-8000-000000000005",
           action="shortlist", choice="shortlist", expected_choice="shortlist", rationale=None,
           payload=action_payload(research_note="Grounded note.")),
    vector("assign", "00000000-0000-4000-8000-000000000006",
           action="assign", choice=None, expected_choice=None, rationale=None,
           payload=action_payload(assignee="  researcher-7  ")),
    vector("escaping-and-control-characters", "00000000-0000-4000-8000-000000000007",
           action="commit", choice="research", expected_choice="research", rationale=None,
           payload=action_payload(research_note='quote " backslash \\ tab \t newline \n unit \u001f slash / emoji 😀 U+2028   end')),
]


def presentation():
    options = {}
    for rules in DECISIONS:
        for note in (False, True):
            options[f"rules={rules},note_available={note}"] = [
                option.model_dump(mode="json") for option in decision_options(rules, note_available=note)]
    rules_table = [
        {"chosen": chosen, "rules_decision": rules, "note_available": note,
         "needs_rationale": needs[0], "needs_note": needs[1]}
        for chosen in DECISIONS for rules in DECISIONS for note in (False, True)
        for needs in [required_prompts(chosen, rules_decision=rules, note_available=note)]
    ]
    return {
        "_comment": "Approval prompt presentation as produced by agent/src/nat_streaming_react/approval.py at the pinned revision. The recorded sse/ corpus shows the same prompts on the wire.",
        "decision_options": options,
        "required_prompts": rules_table,
        "prompt_text_choosing": prompt_text(["ETF: VWCE-XETRA", "Action: commit review decision"], "S", choosing=True),
        "prompt_text_confirming": prompt_text(["ETF: VWCE-XETRA", "Action: assign a research owner"], "S", choosing=False),
        "binary_options": [{"id": "confirm", "label": "Confirm", "value": True}, {"id": "cancel", "label": "Cancel", "value": False}],
        "token_ttl_seconds": {"configured": 600, "minimum": 60, "maximum": MAX_TOKEN_TTL_SECONDS},
    }


if __name__ == "__main__":
    out = pathlib.Path(sys.argv[1])
    (out / "approval-vectors.json").write_text(json.dumps({
        "_comment": "Tokens minted by the real Python minter (approval.build_claims + mint_token) with a fixed `now` and nonce. "
                    "The Rust verifier must accept each signature and reproduce canonical_claims, canonical_payload and payload_sha256 byte for byte. "
                    "exp is in the past by design: these are encoding vectors, not spendable approvals.",
        "secret": SECRET, "now": NOW, "vectors": VECTORS}, indent=2, ensure_ascii=False) + "\n")
    (out / "approval-presentation.json").write_text(json.dumps(presentation(), indent=2, ensure_ascii=False) + "\n")
