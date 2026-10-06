# Third-party notices and licence exceptions

The [`LICENSE`](LICENSE) file (MIT, Copyright (c) 2026 Victor Nitu) covers the
original code and documentation in this repository. The material below is not
covered by it, or not only by it. Its own terms continue to apply.

The authoritative copies of this file, `LICENSE` and `LICENSES/Apache-2.0.txt`
are at the repository root. Byte-identical copies in `agent/` are packaged with
the `nat-streaming-react` distribution (`project.license-files`), and
`scripts/verify_agent_package_licenses.py` checks that they match. Paths below
are relative to the repository root.

## Apache-2.0: files derived from, or closely following, NVIDIA NeMo Agent Toolkit

| File | Notice in the file | Status |
| --- | --- | --- |
| `agent/src/nat_streaming_react/register.py` | `SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES.` | modified from NAT's `react_agent` workflow registration (`nat/plugins/langchain/agent/react_agent/register.py`); carries a modification notice |
| `agent/src/nat_streaming_react/text_guardrails.py` | `SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.` | modified from NAT's NeMo Guardrails middleware (`nat/plugins/security/middleware/guardrails/`); carries a modification notice |
| `agent/src/nat_streaming_react/observability/otlp_exporter.py` | `SPDX-License-Identifier: Apache-2.0` only (**no NVIDIA copyright line**) | Apache-2.0 retained; exact provenance not recorded (below) |

These files are distributed under the Apache License, Version 2.0, whose full
text is in [`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt). Keep their
headers and this file with any copy or excerpt.

### Provenance of `observability/otlp_exporter.py`

What was verified:

- The file arrived with the template this repository is built from
  (`a52fbe0`, 2026-09-20, shared history with `simple-agent-template`), when the
  agent pinned `nvidia-nat[langchain,opentelemetry]==1.8.0`. It was revised for NAT
  1.9 in `5934dfb`. Git history records no upstream source.
- Its configuration class and factory follow `OtelCollectorTelemetryExporter` and
  `otel_telemetry_exporter` in NVIDIA NeMo Agent Toolkit,
  `packages/nvidia_nat_opentelemetry/src/nat/plugins/opentelemetry/register.py`.
  The shared parts are: the `BatchConfigMixin` / `CollectorConfigMixin` /
  `TelemetryExporterBaseConfig` bases, a `resource_attributes` field, the
  default resource-attribute keys, and the `OTLPSpanAdapterExporter(...)` call with
  the same keyword arguments.
- That upstream function is identical at tags `v1.8.0` and `v1.9.0`. This was
  checked against the published `nvidia-nat-opentelemetry` 1.8.0 and 1.9.0 wheels
  and the GitHub tag contents. The upstream file carries
  `SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.`
  and `SPDX-License-Identifier: Apache-2.0`.
- The rest of the file has no upstream counterpart: the processor insertion, the
  span-prefix handling and the documentation.

What remains unknown: whether those parts were copied or adapted from that
upstream file, or written independently against the same plugin API. Similarity
alone does not establish it. The file therefore keeps its Apache-2.0 declaration,
and no NVIDIA copyright line has been added on the strength of resemblance. If
the origin is established, add the matching notice and a modification notice.

## Lineage

This repository is derived from `simple-agent-template`
(https://github.com/cognokratos/simple-agent-template; see `docs/UPSTREAM.md`),
which is MIT licensed by the same author with the same Apache-2.0 exceptions.

## Dependencies

Dependencies installed at build or run time (NeMo Agent Toolkit, NeMo
Guardrails, Presidio, Keycloak, PostgreSQL, MLflow, the OpenTelemetry Collector,
the rules-engine crates, assistant-ui and the Rust and npm crates and packages in the lock files) are not
part of this repository. Each is used under its own licence.
