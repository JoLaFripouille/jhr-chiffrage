"""Exercise a packaged MCP executable against disposable data only."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def payload(result):
    if result.isError:
        raise RuntimeError("; ".join(getattr(c, "text", "") for c in result.content))
    return result.structuredContent or json.loads(result.content[0].text)


async def run(executable):
    with tempfile.TemporaryDirectory(prefix="jhr-mcp-exe-") as temporary, tempfile.TemporaryFile(mode="w+", encoding="utf-8") as error_log:
        environment = dict(os.environ)
        environment.update(JHR_CHIFFRAGE_DB=str(Path(temporary) / "smoke.sqlite3"), JHR_MCP_ACCESS="draft")
        parameters = StdioServerParameters(command=str(executable), env=environment)
        async with stdio_client(parameters, errlog=error_log) as (read, write):
            async with ClientSession(read, write) as session:
                hello = await session.initialize()
                assert hello.serverInfo.name == "JHR Chiffrage"
                tools = {tool.name for tool in (await session.list_tools()).tools}
                assert "create_estimate" in tools and "freeze_estimate" not in tools
                capabilities = payload(await session.call_tool("get_capabilities"))
                created = payload(await session.call_tool("create_estimate", {"name": "EXE smoke", "operation_id": str(uuid4())}))
                fetched = payload(await session.call_tool("get_estimate", {"estimate_id": created["id"]}))
                assert fetched == created
                created["name"] = "EXE smoke saved"
                saved = payload(await session.call_tool("save_estimate", {"data": created, "expected_revision": created["revision"], "operation_id": str(uuid4())}))
                assert saved["name"] == "EXE smoke saved"
                assert saved["revision"] > created["revision"]
        environment["JHR_MCP_ACCESS"] = "read"
        async with stdio_client(StdioServerParameters(command=str(executable), env=environment), errlog=error_log) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                assert "save_estimate" not in names
                denied = await session.call_tool("create_estimate", {"name": "denied", "operation_id": str(uuid4())})
                assert denied.isError
        error_log.seek(0)
        diagnostics = error_log.read()
        assert "Traceback" not in diagnostics, diagnostics
        print(json.dumps({"result": "PASS", "executable": str(executable), "sdk": capabilities["sdk_version"], "checks": ["initialize", "list_tools", "create", "read", "save", "read profile denies writes", "shutdown without traceback"], "database": "temporary, removed"}, ensure_ascii=False))


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    executable = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / "dist/v0.1.0/JHRChiffrageMCP/JHRChiffrageMCP.exe"
    asyncio.run(run(executable))
