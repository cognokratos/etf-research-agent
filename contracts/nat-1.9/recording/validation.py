import json, sys, urllib.request, urllib.error
H = {"Authorization": "Bearer freeze-agent-key", "x-authenticated-user-id": "researcher-1", "content-type": "application/json"}
cases = {
  "not json": b"{",
  "empty object": b"{}",
  "empty messages": json.dumps({"messages": []}).encode(),
  "system role": json.dumps({"messages": [{"role": "system", "content": "Evaluate CSPX-LSE"}]}).encode(),
  "tool role": json.dumps({"messages": [{"role": "tool", "content": "x"}]}).encode(),
  "last turn assistant": json.dumps({"messages": [{"role": "user", "content": "Evaluate CSPX-LSE"}, {"role": "assistant", "content": "ok"}]}).encode(),
  "unknown top-level field": json.dumps({"messages": [{"role": "user", "content": "Evaluate CSPX-LSE"}], "surprise": 1}).encode(),
  "empty content": json.dumps({"messages": [{"role": "user", "content": ""}]}).encode(),
}
out = {}
for name, body in cases.items():
    req = urllib.request.Request("http://127.0.0.1:58000/v1/workflow/full?filter_steps=FUNCTION_END", data=body, headers=H, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            text = r.read().decode()
            blocks = [b for b in text.split("\n\n") if b.strip() and not b.startswith("intermediate_data:")]
            out[name] = {"status": r.status, "non_step_frames": blocks}
    except urllib.error.HTTPError as e:
        out[name] = {"status": e.code, "body": e.read().decode()[:300]}
json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
