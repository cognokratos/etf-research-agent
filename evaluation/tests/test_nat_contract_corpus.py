"""Replay the frozen NAT 1.9 SSE corpus through the evaluator's real client.

`contracts/nat-1.9/sse/` holds streams recorded from the real NAT agent running
`agent/config.yml` against the real MCP server (see contracts/nat-1.9/README.md).
Each one is served byte for byte over HTTP to `invoke_live_agent`, so what is
asserted is what this harness extracts from what NAT actually sent — the wire
contract a replacement runtime has to keep emitting.

The mutation tests at the end alter one recorded frame at a time and show the
corresponding assertion would fail, so a wire change cannot pass silently.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "contracts" / "nat-1.9" / "sse"
CONTRACTS = ROOT / "contracts" / "nat-1.9"

# Same stand-in as test_client_capture: the client only decorates with mlflow.
if "mlflow" not in sys.modules:
    mlflow = types.ModuleType("mlflow")
    mlflow.trace = lambda *a, **k: (a[0] if a and callable(a[0]) and len(a) == 1 else (lambda f: f))
    sys.modules["mlflow"] = mlflow

from evaluation import client  # noqa: E402


def stream(name: str) -> bytes:
    return (CORPUS / f"{name}.sse").read_bytes()


def meta(name: str) -> dict:
    return json.loads((CORPUS / f"{name}.json").read_text(encoding="utf-8"))


def frames(raw: bytes) -> list[str]:
    return [block for block in raw.decode("utf-8").split("\n\n") if block.strip()]


def steps(raw: bytes) -> list[dict]:
    out = []
    for block in frames(raw):
        if block.startswith("intermediate_data:"):
            envelope = json.loads(block[len("intermediate_data:"):])
            envelope["payload"] = json.loads(envelope["payload"])
            out.append(envelope)
    return out


def function_output(raw: bytes, name: str) -> list:
    return [s["payload"]["data"]["output"] for s in steps(raw) if s["type"] == "FUNCTION_END" and s["name"] == name]


def baseline_decision(etf_id: str) -> str:
    baseline = json.loads((ROOT / "evaluation/results/deterministic-etf-baseline.json").read_text())
    return next(e["decision"] for e in baseline["evaluations"] if e["etf_id"] == etf_id)


class _Replay:
    """Serve one recorded body to the real client over a real socket."""

    def __init__(self, body: bytes):
        payload = body

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - http.server API
                self.rfile.read(int(self.headers.get("content-length", 0)))
                self.send_response(200)
                self.send_header("content-type", "text/event-stream; charset=utf-8")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        host, port = self.server.server_address
        self._env = {k: os.environ.get(k) for k in ("AGENT_WORKFLOW_URL", "AGENT_API_KEY", "EVALUATION_HTTP_MAX_ATTEMPTS")}
        os.environ["AGENT_WORKFLOW_URL"] = f"http://{host}:{port}/v1/workflow/full"
        os.environ["AGENT_API_KEY"] = "replay-key"
        os.environ["EVALUATION_HTTP_MAX_ATTEMPTS"] = "1"
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def replay(body: bytes) -> dict:
    with _Replay(body):
        return client.invoke_live_agent("replayed question", case_id="corpus")


class CorpusIsCompleteTests(unittest.TestCase):
    EXPECTED = {
        "read-tools-scalar-chunks", "evaluate-audit-history", "input-rail-block", "input-rail-oversize",
        "output-rail-block", "workflow-error-provider", "tool-error-unknown-etf", "tool-not-in-allow-list",
        "commit-confirm-radio", "assign-confirm-binary", "commit-human-promotion", "shortlist-promotion-rationale",
        "commit-cancel", "commit-misreported-premise", "commit-without-request-id", "session-error-frame",
    }

    def test_every_scenario_has_a_stream_and_its_recording_metadata(self):
        names = {path.stem for path in CORPUS.glob("*.sse")}
        self.assertEqual(names, self.EXPECTED)
        for name in names:
            self.assertTrue((CORPUS / f"{name}.json").is_file(), name)

    def test_recorded_frames_use_only_the_known_vocabulary(self):
        for path in CORPUS.glob("*.sse"):
            for block in frames(path.read_bytes()):
                with self.subTest(stream=path.stem, frame=block[:40]):
                    self.assertTrue(
                        block.startswith(("intermediate_data: ", "data: {\"value\": ", "event: interaction_required\ndata: ",
                                          "event: error\ndata: ", '{"code":"workflow_error"')),
                        block[:80],
                    )


class WireShapeTests(unittest.TestCase):
    """Facts about NAT's /v1/workflow/full that consumers parse."""

    def test_step_envelope_has_a_string_payload_holding_json(self):
        for path in CORPUS.glob("*.sse"):
            for block in frames(path.read_bytes()):
                if block.startswith("intermediate_data:"):
                    envelope = json.loads(block[len("intermediate_data:"):])
                    self.assertEqual(set(envelope), {"id", "parent_id", "type", "name", "payload"}, path.stem)
                    self.assertIsInstance(envelope["payload"], str, "payload is double-encoded JSON")
                    self.assertEqual(json.loads(envelope["payload"])["event_type"], envelope["type"])

    def test_tools_stream_as_function_events_never_tool_events(self):
        """NAT 1.9 with native tool calling emits FUNCTION_START/END for MCP tools.

        The evaluator relies on its FUNCTION_* fallback for every tool call; the
        TOOL_* path the template contract describes was never exercised here.
        """

        for path in CORPUS.glob("*.sse"):
            types_seen = {s["type"] for s in steps(path.read_bytes())}
            self.assertFalse(types_seen & {"TOOL_START", "TOOL_END"}, path.stem)
        names = [s["name"] for s in steps(stream("read-tools-scalar-chunks")) if s["type"] == "FUNCTION_START"]
        self.assertIn("etf_mcp__get_etf", names)
        self.assertIn("etf_mcp__evaluate_etf", names)
        approval = [s["name"] for s in steps(stream("commit-confirm-radio")) if s["type"] == "FUNCTION_START"]
        self.assertIn("commit_evaluation", approval, "approval functions are unprefixed")

    def test_filter_steps_controls_workflow_events(self):
        evaluator = {s["type"] for s in steps(stream("read-tools-scalar-chunks"))}
        gateway = {s["type"] for s in steps(stream("commit-confirm-radio"))}
        self.assertIn("WORKFLOW_START", evaluator)
        self.assertNotIn("WORKFLOW_START", gateway)
        self.assertEqual(meta("commit-confirm-radio")["recorded"]["filter_steps"], "TOOL_START,TOOL_END,FUNCTION_START,FUNCTION_END")

    def test_answer_frames_are_value_envelopes_with_string_values(self):
        for block in frames(stream("read-tools-scalar-chunks")):
            if block.startswith("data:"):
                self.assertIsInstance(json.loads(block[5:])["value"], str)

    def test_workflow_exception_is_a_bare_json_block_with_code_message_details(self):
        blocks = [b for b in frames(stream("workflow-error-provider")) if b.startswith("{")]
        self.assertEqual(len(blocks), 1)
        error = json.loads(blocks[0])
        self.assertEqual(set(error), {"code", "message", "details"})
        self.assertEqual(error["code"], "workflow_error")
        # Known: NAT forwards the provider's own text.
        self.assertIn("scripted provider failure", error["message"])

    def test_interaction_event_carries_execution_interaction_prompt_and_response_url(self):
        for name in ("commit-confirm-radio", "assign-confirm-binary", "shortlist-promotion-rationale"):
            event = meta(name)["interactions"][0]["prompt_event"]
            self.assertEqual(set(event), {"event_type", "execution_id", "interaction_id", "prompt", "response_url"}, name)
            self.assertEqual(event["response_url"],
                             f"/executions/{event['execution_id']}/interactions/{event['interaction_id']}/response")
            self.assertIsNone(event["prompt"]["timeout"], "pending approvals never expire under NAT")


