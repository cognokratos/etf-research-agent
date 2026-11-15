"""Check the NAT agent still produces what contracts/nat-1.9/ froze.

The frozen records are asserted offline from the consumers' side elsewhere
(evaluator, UI route, gateway, MCP crate). This file asserts them from the
producer's side, against the Python code itself, so the freeze can be re-checked
on the NAT tree at any time — for example immediately before tagging it.

Needs NAT 1.9 and this package importable and the repository's contracts/
directory, so it runs from a checkout rather than inside the image::

    make verify-nat-contract PYTHON=/path/to/venv/bin/python
"""

from __future__ import annotations

import asyncio
import json
import os
import types
import unittest
from pathlib import Path

os.environ["HITL_APPROVAL_SECRET"] = "a-test-secret-of-at-least-24-characters"

from nat_streaming_react import provenance  # noqa: E402
from nat_streaming_react.approval import (  # noqa: E402
    AssignEtfRequest,
    CommitEvaluationRequest,
    ShortlistEtfRequest,
    canonical_json,
    decision_options,
    mint_token,
    payload_hash,
    required_prompts,
)
from nat_streaming_react.interaction_guard import (  # noqa: E402
    OwnerAwareExecutionStore,
    ResponderIdentityMiddleware,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts" / "nat-1.9"


def contract(name: str):
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


class ApprovalVectorTests(unittest.TestCase):
    def test_every_vector_is_reproduced_by_the_minter(self):
        document = contract("approval-vectors.json")
        secret = document["secret"].encode()
        for vector in document["vectors"]:
            with self.subTest(vector=vector["name"]):
                claims = vector["claims"]
                self.assertEqual(canonical_json(claims), vector["canonical_claims"])
                self.assertEqual(canonical_json(claims["payload"]), vector["canonical_payload"])
                self.assertEqual(payload_hash(claims["payload"]), vector["payload_sha256"])
                self.assertEqual(mint_token(secret, claims), vector["token"])


class PresentationTests(unittest.TestCase):
    def test_decision_options_and_prompt_rules_are_unchanged(self):
        frozen = contract("approval-presentation.json")
        for key, options in frozen["decision_options"].items():
            rules = key.split(",")[0].split("=")[1]
            note = key.endswith("True")
            with self.subTest(key=key):
                produced = [o.model_dump(mode="json") for o in decision_options(rules, note_available=note)]
                self.assertEqual(produced, options)
        for row in frozen["required_prompts"]:
            self.assertEqual(
                required_prompts(row["chosen"], rules_decision=row["rules_decision"], note_available=row["note_available"]),
                (row["needs_rationale"], row["needs_note"]),
            )


class FunctionSchemaTests(unittest.TestCase):
    """The approval functions' request models against what the model was sent."""

    def test_request_models_match_the_recorded_model_visible_schemas(self):
        recorded = {t["function"]["name"]: t["function"]["parameters"] for t in contract("model-tools.json")["tools"]}
        models = {"commit_evaluation": CommitEvaluationRequest, "shortlist_etf": ShortlistEtfRequest,
                  "assign_etf": AssignEtfRequest}
        for name, model in models.items():
            with self.subTest(function=name):
                schema = model.model_json_schema()
                sent = recorded[name]
                self.assertEqual(set(schema["properties"]), set(sent["properties"]))
                self.assertEqual(set(schema.get("required", [])), set(sent.get("required", [])))
                for field, spec in sent["properties"].items():
                    for constraint in ("enum", "minLength", "maxLength", "default", "anyOf"):
                        if constraint in spec:
                            self.assertEqual(schema["properties"][field].get(constraint), spec[constraint], f"{field}.{constraint}")


class VersionTests(unittest.TestCase):
    def test_version_reports_the_frozen_field_set(self):
        frozen = contract("agent-routes.json")["version_field_names"]
        described = provenance.describe(ROOT / "agent" / "config.yml", refresh=True)
        self.assertEqual(sorted(described), frozen)
        self.assertEqual(described["tools_exposed"], contract("agent-routes.json")["version_response"]["tools_exposed"])


class InteractionRouteTests(unittest.TestCase):
    """NAT's real interaction route with this package's store: the frozen statuses."""

    def test_route_statuses_match_the_frozen_matrix(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from nat.data_models.interactive import HumanPromptRadio
        from nat.front_ends.fastapi.routes.execution import add_execution_routes

        store = OwnerAwareExecutionStore(strict=False)
        app = FastAPI()
        asyncio.run(add_execution_routes(types.SimpleNamespace(_execution_store=store), app))
        app.add_middleware(ResponderIdentityMiddleware)
        http = TestClient(app, raise_server_exceptions=False)
        loop = asyncio.new_event_loop()

        def pending():
            record = loop.run_until_complete(store.create_execution())
            prompt = HumanPromptRadio(text="t", options=decision_options("research", note_available=True))
            interaction = loop.run_until_complete(store.set_interaction_required(record.execution_id, prompt))
            store.record_owner_for_test(record.execution_id, "researcher-1")
            return f"/executions/{record.execution_id}/interactions/{interaction.interaction_id}/response"

        def radio(value):
            return {"response": {"type": "radio", "selected_option": {"id": value, "label": value, "value": value}}}

        cases = [
            ("researcher-1", radio("research"), 204),
            ("intruder", radio("research"), 500),
            ("researcher-1", {"response": {"type": "radio", "selected_option": {"id": "research", "label": "x", "value": "shortlist"}}}, 500),
            ("researcher-1", {"response": {"type": "text", "text": "x"}}, 500),
        ]
        for user, body, status in cases:
            with self.subTest(user=user, body=body):
                self.assertEqual(http.post(pending(), json=body, headers={"x-authenticated-user-id": user}).status_code, status)
        url = pending()
        self.assertEqual(http.post(url, json=radio("research"), headers={"x-authenticated-user-id": "researcher-1"}).status_code, 204)
        self.assertEqual(http.post(url, json=radio("research"), headers={"x-authenticated-user-id": "researcher-1"}).status_code, 400)
        missing = "/executions/nope/interactions/nope/response"
        self.assertEqual(http.post(missing, json=radio("research"), headers={"x-authenticated-user-id": "researcher-1"}).status_code, 404)
        loop.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
