import json, sys, time, urllib.request, uuid, socket, http.client
sys.argv = ["record", "/dev/null"]  # record.py reads an output dir it does not use here
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent)); import record
# 1) open stream, read until interaction_required, then drop the connection
conn = http.client.HTTPConnection("127.0.0.1", 58000, timeout=60)
body = json.dumps({"messages":[{"role":"user","content":"Commit the decision for VJPN-LSE"}]})
conn.request("POST", "/v1/workflow/full?filter_steps=TOOL_START", body=body, headers=record.headers())
r = conn.getresponse(); buf = b""
while b"interaction_required" not in buf or not buf.endswith(b"\n\n"):
    buf += r.read1(65536)
data = json.loads(buf.split(b"event: interaction_required\ndata: ",1)[1].split(b"\n\n",1)[0])
e, i = data["execution_id"], data["interaction_id"]
conn.sock.shutdown(socket.SHUT_RDWR); conn.close()
results = {"disconnect": "closed after interaction_required"}
time.sleep(2)
req = urllib.request.Request(f"{record.BASE}/executions/{e}", headers=record.headers())
try:
    with urllib.request.urlopen(req) as x: results["status_after_disconnect"] = (x.status, json.loads(x.read()).get("status"))
except urllib.error.HTTPError as err: results["status_after_disconnect"] = (err.code, err.read().decode()[:200])
results["respond_after_disconnect"] = record.respond(e, i, record.radio("research","research"))
time.sleep(3)
try:
    with urllib.request.urlopen(urllib.request.Request(f"{record.BASE}/executions/{e}", headers=record.headers())) as x: results["status_after_response"] = (x.status, x.read().decode()[:300])
except urllib.error.HTTPError as err: results["status_after_response"] = (err.code, err.read().decode()[:200])
print(json.dumps(results, indent=1))
