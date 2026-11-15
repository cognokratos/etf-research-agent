#!/usr/bin/env python3
"""Tests for verify_nat_freeze.py: it passes on this tree and fails when it should."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify_nat_freeze as freeze  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover - host without PyYAML
    yaml = None


def manifest() -> dict:
    return json.loads(freeze.MANIFEST.read_text(encoding="utf-8"))


class FreezeHoldsTests(unittest.TestCase):
    def test_every_check_passes_on_this_tree(self):
        current = manifest()
        self.assertEqual(freeze.check_digests(current, update=False), [])
        self.assertEqual(freeze.check_prompts(current), [])
        self.assertEqual(freeze.check_domain(current), [])
        self.assertEqual(freeze.check_suites(current), [])

    def test_the_frozen_prompt_copies_are_what_config_yml_carries(self):
        for name, text in freeze.prompt_texts().items():
            copy_path = freeze.ROOT / manifest()["prompts"][name]["frozen_copy"]
            self.assertEqual(copy_path.read_text(encoding="utf-8"), text, name)

    @unittest.skipUnless(yaml, "PyYAML is not installed on this host")
    def test_the_narrow_extractor_agrees_with_a_yaml_parser(self):
        config = yaml.safe_load((freeze.ROOT / "agent" / "config.yml").read_text())
        texts = freeze.prompt_texts()
        self.assertEqual(texts["system_prompt"], config["workflow"]["system_prompt"])
        rail = config["middleware"]["workflow_guardrails"]["guardrails"]["prompts"][0]
        self.assertEqual(rail["task"], "self_check_input")
        self.assertEqual(texts["self_check_input"], rail["content"])

    def test_the_byte_for_byte_data_digests_agree_with_the_baseline_s_own(self):
        digests = {Path(e["path"]).name: e["sha256"] for e in manifest()["byte_for_byte"] if e["path"].startswith("data/")}
        self.assertEqual(digests, manifest()["domain_invariants"]["baseline_fixture_sha256"])

    def test_every_contract_record_is_covered(self):
        listed = {e["path"] for e in manifest()["contract_records"]}
        root = freeze.ROOT / "contracts" / "nat-1.9"
        on_disk = {
            str(p.relative_to(freeze.ROOT)) for p in root.rglob("*")
            if p.is_file() and p.name not in ("manifest.json", "README.md") and "recording" not in p.parts
        }
        self.assertEqual(listed, on_disk)


class FreezeDetectsDriftTests(unittest.TestCase):
    def test_a_changed_byte_for_byte_file_is_reported(self):
        tampered = copy.deepcopy(manifest())
        tampered["byte_for_byte"][0]["sha256"] = "0" * 64
        failures = freeze.check_digests(tampered, update=False)
        self.assertEqual(len(failures), 1)
        self.assertIn(tampered["byte_for_byte"][0]["path"], failures[0])

    def test_a_changed_prompt_is_reported(self):
        tampered = copy.deepcopy(manifest())
        tampered["prompts"]["system_prompt"]["sha256"] = hashlib.sha256(b"other").hexdigest()
        self.assertTrue(freeze.check_prompts(tampered))

    def test_a_changed_domain_invariant_is_reported(self):
        for key, value in (("listings", 30), ("funds_by_isin", 31), ("baseline_accuracy", 0.95)):
            tampered = copy.deepcopy(manifest())
            tampered["domain_invariants"][key] = value
            with self.subTest(key=key):
                self.assertTrue(freeze.check_domain(tampered))
        tampered = copy.deepcopy(manifest())
        tampered["domain_invariants"]["_spot_checks"]["VTI-ARCA"] = "shortlist"
        self.assertTrue(freeze.check_domain(tampered))

    def test_a_changed_gate_is_reported(self):
        tampered = copy.deepcopy(manifest())
        tampered["evaluation"]["suites"]["policy"]["gated_metric"] = "decision_relationship_correct/mean"
        self.assertTrue(freeze.check_suites(tampered))
        tampered = copy.deepcopy(manifest())
        tampered["evaluation"]["fail_threshold"] = 0.9
        self.assertTrue(freeze.check_suites(tampered))

    def test_the_extractor_reads_a_literal_block_exactly(self):
        text = "a:\n  key: |\n    line one\n      indented\n\n    line two\n\n  other: 1\n"
        self.assertEqual(freeze.literal_block(text, "key"), "line one\n  indented\n\nline two\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
