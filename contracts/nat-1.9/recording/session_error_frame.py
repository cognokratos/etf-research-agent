"""Write sse/session-error-frame.sse with NAT 1.9's own code.

http_interactive_runner._streaming_generator_impl frames an exception raised
while acquiring the session (before the workflow streams anything) as
`event: error\ndata: <Error JSON>`. No shipped scenario reaches that path, so
this frame is produced from the same model and format string rather than
recorded. Needs NAT 1.9 importable.
"""
import json
import pathlib
import sys

from nat.data_models.api_server import Error, ErrorTypes

out = pathlib.Path(sys.argv[1])
item = Error(code=ErrorTypes.WORKFLOW_ERROR, message="Configured identity header 'x-authenticated-user-id' is missing",
             details="IdentityHeaderError")
(out / "session-error-frame.sse").write_text(f"event: error\ndata: {item.model_dump_json()}\n\n")
(out / "session-error-frame.json").write_text(json.dumps({
    "scenario": "session-error-frame",
    "synthetic": True,
    "source": "nat/front_ends/fastapi/http_interactive_runner.py _streaming_generator_impl: yield f\"event: error\\ndata: {item.model_dump_json()}\\n\\n\"",
    "reachability": "Not reached by any recorded scenario; RequireIdentityHeaderMiddleware refuses the identity case with 401 before NAT runs. Kept because both consumers silently ignore this framing.",
}, indent=2) + "\n")
