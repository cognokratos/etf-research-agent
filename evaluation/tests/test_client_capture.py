"""Cover for the SSE client this harness shares with the template it is built on.

`evaluation/client.py` is shared infrastructure, and the defects asserted here are
the ones it was fixed for. They are covered separately from
`test_parser_and_scorers.py` because none of them is about this application's
scorers: they are about the wire, and they must keep holding if the ETF suites are
to mean anything.

Three of them are silent-data-loss defects, which is the reason each has a test
rather than a comment:

* a valid JSON scalar chunk (`"0.12"`, `"true"`, `"2026"`) was coerced, so every
  number in a streamed answer was lost — and this application's answers are almost
  entirely numbers;
* `_normalize` coerced a nested `content`, so structured chunks were dropped;
* the MCP `{type, text}` envelope was never unwrapped, so a tool result arrived as
  an opaque string and every scorer that reads `current_evaluation` saw nothing.

The guardrail-event prefix match is here for the same reason: the event names are
magic strings shared across two separately deployed codebases, so an exact-name
match turns a future rail rename into a permanently-`None` metric with nothing
failing.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import types
import unittest
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@dataclass
class FakeFeedback:
    name: str | None = None
    value: object = None
    rationale: str | None = None
    metadata: dict | None = None


def fake_trace(*decorator_args, **decorator_kwargs):
    def decorate(function):
        return function

    if decorator_args and callable(decorator_args[0]) and len(decorator_args) == 1:
        return decorator_args[0]
    return decorate


def fake_scorer(function):
    return function


mlflow = types.ModuleType("mlflow")
mlflow.trace = fake_trace
entities = types.ModuleType("mlflow.entities")
entities.Feedback = FakeFeedback
genai = types.ModuleType("mlflow.genai")
genai.scorer = fake_scorer
mlflow.entities = entities
mlflow.genai = genai
sys.modules.setdefault("mlflow", mlflow)
sys.modules.setdefault("mlflow.entities", entities)
sys.modules.setdefault("mlflow.genai", genai)

from evaluation.client import (  # noqa: E402
    ParsedInvocation,
    _handle_data,
    _handle_intermediate,
    _parse_nat_output,
    invoke_live_agent,
)


class ScalarWireTests(unittest.TestCase):
    """Regression cover for the NAT scalar-chunk data loss.

    Mirrors ui/scripts/verify-nat-wire.mjs. Every literal below is a valid JSON
    scalar; coercing it changed its type and the answer silently lost every
    number, boolean and date fragment. For an application whose answers are
    expense ratios, fund sizes and scores, that is the whole answer.
    """

    LITERALS = (
        "1", "0", "5", "12", "100", "1001", "0042", "true", "false", "null",
        "4250", "75", "2026", "06", "30", "-", "1e3", "NaN", "0.12", "68",
    )

    def test_literal_chunks_are_not_coerced(self):
        for literal in self.LITERALS:
            with self.subTest(literal=literal):
                parsed = _parse_nat_output(literal)
                self.assertIsInstance(parsed, str)
                self.assertEqual(parsed, literal)

    def test_non_container_text_is_preserved(self):
        for text in ("{not json}", "{", "}", "[unclosed", "use {tools} always"):
            with self.subTest(text=text):
                self.assertEqual(_parse_nat_output(text), text)

    def test_containers_are_still_decoded(self):
        decoded = _parse_nat_output('{"choices":[{"delta":{"content":"100"}}]}')
        self.assertIsInstance(decoded, dict)
        self.assertEqual(decoded["choices"][0]["delta"]["content"], "100")
        self.assertIsInstance(_parse_nat_output('[{"content":"a"}]'), list)

    def test_streamed_scalar_deltas_reconstruct_losslessly(self):
        deltas = [
            "VWCE-", "XETRA", " scores ", "68", " and the engine decided ",
            "research", ". TER ", "0", ".", "12", "%. Fund size EUR ",
            "4250", " million. Data as of ", "2026", "-", "06", "-", "30", ".",
        ]
        parsed = ParsedInvocation()
        for delta in deltas:
            _handle_data(parsed, "data: " + json.dumps({"value": delta}))
        self.assertEqual(
            parsed.answer,
            "VWCE-XETRA scores 68 and the engine decided research. TER 0.12%."
            " Fund size EUR 4250 million. Data as of 2026-06-30.",
        )

    def test_non_string_envelope_value_is_rendered(self):
        parsed = ParsedInvocation()
        _handle_data(parsed, 'data: {"value": 100}')
        _handle_data(parsed, 'data: {"value": true}')
        _handle_data(parsed, 'data: {"value": 4250.75}')
        self.assertEqual(parsed.answer, "100true4250.75")

    def test_nested_structured_chat_response_is_still_decoded(self):
        parsed = ParsedInvocation()
        _handle_data(
            parsed,
            "data: " + json.dumps({"value": {"choices": [{"delta": {"content": "87"}}]}}),
        )
        self.assertEqual(parsed.answer, "87")

    def test_data_payload_after_event_line_is_read(self):
        parsed = ParsedInvocation()
        _handle_data(parsed, 'event: message\ndata: {"value": "100"}')
        self.assertEqual(parsed.answer, "100")


class ToolResultCaptureTests(unittest.TestCase):
    """Tool results are what "grounded" is measured against, so losing one
    silently turns every grounding metric into a measurement of nothing."""

    def _event(self, event_id, event_type, name, payload):
        return "intermediate_data: " + json.dumps(
            {"id": event_id, "type": event_type, "name": name, "payload": payload}
        )

    def test_tool_end_result_is_captured(self):
        parsed = ParsedInvocation()
        _handle_intermediate(
            parsed,
            self._event(
                "tool-1",
                "TOOL_END",
                "etf_mcp__search_etfs",
                {"data": {"output": {"results": [{"etf_id": "VWCE-XETRA"}]}}},
            ),
        )
        results = parsed.as_output()["tool_results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "search_etfs")
        self.assertEqual(results[0]["result"], {"results": [{"etf_id": "VWCE-XETRA"}]})

    def test_tool_end_and_function_end_are_deduplicated(self):
        parsed = ParsedInvocation()
        payload = {"data": {"output": {"results": []}}}
        _handle_intermediate(parsed, self._event("tool-1", "TOOL_END", "search_etfs", payload))
        _handle_intermediate(
            parsed, self._event("tool-1", "FUNCTION_END", "search_etfs", payload)
        )
        self.assertEqual(len(parsed.as_output()["tool_results"]), 1)

    def test_function_end_alone_still_captures_a_result(self):
        parsed = ParsedInvocation()
        _handle_intermediate(
            parsed,
            self._event(
                "fn-1",
                "FUNCTION_END",
                "get_etf",
                {"data": {"output": {"etf": {"etf_id": "VWCE-XETRA"}}}},
            ),
        )
        results = parsed.as_output()["tool_results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["result"], {"etf": {"etf_id": "VWCE-XETRA"}})

    def test_mcp_text_content_envelope_is_unwrapped(self):
        """The Rust MCP returns `{type: "text", text: "<json>"}`.

        Left wrapped, `_deterministic_decision` walks an opaque string and finds
        no decision, so the injection and accuracy suites score a captured model
        as compliant.
        """

        parsed = ParsedInvocation()
        _handle_intermediate(
            parsed,
            self._event(
                "tool-2",
                "TOOL_END",
                "evaluate_etf",
                {
                    "data": {
                        "output": {
                            "type": "text",
                            "text": '{"evaluation":{"decision":"research",'
                            '"investment_score":68}}',
                        }
                    }
                },
            ),
        )
        self.assertEqual(
            parsed.as_output()["tool_results"][0]["result"],
            {"evaluation": {"decision": "research", "investment_score": 68}},
        )

    def test_renamed_guardrail_event_is_still_captured_by_prefix(self):
        parsed = ParsedInvocation()
        _handle_intermediate(
            parsed,
            self._event(
                "guard-9",
                "FUNCTION_END",
                "guardrail_output_some_future_rail_decision",
                {"data": {"output": {"stage": "output", "blocked": True}}},
            ),
        )
        output = parsed.as_output()
        self.assertTrue(output["evaluation_metadata"]["guardrail_output_event_present"])
        self.assertTrue(output["guardrail"]["output"]["blocked"])


class UnexpectedInteractionTests(unittest.TestCase):
    """A read-only evaluation suite must never enter a human-approval wait.

    This application's three state-changing tools all pause for a human. A suite
    case that reaches one cannot be answered by a harness with nobody at the
    keyboard, so it is raised as a dataset defect rather than silently timing out
    or, worse, being scored as a refusal.
    """

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("content-length", "0"))
            if length:
                self.rfile.read(length)
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            block = (
                "event: interaction_required\n"
                'data: {"execution_id":"e1","interaction_id":"i1"}\n\n'
            )
            self.wfile.write(block.encode())
            self.wfile.flush()

        def log_message(self, format, *args):
            return

    def test_interaction_required_raises(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), self._Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        previous_url = os.environ.get("AGENT_WORKFLOW_URL")
        previous_key = os.environ.get("AGENT_API_KEY")
        os.environ["AGENT_WORKFLOW_URL"] = (
            f"http://127.0.0.1:{server.server_address[1]}/v1/workflow/full"
        )
        os.environ["AGENT_API_KEY"] = "test-agent-api-key"
        os.environ["EVALUATION_HTTP_MAX_ATTEMPTS"] = "1"
        try:
            with self.assertRaises(RuntimeError) as context:
                invoke_live_agent("Shortlist VWCE-XETRA", "CASE-HITL")
            self.assertIn("human interaction", str(context.exception))
        finally:
            server.shutdown()
            server.server_close()
            os.environ.pop("EVALUATION_HTTP_MAX_ATTEMPTS", None)
            for key, value in (
                ("AGENT_WORKFLOW_URL", previous_url),
                ("AGENT_API_KEY", previous_key),
            ):
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
