#!/usr/bin/env python3
"""Verify the NAT 1.9 freeze manifest against the working tree.

`contracts/nat-1.9/manifest.json` names what the agent-runtime migration must
preserve. This checks the parts a machine can check without any dependency:

* every file classified byte-for-byte still has its recorded SHA-256 — the ETF
  snapshot, profile, rules, labelled cases, deterministic baseline, evaluation
  datasets and the historical NAT results;
* every frozen contract record under contracts/nat-1.9/ is unchanged;
* the system prompt and the input-rail prompt still digest to the values the
  NAT agent reported over /version (read from agent/config.yml while it exists,
  otherwise from the frozen copies the manifest names);
* the domain invariants: 31 listings, 30 funds, 20 labelled cases, the baseline's
  decision distribution and 20/20 accuracy;
* the five evaluation suites, each gated on the recorded metric at 1.0.

    python3 scripts/verify_nat_freeze.py                  # check
    python3 scripts/verify_nat_freeze.py --update-digests # after an *intended* change

Exits non-zero on the first category with a failure, listing every failure in it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "contracts" / "nat-1.9" / "manifest.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def literal_block(text: str, key: str, *, after: str | None = None) -> str:
    """The value of a YAML literal block scalar (`key: |`), clip chomping.

    Deliberately narrow: enough for the two prompts in agent/config.yml, which
    are both `|` blocks. `after` anchors the search past an earlier line, so the
    right `content: |` is found. Cross-checked against PyYAML by the test.
    """

    lines = text.splitlines()
    start = 0
    if after is not None:
        start = next(i for i, line in enumerate(lines) if line.strip() == after) + 1
    pattern = re.compile(rf"^(\s*){re.escape(key)}:\s*\|\s*$")
    for index in range(start, len(lines)):
        match = pattern.match(lines[index])
        if not match:
            continue
        indent = len(match.group(1))
        body: list[str] = []
        block_indent: int | None = None
        for line in lines[index + 1:]:
            if line.strip() == "":
                body.append("")
                continue
            current = len(line) - len(line.lstrip(" "))
            if current <= indent:
                break
            if block_indent is None:
                block_indent = current
            body.append(line[block_indent:])
        while body and body[-1] == "":
            body.pop()
        return "\n".join(body) + "\n"
    raise KeyError(f"no literal block {key!r}")


def prompt_texts() -> dict[str, str]:
    config = ROOT / "agent" / "config.yml"
    if not config.is_file():
        return {}
    text = config.read_text(encoding="utf-8")
    return {
        "system_prompt": literal_block(text, "system_prompt"),
        "self_check_input": literal_block(text, "content", after="- task: self_check_input"),
    }


def check_digests(manifest: dict, update: bool) -> list[str]:
    failures = []
    for section in ("byte_for_byte", "contract_records"):
        for entry in manifest[section]:
            path = ROOT / entry["path"]
            if not path.is_file():
                failures.append(f"{entry['path']}: missing")
                continue
            actual = sha256_file(path)
            if update:
                entry["sha256"] = actual
            elif actual != entry["sha256"]:
                failures.append(f"{entry['path']}: sha256 {actual} != frozen {entry['sha256']}")
    return failures


def check_prompts(manifest: dict) -> list[str]:
    failures = []
    texts = prompt_texts()
    for name, spec in manifest["prompts"].items():
        if name not in texts:
            continue
        actual = hashlib.sha256(texts[name].encode("utf-8")).hexdigest()
        if actual != spec["sha256"]:
            failures.append(f"prompt {name}: sha256 {actual} != frozen {spec['sha256']}")
    if not texts:
        failures.append("agent/config.yml is gone: name the prompts' new location in the manifest before removing it")
    return failures


def check_domain(manifest: dict) -> list[str]:
    invariants = manifest["domain_invariants"]
    failures = []
    etfs = json.loads((ROOT / "data" / "etfs.json").read_text())
    cases = json.loads((ROOT / "data" / "test_cases.json").read_text())
    cases = cases if isinstance(cases, list) else cases.get("test_cases", cases.get("cases", []))
    baseline = json.loads((ROOT / "evaluation" / "results" / "deterministic-etf-baseline.json").read_text())
    observed = {
        "listings": len(etfs),
        "funds_by_isin": len({etf["isin"] for etf in etfs}),
        "labelled_cases": len(cases),
        "baseline_decision_distribution": baseline["decision_distribution"],
        "baseline_accuracy": baseline["accuracy"],
        "baseline_test_cases_passed": baseline["test_cases_passed"],
        "rules_version": baseline["rules_version"],
        "investor_profile_version": baseline["investor_profile_version"],
        "baseline_fixture_sha256": baseline["fixture_sha256"],
        "decisions": ["reject", "research", "shortlist"],
    }
    for key, expected in invariants.items():
        if key.startswith("_"):
            continue
        if observed.get(key) != expected:
            failures.append(f"domain {key}: {observed.get(key)!r} != frozen {expected!r}")
    for etf_id, decision in invariants.get("_spot_checks", {}).items():
        actual = next((e["decision"] for e in baseline["evaluations"] if e["etf_id"] == etf_id), None)
        if actual != decision:
            failures.append(f"baseline {etf_id}: {actual} != frozen {decision}")
    return failures


def check_suites(manifest: dict) -> list[str]:
    sys.path.insert(0, str(ROOT))
    from evaluation.config import SUITES  # stdlib-only module

    failures = []
    frozen = manifest["evaluation"]["suites"]
    if set(SUITES) != set(frozen):
        failures.append(f"suites {sorted(SUITES)} != frozen {sorted(frozen)}")
    for key, spec in frozen.items():
        config = SUITES.get(key)
        if config is None:
            continue
        if config.required_metric != spec["gated_metric"]:
            failures.append(f"suite {key}: gated metric {config.required_metric} != {spec['gated_metric']}")
        dataset = json.loads(config.dataset_path.read_text())
        if len(dataset) != spec["cases"]:
            failures.append(f"suite {key}: {len(dataset)} cases != frozen {spec['cases']}")
    makefile = (ROOT / "Makefile").read_text()
    main = (ROOT / "evaluation" / "__main__.py").read_text()
    threshold = manifest["evaluation"]["fail_threshold"]
    if f"FAIL_THRESHOLD ?= {threshold}" not in makefile:
        failures.append("Makefile FAIL_THRESHOLD default changed")
    if f'"--fail-threshold", type=float, default={threshold}' not in main:
        failures.append("evaluation/__main__.py --fail-threshold default changed")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--update-digests", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    status = 0
    for name, check in (
        ("digests", lambda m: check_digests(m, args.update_digests)),
        ("prompts", check_prompts),
        ("domain invariants", check_domain),
        ("evaluation suites", check_suites),
    ):
        failures = check(manifest)
        if failures:
            status = 1
            print(f"FAIL {name}:")
            for failure in failures:
                print(f"  - {failure}")
        else:
            print(f"ok   {name}")
    if args.update_digests:
        MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"updated digests in {MANIFEST.relative_to(ROOT)}; review the diff")
    return status


if __name__ == "__main__":
    sys.exit(main())
