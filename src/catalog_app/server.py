"""Set up the Catalog's access checks and Tools on the chosen MCP server."""

from typing import Any

from mcp.server.apps import Apps
from mcp.types import ToolAnnotations

from .mise import MiseAuth, MiseMCPServer as server_class


def create_server(**kwargs: Any) -> server_class:
    server = server_class(
        name="toolforge",
        title="",
        description="",
        instructions="",
        authentication=MiseAuth.from_env(),
        extensions=[Apps()],
        **kwargs,
    )
    tool_directory = server.tool_directory
    with tool_directory("contoso_open_request"):
        from .tools.contoso_open_request import register

        register(server)

    with tool_directory("contoso_review_request"):
        from .tools.contoso_open_request import (
            RESOURCE_URI as CONTOSO_REQUEST_RESOURCE_URI,
            _strict_inputs,
        )
        from .tools.contoso_review_request import contoso_review_request

        server.add_tool(
            contoso_review_request,
            title="Review software request",
            meta={"ui": {"resourceUri": CONTOSO_REQUEST_RESOURCE_URI}},
            annotations=ToolAnnotations(
                read_only_hint=True,
                destructive_hint=False,
                idempotent_hint=True,
                open_world_hint=False,
            ),
        )
        _strict_inputs(server, "contoso_review_request")

    with tool_directory("contoso_submit_request"):
        from .tools.contoso_open_request import _strict_inputs
        from .tools.contoso_submit_request import contoso_submit_request

        server.add_tool(
            contoso_submit_request,
            title="Submit software request",
            meta={"ui": {"visibility": ["model", "app"]}},
            annotations=ToolAnnotations(
                read_only_hint=False,
                destructive_hint=False,
                idempotent_hint=True,
                open_world_hint=False,
            ),
        )
        _strict_inputs(server, "contoso_submit_request")

    with tool_directory("contoso_list_requests"):
        from .tools.contoso_list_requests import contoso_list_requests

        server.add_tool(
            contoso_list_requests,
            title="List software projects",
            annotations=ToolAnnotations(
                read_only_hint=True,
                destructive_hint=False,
                idempotent_hint=True,
                open_world_hint=False,
            ),
        )

    with tool_directory("contoso_submit_answers"):
        from .tools.contoso_open_request import _strict_inputs
        from .tools.contoso_submit_answers import contoso_submit_answers

        server.add_tool(
            contoso_submit_answers,
            title="Submit clarification answers",
            meta={"ui": {"visibility": ["model", "app"]}},
            annotations=ToolAnnotations(
                read_only_hint=False,
                destructive_hint=True,
                idempotent_hint=True,
                open_world_hint=False,
            ),
        )
        _strict_inputs(server, "contoso_submit_answers")

    with tool_directory("customer_information"):
        from .tools.customer_information import register as register_customer_information

        register_customer_information(server)

    return server
