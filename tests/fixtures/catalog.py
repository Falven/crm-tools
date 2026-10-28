"""Reuse production Catalog registration in the existing isolated Tool checks."""

import os
from unittest.mock import Mock, patch

from mcp.server.auth.settings import AuthSettings

from catalog_app.server import create_server


def register_catalog_tool(server, tool_id):
    environment = {
        "TOOLFORGE_MCP_ENTRA_TENANT_ID": "tenant",
        "TOOLFORGE_MCP_ENTRA_AUDIENCE": "audience",
        "TOOLFORGE_MCP_ENTRA_PERMISSION": "read",
        "TOOLFORGE_MCP_ENTRA_CLIENT_ID": "client",
        "TOOLFORGE_MCP_RESOURCE_URL": "https://example.test/mcp",
    }
    with (
        patch.dict(os.environ, environment),
        patch(
            "catalog_app.server.MiseAuth.from_env",
            return_value=Mock(
                settings=AuthSettings(
                    issuer_url="https://issuer.test",
                    resource_server_url="https://example.test/mcp",
                    validate_token_resource=False,
                )
            ),
        ),
    ):
        catalog = create_server()
    module = f"catalog_app.tools.{tool_id}"
    # Preserve the actual SDK argument models, including strict input settings.
    server._tool_manager._tools.update(
        (name, tool)
        for name, tool in catalog._tool_manager._tools.items()
        if tool.fn.__module__ == module
    )
    for resource in catalog._resource_manager.list_resources():
        if resource.fn.__module__ == module:
            server.add_resource(resource)
