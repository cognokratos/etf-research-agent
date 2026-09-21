#!/usr/bin/env python3
"""The shipped Compose configuration must configure the approval boundary fully.

This replaces the template's `verify_read_only_default.py`, which asserted the
opposite posture. That check is right for a template whose sample application
ships read-only and treats human approval as an opt-in demonstration. It is
wrong here: recording an approved research decision *is* this application, so a
deployment with no approval surface is not a safer variant of it, it is a broken
one — the MCP server refuses to start without the secret, and the three
state-changing functions in `agent/config.yml` are registered unconditionally.

What is worth checking is therefore the inverse, and specifically the *partial*
configurations, because those are the ones that look like they work:

* a secret on the agent but not the MCP server (or the reverse) — the agent
  mints tokens the verifier cannot check, so every approval fails after the
  human has already decided;
* two *different* secrets — same symptom, harder to see;
* a secret shorter than the 24 characters both sides independently require —
  fails at boot on the MCP and on first mint in the agent;
* interaction endpoints disabled while the approval functions are registered —
  the workflow pauses for a human who can never answer.

Reads the resolved Compose configuration as JSON on stdin — e.g.::

    docker compose config --format json | python3 scripts/verify_approval_surface.py

Deliberately a file, not a ``python3 - <<'PY'`` heredoc: a heredoc redirects
stdin to the script text itself, which starves ``json.load(sys.stdin)`` of the
piped configuration and fails with ``JSONDecodeError`` before this check ever
runs. Passing the script as a file leaves stdin free for the pipe.
"""

from __future__ import annotations

import json
import sys

#: Both sides enforce this independently — `approval_secret()` in
#: `agent/src/nat_streaming_react/approval.py` and `MIN_SECRET_LENGTH` in
#: `mcp-server/src/approval.rs`. Checked here so a misconfiguration is caught
#: before a deployment rather than at boot.
MIN_SECRET_LENGTH = 24

TRUTHY = {"true", "1", "yes", "on"}


def main() -> None:
    config = json.load(sys.stdin)
    services = config["services"]

    secrets: dict[str, str] = {}
    for name in ("agent", "mcp-server"):
        secret = services[name]["environment"].get("HITL_APPROVAL_SECRET") or ""
        if not secret.strip():
            sys.exit(
                f"::error::{name} ships without HITL_APPROVAL_SECRET; the approval "
                "boundary is this application's purpose, not an opt-in extra"
            )
        if len(secret) < MIN_SECRET_LENGTH:
            sys.exit(
                f"::error::{name} HITL_APPROVAL_SECRET is {len(secret)} characters; "
                f"both sides require at least {MIN_SECRET_LENGTH}"
            )
        secrets[name] = secret

    if secrets["agent"] != secrets["mcp-server"]:
        sys.exit(
            "::error::the agent and the MCP server ship different "
            "HITL_APPROVAL_SECRET values; every approval would be minted with one "
            "key and verified with another, so it would fail after the human has "
            "already decided"
        )

    interactive = services["agent"]["environment"].get("HITL_ENABLE_INTERACTIVE") or ""
    if interactive.strip().lower() not in TRUTHY:
        sys.exit(
            "::error::HITL_ENABLE_INTERACTIVE is not enabled, so NAT mounts no "
            "interaction endpoints; the approval-gated functions would pause for a "
            "human who has no way to answer"
        )

    print(
        "The shipped configuration wires the approval boundary consistently: "
        "one shared secret of adequate length on both sides, and NAT interaction "
        "endpoints mounted."
    )


if __name__ == "__main__":
    main()
