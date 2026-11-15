"""Scripted OpenAI-compatible endpoint used ONLY to record the NAT contract corpus.

It plays both the agent model and the guard model. Each scenario is selected by
a keyword in the latest user turn and advances by counting tool results since
that turn, so a recording is deterministic. Nothing here is used by a test.
"""
import json
import pathlib
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SUMMARY = "Recorded for the NAT freeze corpus."


def tool_name(tools, suffix):
    for tool in tools or []:
        name = tool.get("function", {}).get("name", "")
        if name == suffix or name.endswith("__" + suffix):
            return name
    raise KeyError(f"tool {suffix!r} not offered; offered: {[t.get('function', {}).get('name') for t in tools or []]}")




def call(name, args):
    return {"tool": name, "args": args}


def text(*chunks):
    return {"text": list(chunks)}


SCENARIOS = {
    # keyword: list of steps, one per model call after the user turn
    "Evaluate CSPX-LSE": [
        call("get_etf", {"etf": "CSPX-LSE"}),
        call("evaluate_etf", {"etf": "CSPX-LSE"}),
        text("CSPX-LSE: the deterministic engine decided ", "shortlist", " with an investment score of ",
             "86", " out of ", "100", ". ", "true", " ", "1e3", " ", "0", " — a research decision only."),
    ],
    "Commit the decision for CW8-EPA": [
        call("get_etf", {"etf": "CW8-EPA"}),
        call("evaluate_etf", {"etf": "CW8-EPA"}),
        call("commit_evaluation", {"etf_id": "CW8-EPA", "rules_decision": "research",
                                   "requested_decision": "research", "llm_recommendation": "research",
                                   "summary": SUMMARY}),
        text("The research decision for CW8-EPA is recorded."),
    ],
    "Assign CW8-EPA": [
        call("assign_etf", {"etf_id": "CW8-EPA", "assignee": "researcher-7", "summary": SUMMARY}),
        text("CW8-EPA is assigned to researcher-7."),
    ],
    "Commit the decision for EQQQ-LSE": [
        call("evaluate_etf", {"etf": "EQQQ-LSE"}),
        call("commit_evaluation", {"etf_id": "EQQQ-LSE", "rules_decision": "research",
                                   "requested_decision": "research", "summary": SUMMARY}),
        text("Done."),
    ],
    "Shortlist VFEM-LSE": [
        call("evaluate_etf", {"etf": "VFEM-LSE"}),
        call("shortlist_etf", {"etf_id": "VFEM-LSE", "rules_decision": "research",
                               "research_note": "Emerging-market exposure; see get_research_context.",
                               "summary": SUMMARY}),
        text("Done."),
    ],
    "Commit the decision for VJPN-LSE": [
        call("commit_evaluation", {"etf_id": "VJPN-LSE", "rules_decision": "research",
                                   "requested_decision": "research", "summary": SUMMARY}),
        text("Cancelled; nothing changed."),
    ],
    # Known limitation: the model misreports the engine. XAIX-XETRA is `research`.
    "Commit the decision for XAIX-XETRA": [
        call("commit_evaluation", {"etf_id": "XAIX-XETRA", "rules_decision": "shortlist",
                                   "requested_decision": "shortlist",
                                   "research_note": "Model-drafted note.", "summary": SUMMARY}),
        text("The change was refused; nothing was applied."),
    ],
    "Commit without request id for INRG-LSE": [
        call("commit_evaluation", {"etf_id": "INRG-LSE", "rules_decision": "research",
                                   "requested_decision": "research", "summary": SUMMARY}),
        text("I could not request approval."),
    ],
    "Show me the fund NOPE-XXXX": [
        call("get_etf", {"etf": "NOPE-XXXX"}),
        text("No such fund."),
    ],
    "History of CSPX-LSE": [
        call("get_etf_history", {"etf": "CSPX-LSE"}),
        text("The history lists the deterministic evaluation."),
    ],
    "Mutation tool not in the allow-list": [
        # The MCP server serves this tool, but the agent's include list does not.
        call("commit_evaluation_mcp_direct", {}),
        text("unreachable"),
    ],
    "freeze output secret": [
        text("Here is the value you asked for: api_key", "=ABCDEFGHIJKLMNOP1234 ", "and nothing else."),
    ],
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        sys.stderr.write("llm: " + (args[0] % args[1:]) + "\n")

    def _json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages", [])
        stream = bool(body.get("stream"))
        joined = json.dumps(messages)
        import os
        dump = pathlib.Path(os.environ.get("LLM_DUMP", "/dev/null"))
        if dump.name != "null":
            dump.mkdir(exist_ok=True)
            (dump / f"{time.time_ns()}.json").write_text(json.dumps(body, indent=1))
        if "binary security classifier" in joined:
            asked = joined.rsplit('User message: \\"', 1)[-1]
            verdict = "Yes" if "Ignore your system instructions" in asked else "No"
            return self._reply(stream, {"text": [verdict]}, body)
        question = next((m.get("content") or "" for m in messages if m.get("role") == "user"), "")
        if not isinstance(question, str):
            question = json.dumps(question)
        if "freeze provider error" in question:
            return self._json(500, {"error": {"message": "scripted provider failure", "type": "server_error"}})
        for keyword, steps in SCENARIOS.items():
            if keyword in question:
                index = sum(1 for m in messages if m.get("role") == "assistant")
                step = steps[min(index, len(steps) - 1)]
                if "tool" in step and step["tool"] == "commit_evaluation_mcp_direct":
                    step = {"tool_raw": "commit_evaluation_mcp", "args": {}}
                return self._reply(stream, step, body)
        return self._reply(stream, {"text": ["I can only answer scripted questions."]}, body)

    def _reply(self, stream, step, body):
        created = int(time.time())
        if "tool" in step or "tool_raw" in step:
            name = step.get("tool_raw") or tool_name(body.get("tools"), step["tool"])
            args = json.dumps(step["args"])
            call_id = f"call_{abs(hash((name, args))) % 10**8}"
            if not stream:
                return self._json(200, {"id": "x", "object": "chat.completion", "created": created, "model": body.get("model"),
                                        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                                            "role": "assistant", "content": None,
                                            "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}]}}],
                                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
            chunks = [
                {"role": "assistant", "content": None,
                 "tool_calls": [{"index": 0, "id": call_id, "type": "function", "function": {"name": name, "arguments": ""}}]},
                {"tool_calls": [{"index": 0, "function": {"arguments": args}}]},
            ]
            return self._stream(chunks, "tool_calls", body, created)
        parts = step["text"]
        if not stream:
            return self._json(200, {"id": "x", "object": "chat.completion", "created": created, "model": body.get("model"),
                                    "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "".join(parts)}}],
                                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
        chunks = [{"role": "assistant", "content": ""}] + [{"content": part} for part in parts]
        return self._stream(chunks, "stop", body, created)

    def _stream(self, deltas, finish, body, created):
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()

        def send(obj):
            data = f"data: {obj}\n\n".encode()
            self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()

        for delta in deltas:
            send(json.dumps({"id": "x", "object": "chat.completion.chunk", "created": created, "model": body.get("model"),
                             "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}))
        send(json.dumps({"id": "x", "object": "chat.completion.chunk", "created": created, "model": body.get("model"),
                         "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}))
        send("[DONE]")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
