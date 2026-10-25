"""Talk to the real server process over stdio, the way OpenClaw does."""
import json
import sys
from pathlib import Path

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

EX = Path(__file__).resolve().parents[1] / "examples"


def _text(res):
    return "".join(getattr(c, "text", "") for c in res.content)


def test_stdio_roundtrip(tmp_path):
    async def main():
        params = StdioServerParameters(command=sys.executable,
                                       args=["-m", "engineer_tools", "--runs-dir", str(tmp_path)])
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = (await s.list_tools()).tools
                names = {t.name for t in tools}
                assert {"start_run", "submit_plan", "get_work_order", "hand_calc"} <= names
                assert all("$ref" not in json.dumps(t.inputSchema if hasattr(t, "inputSchema") else t.input_schema)
                           for t in tools)
                res = await s.call_tool("start_run", {
                    "brief": json.loads((EX / "brief_shelf_bracket.json").read_text()),
                    "sketch": json.loads((EX / "sketch_shelf_bracket.json").read_text())})
                body = json.loads(_text(res))
                assert body["phase"] == "review"
                bad = await s.call_tool("submit_plan", {
                    "run_id": body["run_id"], "plan": json.loads((EX / "plan_shelf_bracket.json").read_text())})
                assert getattr(bad, "is_error", getattr(bad, "isError", False))
                assert "Not allowed in phase 'review'" in _text(bad)
    anyio.run(main)
