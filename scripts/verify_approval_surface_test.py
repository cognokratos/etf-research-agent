#!/usr/bin/env python3
"""Behavioral tests for the shipped-configuration approval-surface check.

These run the actual shell invocation CI uses — a subprocess piping JSON into
``python3 scripts/verify_approval_surface.py`` — rather than calling a Python
helper in isolation, so a regression to the ``python3 - <<'PY'`` stdin conflict
that invocation style avoids would be caught here.

Every negative case below is a *partial* configuration. Those are the ones worth
testing: a completely unconfigured deployment fails loudly at boot, but a
half-configured one starts, serves reads, and then fails after a human has
already made a decision.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_approval_surface.py"

GOOD_SECRET = "a-shared-approval-secret-of-32-chars"


def _services(**overrides) -> dict:
    agent_env = {
        "HITL_APPROVAL_SECRET": GOOD_SECRET,
        "HITL_ENABLE_INTERACTIVE": "true",
    }
    mcp_env = {"HITL_APPROVAL_SECRET": GOOD_SECRET}
    agent_env.update(overrides.get("agent", {}))
    mcp_env.update(overrides.get("mcp-server", {}))
    return {
        "services": {
            "agent": {"environment": agent_env},
            "mcp-server": {"environment": mcp_env},
        }
    }


def _run(payload: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )


def test_fully_configured_approval_surface_passes() -> None:
    result = _run(json.dumps(_services()))
    assert result.returncode == 0, result.stderr
    assert "wires the approval boundary consistently" in result.stdout
    print("PASS: one shared secret on both sides with interaction endpoints mounted passes")


def test_a_missing_agent_secret_fails() -> None:
    result = _run(json.dumps(_services(agent={"HITL_APPROVAL_SECRET": ""})))
    assert result.returncode != 0
    assert "HITL_APPROVAL_SECRET" in (result.stdout + result.stderr)
    print("PASS: an agent with no approval secret fails the check")


def test_a_missing_mcp_secret_fails() -> None:
    result = _run(json.dumps(_services(**{"mcp-server": {"HITL_APPROVAL_SECRET": ""}})))
    assert result.returncode != 0
    print("PASS: an MCP server with no approval secret fails the check")


def test_mismatched_secrets_fail() -> None:
    """The case that is invisible until a human has already decided."""

    result = _run(
        json.dumps(_services(**{"mcp-server": {"HITL_APPROVAL_SECRET": GOOD_SECRET + "-different"}}))
    )
    assert result.returncode != 0
    assert "different" in (result.stdout + result.stderr)
    print("PASS: two different secrets fail the check")


def test_a_short_secret_fails() -> None:
    result = _run(
        json.dumps(
            _services(
                agent={"HITL_APPROVAL_SECRET": "too-short"},
                **{"mcp-server": {"HITL_APPROVAL_SECRET": "too-short"}},
            )
        )
    )
    assert result.returncode != 0
    assert "at least 24" in (result.stdout + result.stderr)
    print("PASS: a secret below the length both sides require fails the check")


def test_disabled_interaction_endpoints_fail() -> None:
    for value in ("false", "0", "no", "off", ""):
        result = _run(json.dumps(_services(agent={"HITL_ENABLE_INTERACTIVE": value})))
        assert result.returncode != 0, value
        assert "interaction endpoints" in (result.stdout + result.stderr), value
    print("PASS: disabled interaction endpoints fail the check")


def test_empty_input_fails_clearly() -> None:
    result = _run("")
    assert result.returncode != 0
    print("PASS: empty input fails rather than silently passing")


def test_malformed_input_fails_clearly() -> None:
    result = _run("{not json")
    assert result.returncode != 0
    print("PASS: malformed input fails rather than silently passing")


def test_full_compose_pipeline_passes_on_the_shipped_default() -> None:
    """The exact command CI runs, against this repository's real compose files."""

    if shutil.which("docker") is None:
        print("SKIP: docker CLI not available; full Compose pipeline not exercised")
        return

    env_path = ROOT / ".env"
    created = not env_path.exists()
    if created:
        shutil.copyfile(ROOT / ".env.example", env_path)

    try:
        render = subprocess.run(
            ["docker", "compose", "config", "--format", "json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if render.returncode != 0:
            print(
                f"SKIP: `docker compose config` could not run here: {render.stderr.strip()[:200]}"
            )
            return

        result = _run(render.stdout)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "wires the approval boundary consistently" in result.stdout
        print(
            "PASS: `docker compose config --format json | python3 "
            "scripts/verify_approval_surface.py` passes on the shipped default"
        )
    finally:
        if created:
            env_path.unlink(missing_ok=True)


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
