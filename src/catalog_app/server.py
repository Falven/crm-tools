"""Set up the Catalog's access checks and Tools on the chosen MCP server."""

import os
from typing import Any

from mcp.server.apps import Apps
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver.context import Context
from mcp.types import ToolAnnotations
from mcp_types import CallToolResult, InputRequiredResult
from starlette.applications import Starlette

from . import credentials
from .auth import MiseAuth, MiseRequestContextMiddleware
from .hosting import HostedMCPServer


def create_server(**kwargs: Any) -> HostedMCPServer:
    auth = MiseAuth(
        tenant_id=os.environ["TOOLFORGE_MCP_ENTRA_TENANT_ID"],
        audience=os.environ["TOOLFORGE_MCP_ENTRA_AUDIENCE"],
        permission=os.environ["TOOLFORGE_MCP_ENTRA_PERMISSION"],
        resource_url=os.environ["TOOLFORGE_MCP_RESOURCE_URL"],
        client_id=os.environ["TOOLFORGE_MCP_ENTRA_CLIENT_ID"],
        client_secret=os.environ.get("TOOLFORGE_MCP_ENTRA_CLIENT_SECRET"),
        managed_identity_client_id=os.environ.get(
            "TOOLFORGE_MCP_ENTRA_MANAGED_IDENTITY_CLIENT_ID"
        ),
    )

    class CatalogServer(HostedMCPServer):
        async def call_tool(
            self,
            name: str,
            arguments: dict[str, Any],
            context: Context[dict[str, Any], Any] | None = None,
        ) -> CallToolResult | InputRequiredResult:
            """Keep the caller's credential open for this Tool call, then close it."""
            # Skip this setup for direct Python calls that have no MCP request.
            if context is None:
                return await super().call_tool(name, arguments, context)
            # Preserve the details needed to sign in again before the MCP Python
            # library converts errors to plain text.
            return await credentials.run_with_caller(
                auth, get_access_token(), super().call_tool(name, arguments, context)
            )

        def streamable_http_app(self, **kwargs: Any) -> Starlette:
            app = super().streamable_http_app(**kwargs)
            app.add_middleware(MiseRequestContextMiddleware)
            return app

    server_class = CatalogServer
    server = server_class(
        name="toolforge",
        title="",
        description="",
        instructions="",
        extensions=[Apps()],
        auth=auth.settings,
        token_verifier=auth,
        **kwargs,
    )
    tool_directory = server.tool_directory
    with tool_directory("contoso_open_request"):
        from .tools.contoso_open_request import register

        register(server)

    with tool_directory("contoso_save_request"):
        from .tools.contoso_open_request import (
            RESOURCE_URI as CONTOSO_REQUEST_RESOURCE_URI,
            _strict_intake_inputs,
        )
        from .tools.contoso_save_request import contoso_save_request

        server.add_tool(
            contoso_save_request,
            title="Save software request",
            meta={
                "ui": {
                    "resourceUri": CONTOSO_REQUEST_RESOURCE_URI,
                    "visibility": ["model", "app"],
                }
            },
            annotations=ToolAnnotations(
                read_only_hint=False,
                destructive_hint=True,
                idempotent_hint=True,
                open_world_hint=False,
            ),
        )

        _strict_intake_inputs(server, "contoso_save_request")

    with tool_directory("contoso_create_request"):
        from .tools.contoso_open_request import (
            RESOURCE_URI as CONTOSO_REQUEST_RESOURCE_URI,
            _strict_intake_inputs,
        )
        from .tools.contoso_create_request import contoso_create_request

        server.add_tool(
            contoso_create_request,
            title="Create software request draft",
            meta={
                "ui": {
                    "resourceUri": CONTOSO_REQUEST_RESOURCE_URI,
                    "visibility": ["model", "app"],
                }
            },
            annotations=ToolAnnotations(
                read_only_hint=False,
                destructive_hint=False,
                idempotent_hint=False,
                open_world_hint=False,
            ),
        )

        _strict_intake_inputs(server, "contoso_create_request")

    with tool_directory("contoso_submit_request"):
        from .tools.contoso_open_request import (
            RESOURCE_URI as CONTOSO_REQUEST_RESOURCE_URI,
            _strict_intake_inputs,
        )
        from .tools.contoso_submit_request import contoso_submit_request

        server.add_tool(
            contoso_submit_request,
            title="Submit software request",
            meta={
                "ui": {
                    "resourceUri": CONTOSO_REQUEST_RESOURCE_URI,
                    "visibility": ["model", "app"],
                }
            },
            annotations=ToolAnnotations(
                read_only_hint=False,
                destructive_hint=True,
                idempotent_hint=True,
                open_world_hint=False,
            ),
        )

        _strict_intake_inputs(server, "contoso_submit_request")

    with tool_directory("contoso_list_requests"):
        from .tools.contoso_list_requests import contoso_list_requests

        server.add_tool(
            contoso_list_requests,
            title="List software requests",
            annotations=ToolAnnotations(
                read_only_hint=True,
                destructive_hint=False,
                idempotent_hint=True,
                open_world_hint=False,
            ),
        )

    with tool_directory("contoso_save_answers"):
        from .tools.contoso_open_request import (
            RESOURCE_URI as CONTOSO_REQUEST_RESOURCE_URI,
        )
        from .tools.contoso_save_answers import contoso_save_answers

        server.add_tool(
            contoso_save_answers,
            title="Save clarification answers",
            meta={
                "ui": {
                    "resourceUri": CONTOSO_REQUEST_RESOURCE_URI,
                    "visibility": ["model", "app"],
                }
            },
            annotations=ToolAnnotations(
                read_only_hint=False,
                destructive_hint=True,
                idempotent_hint=True,
                open_world_hint=False,
            ),
        )

    with tool_directory("contoso_submit_answers"):
        from .tools.contoso_open_request import (
            RESOURCE_URI as CONTOSO_REQUEST_RESOURCE_URI,
        )
        from .tools.contoso_submit_answers import contoso_submit_answers

        server.add_tool(
            contoso_submit_answers,
            title="Submit clarification answers",
            meta={
                "ui": {
                    "resourceUri": CONTOSO_REQUEST_RESOURCE_URI,
                    "visibility": ["model", "app"],
                }
            },
            annotations=ToolAnnotations(
                read_only_hint=False,
                destructive_hint=True,
                idempotent_hint=True,
                open_world_hint=False,
            ),
        )

    return server