class EvaluatorExtractionTests(unittest.TestCase):
    def test_read_stream_tools_results_and_scalar_chunks(self):
        result = replay(stream("read-tools-scalar-chunks"))
        self.assertEqual(
            result["answer"],
            "CSPX-LSE: the deterministic engine decided shortlist with an investment score of 86 out of 100. "
            "true 1e3 0 — a research decision only.",
        )
        self.assertEqual([c["name"] for c in result["tool_calls"]], ["get_etf", "evaluate_etf"])
        self.assertEqual(result["tool_calls"][1]["arguments"], {"etf": "CSPX-LSE", "llm_recommendation": None})
        evaluation = next(r["result"] for r in result["tool_results"] if r["name"] == "evaluate_etf")
        self.assertEqual(evaluation["evaluation"]["decision"], baseline_decision("CSPX-LSE"))
        self.assertEqual(evaluation["evaluation"]["investment_score"], 86)
        self.assertFalse(result["blocked"])
        self.assertTrue(result["evaluation_metadata"]["guardrail_output_event_present"])
        self.assertIsNotNone(result["agent_trace_id"])

    def test_tool_results_carry_raw_fractions_and_display_percentages(self):
        result = replay(stream("read-tools-scalar-chunks"))
        etf = next(r["result"] for r in result["tool_results"] if r["name"] == "get_etf")
        self.assertIsInstance(etf["etf"]["ter"], float)
        self.assertTrue(etf["etf"]["ter_percent"].endswith("%"))

    def test_input_rail_blocks_are_scored_from_the_decision_event(self):
        result = replay(stream("input-rail-block"))
        self.assertTrue(result["blocked"])
        self.assertEqual(result["guardrail"]["input"]["decision_source"], "llm_and_deterministic_block")
        self.assertEqual(result["guardrail"]["input"]["deterministic_block_matches"], ["prompt_injection"])
        self.assertEqual(result["tool_calls"], [])

    def test_oversized_input_is_refused_deterministically(self):
        result = replay(stream("input-rail-oversize"))
        self.assertTrue(result["blocked"])
        self.assertEqual(result["guardrail"]["input"]["decision_source"], "deterministic_input_limit")
        self.assertEqual(result["guardrail"]["input"]["input_limit"], 32000)

    def test_output_rail_replaces_an_answer_containing_a_secret(self):
        result = replay(stream("output-rail-block"))
        self.assertTrue(result["guardrail"]["output"]["blocked"])
        self.assertEqual(result["guardrail"]["output"]["presidio_outcome"], "skipped")
        self.assertEqual(result["answer"], "Blocked by regex check output rails.")
        self.assertNotIn("ABCDEFGHIJKLMNOP1234", stream("output-rail-block").decode())

    def test_a_workflow_error_block_raises(self):
        with self.assertRaisesRegex(RuntimeError, "Error code: 500"):
            replay(stream("workflow-error-provider"))

    def test_a_session_error_frame_is_silently_ignored(self):
        """Known gap, pinned: `event: error` (NAT's session-acquisition failure framing).

        Synthetic frame in NAT's exact format (see its .json). Neither consumer
        recognises it: the evaluator returns an empty answer and no error.
        """

        result = replay(stream("session-error-frame"))
        self.assertEqual(result["answer"], "")
        self.assertIsNone(result["guardrail"]["input"])

    def test_a_tool_failure_reaches_the_model_as_text(self):
        result = replay(stream("tool-error-unknown-etf"))
        self.assertIn("No ETF matches 'NOPE-XXXX'", result["tool_results"][0]["result"])

    def test_evaluate_etf_appends_to_the_audit_history(self):
        result = replay(stream("evaluate-audit-history"))
        history = result["tool_results"][0]["result"]
        evaluated = [e for e in history["events"] if e["action"] == "ETF_EVALUATED"]
        self.assertTrue(evaluated, "evaluate_etf wrote an audit row although it is described as read-only")
        self.assertEqual((evaluated[0]["actor_type"], evaluated[0]["actor_id"]), ("system", "deterministic-rules-engine"))
        self.assertIsNone(evaluated[0]["request_id"])

    def test_an_unlisted_tool_name_never_executes(self):
        names = {s["name"] for s in steps(stream("tool-not-in-allow-list"))}
        self.assertFalse({n for n in names if "commit_evaluation" in n})

    def test_known_gap_an_approval_call_that_fails_before_prompting_is_invisible(self):
        """Pinned, not endorsed. See docs/NAT-FREEZE.md, evaluator blind spot.

        The harness sends no x-request-id, so under evaluation every approval
        function raises before it prompts and the interaction_required tripwire
        never fires. With the shipped EVALUATION_TOOL_NAMES (the six read tools,
        in both the client default and docker-compose.yml) the call is not
        captured either, so the mutation-absent scorers see nothing.
        """

        self.assertTrue({"commit_evaluation", "shortlist_etf", "assign_etf"}.isdisjoint(client.KNOWN_TOOL_NAMES))
        recorded = [s for s in steps(stream("commit-without-request-id")) if s["name"] == "commit_evaluation"]
        self.assertTrue(recorded, "the approval function really was invoked")
        result = replay(stream("commit-without-request-id"))
        self.assertEqual(result["tool_calls"], [])
        self.assertEqual(result["tool_results"], [])

    def test_approval_streams_are_refused_by_the_read_only_harness(self):
        for name in ("commit-confirm-radio", "assign-confirm-binary"):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "requested human interaction"):
                replay(stream(name))


