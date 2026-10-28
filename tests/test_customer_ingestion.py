"""Customer intake's public MCP boundary and no-persistence contract."""

import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from mcp.client import Client
from mcp.server import MCPServer
from mcp.server.apps import Apps

from catalog_app.tools.customer_ingestion import (
    RESOURCE_URI,
    customer_ingestion,
    customer_ingestion_app,
)
from tests.fixtures.catalog import register_catalog_tool

ASSETS = Path(__file__).parents[1] / "src/catalog_app/tools/customer_ingestion"


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class CustomerIntakeBoundary(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = MCPServer("customer-intake-test", extensions=[Apps()])
        register_catalog_tool(self.server, "customer_ingestion")

    async def test_one_read_only_tool_and_no_inputs_or_handlers(self):
        async with Client(self.server) as client:
            tools = (await client.list_tools()).tools
            self.assertEqual([tool.name for tool in tools], ["customer_ingestion"])
            tool = tools[0]
            self.assertEqual(tool.input_schema.get("properties"), {})
            self.assertFalse(tool.input_schema["additionalProperties"])
            self.assertTrue(tool.annotations.read_only_hint)
            self.assertTrue(tool.annotations.idempotent_hint)
            self.assertFalse(tool.annotations.destructive_hint)
            self.assertEqual(tool.meta["ui"]["resourceUri"], RESOURCE_URI)

    async def test_open_has_only_static_instructions(self):
        async with Client(self.server) as client:
            result = await client.call_tool("customer_ingestion", {})
            self.assertFalse(result.is_error)
            self.assertEqual(result.structured_content, customer_ingestion())
            self.assertEqual(result.structured_content["persistence"], "none")
            self.assertEqual(result.structured_content["status"], "blank_form")
            self.assertIn("not saved", result.structured_content["instructions"])
            self.assertEqual(
                [field["id"] for field in result.structured_content["fields"]],
                ["first_name", "last_name", "age", "description"],
            )

    async def test_customer_data_cannot_be_accepted_as_tool_arguments(self):
        async with Client(self.server) as client:
            result = await client.call_tool(
                "customer_ingestion", {"first_name": "SYNTHETIC_TEST_ONLY"}
            )
            self.assertTrue(result.is_error)

    async def test_registered_resource_is_static_and_has_restricted_csp(self):
        async with Client(self.server) as client:
            resources = (await client.list_resources()).resources
            resource = next(item for item in resources if str(item.uri) == RESOURCE_URI)
            self.assertEqual(resource.mime_type, "text/html;profile=mcp-app")
            self.assertEqual(resource.meta["ui"]["csp"]["connectDomains"], [])
            self.assertEqual(
                resource.meta["ui"]["csp"]["resourceDomains"],
                ["https://cdn.jsdelivr.net"],
            )
            read_result = await client.read_resource(RESOURCE_URI)
            html = read_result.contents[0].text
            self.assertIn("<script type=\"module\">", html)
            self.assertNotIn("<!-- app.js -->", html)
            self.assertEqual(html, customer_ingestion_app())


class CustomerIntakePrivacy(unittest.TestCase):
    def test_open_does_not_need_environment_or_disk_access(self):
        with patch.dict("os.environ", {}, clear=True), patch(
            "builtins.open", side_effect=AssertionError("No data files allowed")
        ):
            first = customer_ingestion()
            first["fields"][0]["label"] = "Modified caller copy"
            second = customer_ingestion()
        self.assertEqual(second["fields"][0]["label"], "First name")
        self.assertNotIn("value", json.dumps(second))

    def test_native_form_cannot_transmit_named_values(self):
        elements = Elements((ASSETS / "app.html").read_text())
        forms = [attrs for tag, attrs in elements.tags if tag == "form"]
        self.assertEqual(len(forms), 1)
        self.assertEqual(forms[0].get("method"), "dialog")
        self.assertEqual(forms[0].get("autocomplete"), "off")
        self.assertNotIn("action", forms[0])
        fields = [attrs for tag, attrs in elements.tags if tag in ("input", "textarea")]
        self.assertEqual(len(fields), 4)
        for field in fields:
            self.assertNotIn("name", field)
            self.assertNotIn("value", field)
            self.assertEqual(field.get("autocomplete"), "off")
        ids = {field["id"] for field in fields}
        labels = {attrs.get("for") for tag, attrs in elements.tags if tag == "label"}
        self.assertTrue(ids <= labels)

    def test_no_persistence_or_data_transmission_apis(self):
        script = (ASSETS / "app.js").read_text()
        forbidden = (
            r"\b(?:localStorage|sessionStorage|indexedDB|caches)\b",
            r"\bdocument\s*\.\s*cookie\b",
            r"\b(?:fetch|XMLHttpRequest|WebSocket|EventSource)\s*\(",
            r"\b(?:callServerTool|updateModelContext|sendMessage|sendBeacon)\s*\(",
            r"\bconsole\s*\.\s*\w+\s*\(",
            r"\b(?:pushState|replaceState|createObjectURL)\s*\(",
            r"\.innerHTML\s*=",
        )
        for pattern in forbidden:
            self.assertIsNone(re.search(pattern, script), pattern)
        self.assertIn("preventDefault()", script)
        self.assertIn("pagehide", script)
        self.assertIn("pageshow", script)
        self.assertIn("applyDocumentTheme", script)
        self.assertIn("onhostcontextchanged", script)
        self.assertRegex(script, r'availableDisplayModes:\s*\["inline", "fullscreen"\]')

    def test_bad_html_template_is_rejected(self):
        with patch.object(Path, "read_text", side_effect=["<html></html>", ""]):
            with self.assertRaisesRegex(Exception, "interface is incomplete"):
                customer_ingestion_app()


if __name__ == "__main__":
    unittest.main()
