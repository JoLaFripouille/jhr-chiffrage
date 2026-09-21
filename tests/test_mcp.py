"""Real stdio client/server integration, using the official MCP Python SDK."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def payload(result):
    if result.isError:
        raise AssertionError("\n".join(getattr(c, "text", "") for c in result.content))
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(result.content[0].text)


class MCPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temporary.name) / "mcp.sqlite3")
        self.addCleanup(self.temporary.cleanup)

    def parameters(self, profile):
        environment = dict(os.environ)
        environment.update({
            "JHR_CHIFFRAGE_DB": self.database,
            "JHR_MCP_ACCESS": profile,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        })
        return StdioServerParameters(command=sys.executable,
                                     args=["-m", "jhr_chiffrage.mcp_server"], env=environment)

    async def test_draft_roundtrip_revision_idempotence_and_read_permissions(self):
        async with stdio_client(self.parameters("draft")) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                self.assertEqual(initialized.serverInfo.name, "JHR Chiffrage")
                listing = await session.list_tools()
                names = {tool.name for tool in listing.tools}
                self.assertIn("create_estimate", names)
                self.assertNotIn("freeze_estimate", names)
                self.assertNotIn("save_settings", names)
                self.assertNotIn("refresh_estimate_settings", names)
                schema = next(t.inputSchema for t in listing.tools if t.name == "save_estimate")
                self.assertIn("expected_revision", schema["required"])
                self.assertIn("operation_id", schema["required"])
                self.assertNotIn("actor", schema["properties"])
                self.assertIn("parent_id", schema["$defs"]["ItemData"]["properties"])
                capabilities = payload(await session.call_tool("get_capabilities"))
                self.assertEqual(capabilities["profile"], "draft")
                creation = {"name": "Essai MCP", "client": "Client test", "operation_id": str(uuid4())}
                estimate = payload(await session.call_tool("create_estimate", creation))
                replay = payload(await session.call_tool("create_estimate", creation))
                self.assertEqual(estimate, replay)
                reread = payload(await session.call_tool("get_estimate", {"estimate_id": estimate["id"]}))
                self.assertEqual(reread, estimate)
                estimate["name"] = "Essai modifié"
                saved = payload(await session.call_tool("save_estimate", {
                    "data": estimate, "expected_revision": estimate["revision"], "operation_id": str(uuid4()),
                }))
                self.assertEqual(saved["name"], "Essai modifié")
                self.assertGreater(saved["revision"], estimate["revision"])
                stale = await session.call_tool("save_estimate", {
                    "data": estimate, "expected_revision": estimate["revision"], "operation_id": str(uuid4()),
                })
                self.assertTrue(stale.isError)
                self.assertIn("REVISION_CONFLICT", stale.content[0].text)
                simulation = await session.call_tool("simulate_estimate", {"data": saved})
                self.assertFalse(simulation.isError)
                validation = payload(await session.call_tool("validate_estimate", {"estimate_id": saved["id"]}))
                self.assertFalse(validation["valid"])
                saved["works"] = [{"id": str(uuid4()), "name": "Plans EXE", "items": []}]
                saved = payload(await session.call_tool("save_estimate", {
                    "data": saved, "expected_revision": saved["revision"], "operation_id": str(uuid4()),
                }))
                template = payload(await session.call_tool("save_template", {
                    "data": {"name": "Forfait plan", "items": [{
                        "id": str(uuid4()), "label": "Dessin", "mode": "fixed", "price": "125.00", "quantity": "1",
                    }]},
                    "expected_revision": None, "operation_id": str(uuid4()),
                }))
                applied = payload(await session.call_tool("apply_template", {
                    "estimate_id": saved["id"], "template_id": template["id"],
                    "work_id": saved["works"][0]["id"], "expected_revision": saved["revision"],
                    "operation_id": str(uuid4()),
                }))
                self.assertEqual(applied["works"][0]["items"][0]["label"], "Dessin")
                self.assertNotEqual(applied["works"][0]["items"][0]["id"], template["items"][0]["id"])
                missing_revision = await session.call_tool("save_template", {
                    "data": template, "expected_revision": None, "operation_id": str(uuid4()),
                })
                self.assertTrue(missing_revision.isError)
                changes = await session.call_tool("list_changes")
                self.assertFalse(changes.isError)
                self.assertIn('"mcp"', " ".join(c.text for c in changes.content if hasattr(c, "text")))
        async with stdio_client(self.parameters("read")) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                for prohibited in ("create_estimate", "save_estimate", "apply_template", "save_template", "revise_estimate", "freeze_estimate", "save_settings", "refresh_estimate_settings"):
                    self.assertNotIn(prohibited, names)
                fetched = payload(await session.call_tool("get_estimate", {"estimate_id": estimate["id"]}))
                self.assertEqual(fetched["name"], "Essai modifié")
                denied = await session.call_tool("create_estimate", creation)
                self.assertTrue(denied.isError)

    async def test_full_profile_advertises_controlled_operations(self):
        async with stdio_client(self.parameters("full")) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {tool.name for tool in (await session.list_tools()).tools}
                self.assertIn("freeze_estimate", names)
                self.assertIn("save_settings", names)
                self.assertIn("refresh_estimate_settings", names)
                result = payload(await session.call_tool("get_capabilities"))
                self.assertTrue(result["can_freeze"])
                settings = payload(await session.call_tool("get_settings"))
                settings.update(hourly_rate="50", levy_rate="0", vat_rate="0")
                payload(await session.call_tool("save_settings", {
                    "data": settings, "expected_revision": settings["revision"], "operation_id": str(uuid4()),
                }))
                estimate = payload(await session.call_tool("create_estimate", {
                    "name": "Export test", "operation_id": str(uuid4()),
                }))
                estimate = payload(await session.call_tool("refresh_estimate_settings", {
                    "estimate_id": estimate["id"], "expected_revision": estimate["revision"], "operation_id": str(uuid4()),
                }))
                estimate["works"] = [{"id": str(uuid4()), "name": "Plan", "items": [{
                    "id": str(uuid4()), "label": "Dessin", "mode": "fixed", "quantity": "1", "price": "125",
                }]}]
                estimate = payload(await session.call_tool("save_estimate", {
                    "data": estimate, "expected_revision": estimate["revision"], "operation_id": str(uuid4()),
                }))
                frozen = payload(await session.call_tool("freeze_estimate", {
                    "estimate_id": estimate["id"], "expected_revision": estimate["revision"], "operation_id": str(uuid4()),
                }))
                self.assertEqual(frozen["status"], "frozen")
                commercial = payload(await session.call_tool("export_estimate", {"estimate_id": frozen["id"]}))
                self.assertEqual(commercial["totals"]["ht_cents"], 12500)
                self.assertNotIn("levy_cents", commercial["totals"])
                self.assertNotIn("settings", commercial)
                internal = payload(await session.call_tool("export_estimate", {"estimate_id": frozen["id"], "kind": "internal"}))
                self.assertIn("levy_cents", internal["totals"])
                self.assertFalse((Path(self.database).parent / "exports").exists())
                revised = payload(await session.call_tool("revise_estimate", {
                    "estimate_id": frozen["id"], "expected_revision": frozen["revision"], "operation_id": str(uuid4()),
                }))
                self.assertEqual(revised["status"], "draft")
                self.assertEqual(revised["parent_id"], frozen["id"])

    async def test_minutes_roundtrip_idempotence_and_strict_validation(self):
        async with stdio_client(self.parameters("full")) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                settings = payload(await session.call_tool("get_settings"))
                settings.update(hourly_rate="80", levy_rate="0", vat_rate="0")
                payload(await session.call_tool("save_settings", {
                    "data": settings, "expected_revision": settings["revision"], "operation_id": str(uuid4()),
                }))
                estimate = payload(await session.call_tool("create_estimate", {
                    "name": "Minutes exactes", "operation_id": str(uuid4()),
                }))
                estimate["works"] = [{"id": str(uuid4()), "name": "Plan", "items": [
                    {"id": str(uuid4()), "label": "Dessin", "mode": "hourly", "quantity": "3", "duration_minutes": 1},
                    {"id": str(uuid4()), "label": "Forfait", "mode": "fixed", "quantity": "3", "price": "100", "estimated_minutes": 90},
                ]}]
                estimate["works"][0]["items"][1]["parent_id"] = estimate["works"][0]["items"][0]["id"]
                args = {"data": estimate, "expected_revision": estimate["revision"], "operation_id": str(uuid4())}
                saved = payload(await session.call_tool("save_estimate", args))
                self.assertEqual(saved, payload(await session.call_tool("save_estimate", args)))
                reread = payload(await session.call_tool("get_estimate", {"estimate_id": saved["id"]}))
                self.assertEqual(saved, reread)
                self.assertEqual(reread["works"][0]["items"][0]["duration_minutes"], 1)
                self.assertEqual(reread["works"][0]["items"][1]["estimated_minutes"], 90)
                self.assertEqual(reread["works"][0]["items"][1]["parent_id"], reread["works"][0]["items"][0]["id"])
                result = payload(await session.call_tool("calculate_estimate", {"estimate_id": saved["id"]}))
                self.assertEqual(result["ht_cents"], 30400)
                for index, key in [(0, "duration_minutes"), (1, "estimated_minutes")]:
                    for bad in [True, "90", 1.5, -1, 60_000_000_001]:
                        saved["works"][0]["items"][index][key] = bad
                        invalid = await session.call_tool("simulate_estimate", {"data": saved})
                        self.assertTrue(invalid.isError, (key, bad))
                    saved["works"][0]["items"][index][key] = [1, 90][index]
                saved["works"][0]["items"][0]["parent_id"] = saved["works"][0]["items"][1]["id"]
                invalid = await session.call_tool("save_estimate", {
                    "data": saved, "expected_revision": saved["revision"], "operation_id": str(uuid4()),
                })
                self.assertTrue(invalid.isError)
                self.assertIn("cycle", invalid.content[0].text)
                self.assertEqual(reread, payload(await session.call_tool("get_estimate", {"estimate_id": saved["id"]})))


if __name__ == "__main__":
    unittest.main()