class ApprovalWireTests(unittest.TestCase):
    def test_commit_prompt_offers_every_decision_plus_cancel_as_presented(self):
        presentation = json.loads((CONTRACTS / "approval-presentation.json").read_text())
        prompt = meta("commit-confirm-radio")["interactions"][0]["prompt_event"]["prompt"]
        self.assertEqual(prompt["input_type"], "radio")
        self.assertEqual(prompt["options"], presentation["decision_options"]["rules=research,note_available=False"])
        self.assertIn("Deterministic engine (authoritative): research", prompt["text"])
        self.assertIn("Model recommendation (advisory only): research", prompt["text"])

    def test_assign_prompt_is_a_binary_confirm_or_cancel(self):
        prompt = meta("assign-confirm-binary")["interactions"][0]["prompt_event"]["prompt"]
        self.assertEqual(prompt["input_type"], "binary_choice")
        self.assertEqual(prompt["options"], [{"id": "confirm", "label": "Confirm", "value": True},
                                             {"id": "cancel", "label": "Cancel", "value": False}])

    def test_guard_refusals_return_500_and_leave_the_prompt_pending(self):
        responses = meta("commit-confirm-radio")["interactions"][0]["responses"]
        self.assertEqual([r["status"] for r in responses], [500, 500, 500, 204])
        self.assertEqual(responses[0]["sent"]["user"], "intruder")

    def test_a_human_promotion_collects_rationale_then_note(self):
        interactions = meta("commit-human-promotion")["interactions"]
        self.assertEqual([i["prompt_event"]["prompt"]["input_type"] for i in interactions], ["radio", "text", "text"])
        result = json.loads(function_output(stream("commit-human-promotion"), "commit_evaluation")[0])
        self.assertTrue(result["committed"])
        self.assertEqual(result["result"]["final_decision"], "shortlist")
        self.assertEqual(result["result"]["human_override_decision"], "shortlist")
        self.assertEqual(result["result"]["default_decision"], baseline_decision("EQQQ-LSE"))

    def test_a_shortlist_promotion_asks_for_the_rationale_instead_of_a_confirmation(self):
        interactions = meta("shortlist-promotion-rationale")["interactions"]
        self.assertEqual(len(interactions), 1)
        self.assertEqual(interactions[0]["prompt_event"]["prompt"]["input_type"], "text")

    def test_cancel_returns_approved_false_and_mints_nothing(self):
        result = json.loads(function_output(stream("commit-cancel"), "commit_evaluation")[0])
        self.assertEqual(result, {"approved": False, "etf_id": "VJPN-LSE", "action": "commit",
                                  "message": "The human cancelled the requested change."})

    def test_a_misreported_premise_is_displayed_as_authoritative_and_refused_at_mutation(self):
        """Known limitation, pinned: the prompt shows the model's report as the engine."""

        prompt = meta("commit-misreported-premise")["interactions"][0]["prompt_event"]["prompt"]
        self.assertEqual(baseline_decision("XAIX-XETRA"), "research")
        self.assertIn("Deterministic engine (authoritative): shortlist", prompt["text"])
        confirm = next(o for o in prompt["options"] if o["id"] == "shortlist")
        self.assertEqual(confirm["label"], "Confirm — shortlist", "a promotion is labelled as a confirmation")
        result = json.loads(function_output(stream("commit-misreported-premise"), "commit_evaluation")[0])
        self.assertFalse(result["committed"])
        self.assertIn("issued against a different deterministic decision", result["result"])

    def test_an_approval_without_a_request_id_is_refused_and_retried(self):
        starts = [s for s in steps(stream("commit-without-request-id"))
                  if s["type"] == "FUNCTION_START" and s["name"] == "commit_evaluation"]
        self.assertEqual(len(starts), 2, "NAT re-invokes a raising approval function (tool_call_max_retries)")
        self.assertNotIn("interactions", meta("commit-without-request-id"))


class MutationTests(unittest.TestCase):
    """Altering one recorded frame changes what the harness extracts."""

    def test_a_coerced_scalar_chunk_would_change_the_answer(self):
        raw = stream("read-tools-scalar-chunks").replace(b'data: {"value": "86"}', b'data: {"value": 86.0}')
        self.assertNotIn(" 86 ", replay(raw)["answer"])

    def test_a_renamed_input_decision_event_would_lose_the_block(self):
        raw = stream("input-rail-block").replace(b"guardrail_input_self_check_decision", b"input_rail_decision")
        self.assertFalse(replay(raw)["blocked"])

    def test_tool_events_without_the_group_prefix_contract_still_match_but_other_names_do_not(self):
        raw = stream("read-tools-scalar-chunks").replace(b"etf_mcp__get_etf", b"etf_mcp__fetch_fund")
        self.assertNotIn("get_etf", [c["name"] for c in replay(raw)["tool_calls"]])

    def test_an_error_framed_as_an_sse_field_would_not_raise(self):
        raw = stream("workflow-error-provider").replace(b'{"code":"workflow_error"', b'event: error\ndata: {"code":"workflow_error"')
        replay(raw)  # no exception: this framing is invisible to the harness


if __name__ == "__main__":
    unittest.main()
