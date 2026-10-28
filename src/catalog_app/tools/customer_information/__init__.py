"""A customer information form with ephemeral, App-instance-only state."""

from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

RESOURCE_URI = "ui://customer-information/app.html"
FIELDS = ("full_name", "company", "email", "phone", "address", "notes")
ASSETS = Path(__file__).parent


def customer_information() -> dict[str, Any]:
    """Open a simple, blank customer information form.

    Includes full name, company, email, phone, address, and notes; all optional.
    Edits are held instantly in the open App's JavaScript memory only. No database,
    files, browser storage, server-side customer state, or automatic transmission
    of edited values to the assistant. Closing, reloading, or reopening the App
    discards edits. Review details and Clear form operate locally. Returns a blank
    field dictionary and retention information for clients without App support.
    """
    return {
        "title": "Customer information",
        "fields": dict.fromkeys(FIELDS, ""),
        "storage": "memory",
        "message": (
            "Values stay only in the open form's memory. Nothing is saved or sent "
            "to the server or assistant. Closing or reloading the form clears all values."
        ),
    }


def customer_information_app() -> str:
    """Serve the self-contained form; never read or store customer data."""
    html = (ASSETS / "app.html").read_text(encoding="utf-8")
    script = (ASSETS / "app.js").read_text(encoding="utf-8")
    if html.count("<!-- app.js -->") != 1:
        raise RuntimeError("The customer information interface is incomplete.")
    return html.replace(
        "<!-- app.js -->", f'<script type="module">\n{script}\n</script>'
    )


def register(server: MCPServer) -> None:
    server.add_tool(
        customer_information,
        title="Customer information form",
        meta={"ui": {"resourceUri": RESOURCE_URI}},
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    server.resource(
        RESOURCE_URI,
        mime_type="text/html;profile=mcp-app",
        title="Customer information",
        meta={"ui": {"csp": {"resourceDomains": [], "connectDomains": []}}},
    )(customer_information_app)
