from __future__ import annotations

import json
import logging
import os
import re
import secrets
import signal
import sys
import threading
from importlib.metadata import version
from ipaddress import ip_address
from time import monotonic
from urllib.parse import urlsplit

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from . import hosting, telemetry


def create_app(
    server: hosting.HostedMCPServer,
    internal_token: str,
    resource_url: str,
    *,
    catalog_commit: str = "",
    allowed_hosts: list[str] | None = None,
    allowed_origins: list[str] | None = None,
) -> ASGIApp:
    if not internal_token:
        raise ValueError("The execution process requires a private control token.")
    parsed_resource_url = urlsplit(resource_url)
    resource_origin = f"{parsed_resource_url.scheme}://{parsed_resource_url.netloc}"
    if allowed_hosts is None:
        allowed_hosts = [
            parsed_resource_url.netloc,
            "127.0.0.1:*",
            "localhost:*",
            "[::1]:*",
        ]
    if allowed_origins is None:
        allowed_origins = [
            resource_origin,
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
        ]
    admission_error: str | None = (
        "The Tool Server has not activated a Catalog Generation."
    )
    admission_deadline = 0.0

    def ready() -> bool:
        return admission_error is None or monotonic() < admission_deadline

    @server.custom_route("/healthz", methods=["GET"])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "pythonVersion": sys.version.split()[0],
                "mcpSdkVersion": version("mcp"),
                "ready": ready(),
                "error": admission_error,
            }
        )

    @server.custom_route("/admission", methods=["POST"])
    async def update_admission(request: Request) -> JSONResponse:
        nonlocal admission_error, admission_deadline
        payload = await request.json()
        admission_error = (
            None
            if payload["ready"]
            else payload.get("error") or "The Tool Server is unavailable."
        )
        admission_deadline = monotonic() + float(payload.get("graceSeconds", 0))
        return JSONResponse(
            {"status": "ok", "ready": ready(), "error": admission_error}
        )

    @server.custom_route("/activate", methods=["POST"])
    async def activate(request: Request) -> JSONResponse:
        payload = await request.json()
        if payload.get("catalogCommit") != catalog_commit:
            return JSONResponse(
                {
                    "status": "failed",
                    "error": "Catalog Generation does not match the execution process.",
                },
                status_code=409,
            )
        expected_tool_ids = set(payload.get("enabledToolIds", []))
        registered_tool_ids = set(server.tool_ids_by_mcp_name.values())
        if (
            registered_tool_ids - expected_tool_ids
            or set(server.load_errors) - expected_tool_ids
        ):
            return JSONResponse(
                {
                    "status": "failed",
                    "error": "Managed Tool registrations do not match enabled Tool Directories.",
                },
                status_code=409,
            )
        load_errors = server.load_errors | {
            tool_id: "Tool Directory registered no model-facing Tool."
            for tool_id in expected_tool_ids
            - registered_tool_ids
            - server.load_errors.keys()
        }
        for tool_id in load_errors.keys() - server.load_errors.keys():
            telemetry._operator_log(
                "error",
                "tool.activation.failed",
                toolId=tool_id,
                message=load_errors[tool_id],
            )
        tools = await server.list_tools()
        acknowledgement = {
            "status": "ok",
            "catalogCommit": catalog_commit,
            "tools": [tool.model_dump(mode="json", by_alias=True) for tool in tools],
            "toolIdsByMcpName": server.tool_ids_by_mcp_name,
            "loadErrors": load_errors,
            "profile": {
                "name": server.name,
                "title": server.title,
                "description": server.description,
                "instructions": server.instructions,
                "websiteUrl": str(server.website_url) if server.website_url else None,
            },
        }
        telemetry._operator_log(
            "info",
            "runtime.catalog.applied",
            catalogCommit=catalog_commit,
            toolCount=len(acknowledgement["tools"]),
        )
        return JSONResponse(acknowledgement)

    native_app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=False,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        ),
    )

    async def request_context(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await native_app(scope, receive, send)
            return
        request = Request(scope)
        private_control = request.url.path in {"/healthz", "/activate", "/admission"}
        if private_control and not (
            request.client
            and ip_address(request.client.host).is_loopback
            and secrets.compare_digest(
                request.headers.get("authorization", ""), f"Bearer {internal_token}"
            )
        ):
            await JSONResponse({"status": "unauthorized"}, status_code=401)(
                scope, receive, send
            )
            return
        if request.url.path.rstrip("/") == "/mcp" and not ready():
            await JSONResponse(
                {
                    "error": "mcp_unavailable",
                    "error_description": "The Tool Server is unavailable.",
                },
                status_code=503,
                headers={"retry-after": "5"},
            )(scope, receive, send)
            return
        log_token = None
        if private_control:
            log_token = telemetry._log_context.set(
                {
                    "actor": "management",
                    "correlationId": request.headers.get("x-correlation-id", "")[:256],
                }
            )
        try:
            await native_app(scope, receive, send)
        finally:
            if log_token is not None:
                telemetry._log_context.reset(log_token)

    wildcard_origins = [
        re.escape(origin[:-1]) + r"\d+"
        for origin in allowed_origins
        if origin.endswith(":*")
    ]
    application = CORSMiddleware(
        request_context,
        allow_origins=allowed_origins,
        allow_origin_regex="^(?:" + "|".join(wildcard_origins) + ")$"
        if wildcard_origins
        else None,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )
    if telemetry._telemetry_enabled:
        from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware

        application = OpenTelemetryMiddleware(
            application,
            excluded_urls=r"/healthz(?:\?|$)",
            exclude_spans=["send", "receive"],
        )
    return application


def main() -> None:
    telemetry.configure()
    if os.name == "posix" and os.getsid(0) != os.getpid():
        os.setsid()
    parent_fd = os.environ.pop("TOOLFORGE_PARENT_FD", None)
    if parent_fd is not None:

        def stop_with_parent() -> None:
            os.read(int(parent_fd), 1)
            if os.name == "posix":
                os.killpg(os.getpgrp(), signal.SIGTERM)
            else:
                os.kill(os.getpid(), signal.SIGTERM)

        threading.Thread(target=stop_with_parent, daemon=True).start()
    catalog_commit = os.environ.pop("TOOLFORGE_CATALOG_COMMIT")
    internal_token = os.environ.pop("TOOLFORGE_INTERNAL_MCP_TOKEN")
    host = os.environ.pop("TOOLFORGE_MCP_HOST", "0.0.0.0")
    port = int(os.environ.pop("TOOLFORGE_MCP_PORT", "8000"))
    resource_url = os.environ["TOOLFORGE_MCP_RESOURCE_URL"]
    allowed_hosts = json.loads(os.environ.pop("TOOLFORGE_MCP_ALLOWED_HOSTS", "null"))
    allowed_origins = json.loads(
        os.environ.pop("TOOLFORGE_MCP_ALLOWED_ORIGINS", "null")
    )
    trusted_client_ids = json.loads(
        os.environ.pop("TOOLFORGE_MCP_TRUSTED_CLIENT_IDS", "[]")
    )
    from catalog_app.server import create_server

    server = create_server(
        source_commit=catalog_commit,
        trusted_client_ids=trusted_client_ids,
        log_level=logging.getLevelName(telemetry._console_level),
    )
    application = create_app(
        server,
        internal_token,
        resource_url,
        catalog_commit=catalog_commit,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )
    telemetry._operator_log("info", "runtime.starting")

    uvicorn.run(
        application,
        host=host,
        port=port,
        log_level=telemetry._console_level,
        log_config=None,
        access_log=False,
        proxy_headers=False,
    )
    telemetry._operator_log("info", "runtime.stopped")


if __name__ == "__main__":
    main()
