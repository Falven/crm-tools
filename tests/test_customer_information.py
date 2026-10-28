"""Customer information's stateless tool and self-contained, memory-only App."""

from copy import deepcopy
from html.parser import HTMLParser
import inspect
from pathlib import Path
import re
import unittest

from mcp.client import Client
from mcp.server import MCPServer
from mcp.server.apps import Apps

from catalog_app.tools import customer_information as customer_information_module
from catalog_app.tools.customer_information import (
    RESOURCE_URI,
    customer_information,
    customer_information_app,
    register,
)


EXPECTED_FIELDS = {
    "full_name": "",
    "company": "",
    "email": "",
    "phone": "",
    "address": "",
    "notes": "",
}
EXPECTED_RESOURCE_URI = "ui://customer-information/app.html"
APP_MIME_TYPE = "text/html;profile=mcp-app"


class AppHTML(HTMLParser):
    """Inspect executable scripts and asset references without a browser."""

    def __init__(self):
        super().__init__()
        self.html_count = 0
        self.scripts = []
        self.styles = []
        self.external_assets = []
        self._script = None
        self._style = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "html":
            self.html_count += 1
        if tag == "script":
            self._script = []
            self.scripts.append(self._script)
        if tag == "style":
            self._style = []
            self.styles.append(self._style)
        if attrs.get("style"):
            self.styles.append([attrs["style"]])
        asset_attributes = {
            "script": ("src",),
            "link": ("href",),
            "img": ("src", "srcset"),
            "iframe": ("src",),
            "embed": ("src",),
            "object": ("data",),
            "audio": ("src",),
            "video": ("src", "poster"),
            "source": ("src", "srcset"),
        }
        for name in asset_attributes.get(tag, ()):
            value = attrs.get(name)
            if value and not value.startswith(("data:", "#")):
                self.external_assets.append((tag, name, value))

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)
        if self._style is not None:
            self._style.append(data)

    def handle_endtag(self, tag):
        if tag == "script":
            self._script = None
        if tag == "style":
            self._style = None


class AppAssertions:
    def assert_self_contained_html(self, html):
        self.assertIsInstance(html, str)
        self.assertNotIn("<!-- app.js -->", html)
        parsed = AppHTML()
        parsed.feed(html)
        parsed.close()
        self.assertEqual(parsed.html_count, 1)
        self.assertEqual(parsed.external_assets, [])
        scripts = ["".join(parts).strip() for parts in parsed.scripts]
        self.assertTrue(scripts, "The App must include its JavaScript inline")
        self.assertTrue(any(scripts), "The JavaScript bundle must not be empty")
        javascript = "\n".join(scripts)
        self.assertRegex(javascript, r"\b(?:function|class|const|let|var)\b|=>")
        self.assertNotRegex(
            javascript,
            r"(?m)^\s*import\s*(?:[\"'{*]|[A-Za-z_$])",
            "The inline bundle must not depend on unresolved module imports",
        )
        self.assertNotRegex(javascript, r"\bimport\s*\(")
        css = "\n".join("".join(parts) for parts in parsed.styles)
        self.assertNotRegex(css, r"(?i)@import\b")
        for asset in re.findall(r"(?i)url\(\s*([^)]*)\)", css):
            asset = asset.strip().strip("\"'")
            self.assertTrue(
                asset.startswith(("data:", "#")),
                f"CSS asset is not inlined: {asset}",
            )


class CustomerInformationTests(AppAssertions, unittest.TestCase):
    def test_no_argument_result_has_empty_customer_fields_and_retention_notice(self):
        self.assertEqual(dict(inspect.signature(customer_information).parameters), {})
        result = customer_information()
        self.assertIsInstance(result, dict)
        self.assertEqual(result["title"], "Customer information")
        self.assertEqual(result["storage"], "memory")
        self.assertIsInstance(result["fields"], dict)
        self.assertEqual(result["fields"], EXPECTED_FIELDS)
        self.assertIsInstance(result["message"], str)
        message = result["message"].lower()
        self.assertRegex(message, r"\bmemory\b")
        self.assertRegex(message, r"\b(?:ui|interface|app|browser|form)\b")
        self.assertRegex(message, r"\bopen\b")

    def test_each_call_returns_fresh_outer_and_nested_dictionaries(self):
        first = customer_information()
        second = customer_information()
        pristine = deepcopy(second)
        self.assertIsNot(first, second)
        self.assertIsNot(first["fields"], second["fields"])
        first["title"] = "Changed title"
        first["storage"] = "persistent"
        first["message"] = "Changed notice"
        first["fields"]["full_name"] = "Example Customer"
        first["fields"]["extra"] = "Must not leak"
        first["extra"] = "Must not leak"
        self.assertEqual(second, pristine)
        self.assertEqual(customer_information(), pristine)
        second["fields"].clear()
        self.assertEqual(customer_information(), pristine)

    def test_app_function_returns_one_bundled_html_document(self):
        self.assertEqual(RESOURCE_URI, EXPECTED_RESOURCE_URI)
        self.assert_self_contained_html(customer_information_app())

    def test_frontend_does_not_persist_or_send_customer_information(self):
        frontend = (
            Path(customer_information_module.__file__).parent / "frontend" / "main.js"
        )
        if not frontend.is_file():
            self.skipTest("Optional frontend/main.js has not been created yet")
        source = frontend.read_text(encoding="utf-8")
        forbidden = (
            r"\bfetch\b",
            r"\blocalStorage\b",
            r"\bsessionStorage\b",
            r"\bindexedDB\b",
            r"\bupdate[_-]?model[_-]?context\b",
            r"ui/update-model-context",
        )
        for pattern in forbidden:
            with self.subTest(pattern=pattern):
                self.assertNotRegex(source, re.compile(pattern, re.IGNORECASE))


class CustomerInformationMCPTests(AppAssertions, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server = MCPServer("customer-information-check", extensions=[Apps()])
        register(self.server)

    async def test_register_exposes_precisely_one_read_only_no_argument_tool(self):
        async with Client(self.server) as client:
            tools = (await client.list_tools()).tools
            self.assertEqual([tool.name for tool in tools], ["customer_information"])
            tool = tools[0]
            self.assertEqual(tool.input_schema.get("properties", {}), {})
            self.assertEqual(tool.input_schema.get("required", []), [])
            self.assertIsNotNone(tool.annotations)
            self.assertIs(tool.annotations.read_only_hint, True)
            self.assertEqual(tool.meta["ui"]["resourceUri"], EXPECTED_RESOURCE_URI)
            result = await client.call_tool("customer_information", {})
        self.assertFalse(result.is_error, result.content)
        self.assertEqual(result.structured_content, customer_information())

    async def test_registered_resource_is_bundled_html_with_app_mime_type(self):
        async with Client(self.server) as client:
            resources = (await client.list_resources()).resources
            matching = [
                resource
                for resource in resources
                if str(resource.uri) == EXPECTED_RESOURCE_URI
            ]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0].mime_type, APP_MIME_TYPE)
            result = await client.read_resource(EXPECTED_RESOURCE_URI)
        self.assertEqual(len(result.contents), 1)
        content = result.contents[0]
        self.assertEqual(str(content.uri), EXPECTED_RESOURCE_URI)
        self.assertEqual(content.mime_type, APP_MIME_TYPE)
        self.assertEqual(content.text, customer_information_app())
        self.assert_self_contained_html(content.text)


if __name__ == "__main__":
    unittest.main()
