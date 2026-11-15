"""Probe: what HTTP status does NAT 1.9's real interaction route return for each guard outcome?"""
import asyncio, json, types
from fastapi import FastAPI
from fastapi.testclient import TestClient
from nat.front_ends.fastapi.routes.execution import add_execution_routes
from nat.data_models.interactive import HumanPromptRadio, MultipleChoiceOption, HumanPromptBinary, BinaryHumanPromptOption, HumanPromptText
from nat_streaming_react.interaction_guard import OwnerAwareExecutionStore, ResponderIdentityMiddleware
from nat_streaming_react.approval import decision_options

store = OwnerAwareExecutionStore(strict=False)
app = FastAPI()
worker = types.SimpleNamespace(_execution_store=store)
asyncio.run(add_execution_routes(worker, app))
app.add_middleware(ResponderIdentityMiddleware)
client = TestClient(app, raise_server_exceptions=False)
H = "x-authenticated-user-id"

async def make(prompt, owner="researcher-1"):
    rec = await store.create_execution()
    store.record_owner_for_test(rec.execution_id, owner)
    pending = await store.set_interaction_required(rec.execution_id, prompt)
    store.record_owner_for_test(rec.execution_id, owner)
    return rec.execution_id, pending.interaction_id

loop = asyncio.new_event_loop()
def run(c): return loop.run_until_complete(c)

radio = HumanPromptRadio(text="t", options=decision_options("research", note_available=True))
binary = HumanPromptBinary(text="t", options=[BinaryHumanPromptOption(id="confirm", label="Confirm", value=True), BinaryHumanPromptOption(id="cancel", label="Cancel", value=False)])
text = HumanPromptText(text="t", required=True, placeholder="p")
cases = [
  ("radio offered pair, owner", radio, {"type":"radio","selected_option":{"id":"research","label":"Confirm — research","value":"research","description":""}}, "researcher-1"),
  ("radio cancel, owner", radio, {"type":"radio","selected_option":{"id":"cancel","label":"Cancel","value":"__CANCEL__","description":""}}, "researcher-1"),
  ("radio offered pair, NOT owner", radio, {"type":"radio","selected_option":{"id":"research","label":"x","value":"research","description":""}}, "intruder"),
  ("radio unoffered value", radio, {"type":"radio","selected_option":{"id":"research","label":"x","value":"shortlist","description":""}}, "researcher-1"),
  ("radio wrong type (text)", radio, {"type":"text","text":"hello"}, "researcher-1"),
  ("binary confirm", binary, {"type":"binary_choice","selected_option":{"id":"confirm","label":"Confirm","value":True}}, "researcher-1"),
  ("binary cancel", binary, {"type":"binary_choice","selected_option":{"id":"cancel","label":"Cancel","value":False}}, "researcher-1"),
  ("text rationale", text, {"type":"text","text":"because"}, "researcher-1"),
  ("text empty", text, {"type":"text","text":""}, "researcher-1"),
]
out = []
for name, prompt, response, who in cases:
    e, i = run(make(prompt))
    r = client.post(f"/executions/{e}/interactions/{i}/response", json={"response": response}, headers={H: who})
    out.append((name, r.status_code, r.text[:120]))
# unknown ids, already-resolved
r = client.post("/executions/nope/interactions/nope/response", json={"response":{"type":"text","text":"x"}}, headers={H:"researcher-1"})
out.append(("unknown execution", r.status_code, r.text[:120]))
e, i = run(make(text))
client.post(f"/executions/{e}/interactions/{i}/response", json={"response":{"type":"text","text":"a"}}, headers={H:"researcher-1"})
r = client.post(f"/executions/{e}/interactions/{i}/response", json={"response":{"type":"text","text":"b"}}, headers={H:"researcher-1"})
out.append(("already answered", r.status_code, r.text[:120]))
r = client.post(f"/executions/{e}/interactions/{i}/response", json={"response":{"type":"text","text":"b"},"extra":1}, headers={H:"researcher-1"})
out.append(("unknown top-level field (answered id)", r.status_code, r.text[:120]))
r = client.get(f"/executions/{e}", headers={H:"intruder"})
out.append(("GET status by non-owner", r.status_code, r.text[:160]))
print(json.dumps([{"case": n, "status": s, "body": b} for n, s, b in out], indent=2, ensure_ascii=False))
