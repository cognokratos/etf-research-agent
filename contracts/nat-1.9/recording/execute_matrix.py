import json, os, urllib.request, urllib.error
os.environ["HITL_APPROVAL_SECRET"] = "freeze-hitl-approval-secret-32-characters"
from nat_streaming_react.approval import build_claims, mint_token, action_payload, approval_secret
URL = "http://127.0.0.1:58080/approvals/execute"
def post(body, key="freeze-mcp-key"):
    h = {"content-type": "application/json"}
    if key: h["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(URL, data=json.dumps(body).encode() if not isinstance(body, bytes) else body, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req) as r: return r.status, r.read().decode()[:400]
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:400]
def tok(**kw):
    base = dict(action="commit", resource_id="XNIF-XETRA", actor_id="researcher-1", request_id="req-1", ttl_seconds=600,
                choice="reject", expected_choice="reject", rationale=None, payload=action_payload())
    base.update(kw); c = build_claims(**base); return mint_token(approval_secret(), c), c
out = {}
t, _ = tok(resource_id="XNIF-XETRA"); out["valid commit"] = post({"approval_token": t, "request_id": "req-1"})
out["replay same token"] = post({"approval_token": t, "request_id": "req-1"})
t2, _ = tok(resource_id="IH2O-LSE", choice="research", expected_choice="reject")
out["premise differs from recomputed engine"] = post({"approval_token": t2, "request_id": "req-1"})
t3, _ = tok(resource_id="IH2O-LSE", choice="research", expected_choice="research")
out["request_id mismatch"] = post({"approval_token": t3, "request_id": "other"})
out["undecodable token"] = post({"approval_token": "not-a-token", "request_id": "req-1"})
out["no api key"] = post({"approval_token": t3, "request_id": "req-1"}, key=None)
out["missing field"] = post({"approval_token": t3})
t4, _ = tok(action="shortlist", resource_id="SPY-ARCA", choice="shortlist", expected_choice="reject", rationale="r", payload=action_payload(research_note="n"))
out["shortlist non-UCITS (hard constraint)"] = post({"approval_token": t4, "request_id": "req-1"})
for k, v in out.items(): print(json.dumps([k, v[0], v[1]]))
t5, _ = tok(action="assign", resource_id="CW8-EPA", choice=None, expected_choice=None, payload=action_payload(assignee="researcher-9"))
print(json.dumps(["assign valid", *post({"approval_token": t5, "request_id": "req-1"})])[:200])
print(json.dumps(["assign replay (nonce)", *post({"approval_token": t5, "request_id": "req-1"})]))
