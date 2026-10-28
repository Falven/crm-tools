"""A customer intake App with no data ingestion or persistence."""

from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

RESOURCE_URI = "ui://customer-ingestion/app.html"


def customer_ingestion() -> dict[str, Any]:
    """Open a blank, private customer intake form.

    Asks for first name, last name, optional age, and optional description.
    Entered details stay only in the open App's DOM/memory: they are not sent
    to this Tool, stored on the server or in browser storage, or added to model
    context. Review details is an on-screen preview, not a submission. Clear
    form or reopening the App discards the details. This Tool accepts no inputs
    and returns only the form's field descriptions and privacy instructions.
    Clients without Apps can read these instructions but cannot fill the form.
    Do not ask the user to put customer details into chat as a fallback.
    """
    return {
        "title": "Customer intake",
        "status": "blank_form",
        "fields": [
            {
                "id": "first_name",
                "label": "First name",
                "required": True,
                "max_length": 100,
            },
            {
                "id": "last_name",
                "label": "Last name",
                "required": True,
                "max_length": 100,
            },
            {
                "id": "age",
                "label": "Age",
                "required": False,
                "type": "integer",
                "minimum": 0,
                "maximum": 150,
            },
            {
                "id": "description",
                "label": "Description",
                "required": False,
                "max_length": 2000,
            },
        ],
        "persistence": "none",
        "instructions": (
            "Open the App to enter and review customer details. Details stay only "
            "in this open form; they are not saved or sent to the server or the "
            "conversation. Clear the form to discard them. Reopening starts blank."
        ),
    }


def customer_ingestion_app() -> str:
    """Serve static interface assets without reading or accepting customer data."""
    assets = Path(__file__).parent
    html = (assets / "app.html").read_text(encoding="utf-8")
    script = (assets / "app.js").read_text(encoding="utf-8")
    if html.count("<!-- app.js -->") != 1:
        raise ToolError("The customer intake interface is incomplete.")
    return html.replace("<!-- app.js -->", f'<script type="module">\n{script}\n</script>')


def register(server: MCPServer) -> None:
    server.add_tool(
        customer_ingestion,
        title="Customer intake (not saved)",
        meta={"ui": {"resourceUri": RESOURCE_URI}},
        annotations=ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    # No argument (including a customer field) should be silently accepted. Keep
    # this empty schema strict, using the pinned SDK's existing catalog pattern.
    tool = server._tool_manager.get_tool("customer_ingestion")
    if tool is None:
        raise RuntimeError("Customer intake Tool was not registered.")
    model = tool.fn_metadata.arg_model
    model.model_config = {**model.model_config, "extra": "forbid", "strict": True}
    model.model_rebuild(force=True)
    tool.parameters = model.model_json_schema(by_alias=True)

    server.resource(
        RESOURCE_URI,
        mime_type="text/html;profile=mcp-app",
        title="Customer intake",
        meta={
            "ui": {
                "csp": {
                    "resourceDomains": ["https://cdn.jsdelivr.net"],
                    "connectDomains": [],
                }
            }
        },
    )(customer_ingestion_app)
