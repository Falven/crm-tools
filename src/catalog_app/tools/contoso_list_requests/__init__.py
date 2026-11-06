"""List the signed-in customer's saved Contoso requests."""

from pathlib import Path
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from ..contoso_open_request import (
    _customer_partition,
    _next_action,
    _storage,
    _validate_request,
)


RESOURCE_URI = "ui://contoso-projects/app.html"


def contoso_list_requests(
    needs_attention: Annotated[bool, Field(strict=True)] = False,
) -> dict[str, Any]:
    """List your requests; needs_attention limits results to drafts and clarification.

    Returns project IDs, titles, statuses, and next actions, plus an App on
    supported clients. The same project data is available without App support.
    """
    partition = _customer_partition()
    with _storage() as table:
        entities = table.query_entities(
            "PartitionKey eq @partition and DemoId eq @demo",
            parameters={"partition": partition, "demo": "contoso-demo"},
        )
        requests = []
        for entity in entities:
            _validate_request(entity)
            if needs_attention and entity["Status"] not in (
                "draft",
                "needs_clarification",
            ):
                continue
            requests.append(
                {
                    "request_id": entity["RowKey"],
                    "title": entity["Title"],
                    "status": entity["Status"],
                    "next_action": _next_action(entity["Status"]),
                }
            )
    return {"requests": sorted(requests, key=lambda request: request["request_id"])}


def contoso_projects_app() -> str:
    """Serve the project list without reading customer records."""
    assets = Path(__file__).parent
    html = (assets / "app.html").read_text(encoding="utf-8")
    script = (assets / "app.js").read_text(encoding="utf-8")
    return html.replace(
        "<!-- app.js -->", f'<script type="module">\n{script}\n</script>'
    )


def register(server: MCPServer) -> None:
    server.add_tool(
        contoso_list_requests,
        title="List software projects",
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
        title="Software projects",
        meta={"ui": {"csp": {"resourceDomains": ["https://cdn.jsdelivr.net"]}}},
    )(contoso_projects_app)
