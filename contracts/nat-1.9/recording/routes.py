"""Measure the NAT agent's route surface, auth matrix and /version shape."""
import json, sys, urllib.request, urllib.error
BASE, KEY = "http://127.0.0.1:58000", "freeze-agent-key"
def call(method, path, headers, body=None):
    req = urllib.request.Request(BASE + path, method=method, headers=headers, data=body)
    try:
        with urllib.request.urlopen(req, timeout=30) as r: return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e: return e.code, dict(e.headers), e.read().decode()
K = {"Authorization": f"Bearer {KEY}"}; U = {"x-authenticated-user-id": "researcher-1"}
openapi = json.loads(call("GET", "/openapi.json", {**K, **U})[2])
served = sorted(f"{m.upper()} {p}" for p, v in openapi["paths"].items() for m in v)
matrix = {}
for method, path in [("GET","/health"),("GET","/health/live"),("GET","/health/ready"),("GET","/version"),("POST","/v1/workflow/full"),("GET","/v1/workflow/full"),("GET","/docs"),("GET","/openapi.json"),("GET","/no-such-route"),("GET","/executions/00000000-0000-4000-8000-000000000000")]:
    body = b'{"messages":[{"role":"user","content":"Evaluate CSPX-LSE"}]}' if method == "POST" else None
    row = {}
    for label, h in [("no_key", {}), ("wrong_key", {"Authorization": "Bearer nope"}), ("key_no_identity", K), ("key_blank_identity", {**K, "x-authenticated-user-id": "  "}), ("key_identity", {**K, **U})]:
        if method == "POST" and label == "key_identity":
            row[label] = "200 text/event-stream (see sse/ corpus)"; continue
        s, hdr, text = call(method, path, {**h, "content-type": "application/json"}, body)
        row[label] = {"status": s, "body": text if s in (401,) or len(text) < 120 else "<omitted>", "www_authenticate": hdr.get("www-authenticate")}
    matrix[f"{method} {path}"] = row
# repeated identity, raw socket since urllib merges headers
import http.client
c = http.client.HTTPConnection("127.0.0.1", 58000); c.putrequest("GET", "/version"); c.putheader("Authorization", f"Bearer {KEY}")
c.putheader("x-authenticated-user-id", "a"); c.putheader("x-authenticated-user-id", "b"); c.endheaders(); r = c.getresponse()
matrix["GET /version"]["key_repeated_identity"] = {"status": r.status, "body": r.read().decode()}
version = json.loads(call("GET", "/version", {**K, **U})[2])
version["config_path"] = "<absolute path of the loaded config; /app/config.yml in the image>"
json.dump({"_comment": "Measured against the real NAT 1.9 agent (agent/config.yml, fastapi_worker.AuthenticatedFastApiFrontEndPluginWorker) at 87daaa3 on 2026-11-15. Values, not documentation.",
           "served_routes_from_openapi": served, "auth_matrix": matrix, "version_response": version,
           "version_field_names": sorted(version)}, sys.stdout, indent=2, ensure_ascii=False)
