from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sys
from contextlib import asynccontextmanager
from importlib.metadata import version
from ipaddress import ip_address
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import uvicorn
from mcp.server.auth.middleware.bearer_auth import RequireAuthMiddleware
from mcp.server.auth.routes import build_resource_metadata_url
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from . import hosting, telemetry


def create_app(
    server: hosting.HostedMCPServer,
    internal_token: str,
    resource_url: str,
    *,
    catalog_commit: str,
    launch: dict[str, Any],
    secret_values: list[str],
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
    admission_error: str | None = "The MCP Application has not finished startup."
    acknowledgement: dict[str, Any] | None = None
    action_id = launch["actionId"]
    git_revision = launch["gitRevision"]

    def safe_error(value: str | None) -> str | None:
        if value is None:
            return None
        for secret in secret_values:
            value = value.replace(secret, "[REDACTED]").replace(
                quote(secret), "[REDACTED]"
            )
        value = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [REDACTED]", value)
        value = re.sub(
            r"(?i)https?://[\w-]+\.vault\.azure\.net[^\s\"'<>)]*",
            "[REDACTED_KEY_VAULT_URI]",
            value,
        )
        value = re.sub(
            r"(?i)/subscriptions/[^\s/]+/resourceGroups/[^\s/]+/providers/Microsoft\.ManagedIdentity/userAssignedIdentities/[^\s\"'<>)]*",
            "[REDACTED_IDENTITY]",
            value,
        )
        value = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", value)
        return re.sub(
            r"(?i)([?&](?:sig|token|key|secret|password|access_token)=)[^&\s]+",
            r"\1[REDACTED]",
            value,
        )

    def ready() -> bool:
        return acknowledgement is not None and admission_error is None

    def observation() -> dict[str, Any]:
        return {
            "instanceId": launch["instanceId"],
            "processId": launch["processId"],
            "role": "mcp",
            "state": "healthy" if ready() else "unhealthy",
            "pid": os.getpid(),
            "workerProcessId": launch["processId"],
            "workerState": "running",
            "workerExitCode": None,
            "error": safe_error(admission_error),
            "python": sys.executable,
            "pythonVersion": sys.version.split()[0],
            "mcpSdkVersion": version("mcp"),
            "appliedCatalogCommit": catalog_commit
            if acknowledgement is not None
            else None,
            "sourceRevision": launch["sourceRevision"],
            "gitRevision": git_revision,
            "requirementsRevision": launch["requirementsRevision"],
            "requirementsLockRevision": launch["requirementsLockRevision"],
            "environment": launch["environment"],
            "tools": acknowledgement["tools"] if acknowledgement else [],
            "toolIdsByMcpName": server.tool_ids_by_mcp_name,
            "loadErrors": acknowledgement["loadErrors"] if acknowledgement else {},
            "profile": acknowledgement["profile"] if acknowledgement else {},
        }

    async def health(request: Request) -> JSONResponse:
        return JSONResponse(
            {**observation(), "status": "ok", "ready": ready(), "actionId": action_id},
            headers={"Cache-Control": "no-store"},
        )

    @server.custom_route("/readyz", methods=["GET"])
    async def readiness(request: Request) -> Response:
        return Response(
            status_code=200 if ready() else 503, headers={"Cache-Control": "no-store"}
        )

    async def update_admission(request: Request) -> JSONResponse:
        nonlocal admission_error, action_id, git_revision
        payload = await request.json()
        admission_error = (
            None
            if payload["ready"]
            else payload.get("error") or "The MCP Application is unavailable."
        )
        if "actionId" in payload:
            action_id = payload["actionId"]
        if "gitRevision" in payload:
            git_revision = payload["gitRevision"]
        return await health(request)

    async def activate() -> None:
        nonlocal acknowledgement, admission_error
        expected_tool_ids = set(launch["toolVersions"])
        registered_tool_ids = set(server.tool_ids_by_mcp_name.values())
        if registered_tool_ids - expected_tool_ids:
            raise RuntimeError(
                "Managed Tool registrations do not match enabled Tool Directories."
            )
        load_errors = {
            tool_id: error
            for tool_id, error in server.load_errors.items()
            if tool_id in expected_tool_ids
        } | {
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
            "tools": [tool.model_dump(mode="json", by_alias=True) for tool in tools],
            "loadErrors": {
                tool_id: {
                    "version": launch["toolVersions"][tool_id],
                    "error": safe_error(error),
                }
                for tool_id, error in load_errors.items()
            },
            "profile": {
                "name": server.name,
                "title": server.title,
                "description": server.description,
                "instructions": server.instructions,
                "websiteUrl": str(server.website_url) if server.website_url else None,
            },
        }
        admission_error = None
        telemetry._operator_log(
            "info",
            "runtime.catalog.applied",
            catalogCommit=catalog_commit,
            toolCount=len(tools),
        )

    native_app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=False,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        ),
    )

    async def public_status(request: Request) -> JSONResponse:
        return JSONResponse(observation(), headers={"Cache-Control": "no-store"})

    status_route = Route("/status", public_status, methods=["GET"])
    authentication = server._authentication
    if authentication is not None:
        status_route.app = RequireAuthMiddleware(
            status_route.app,
            authentication.settings.required_scopes or [],
            build_resource_metadata_url(authentication.settings.resource_server_url)
            if authentication.settings.resource_server_url
            else None,
        )
    native_app.routes.append(status_route)
    native_lifespan = native_app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        nonlocal admission_error
        async with native_lifespan(app):
            await activate()
            try:
                yield
            finally:
                admission_error = "The MCP Application is stopping."

    native_app.router.lifespan_context = lifespan
    private_routes = {
        "/healthz": ("GET", health),
        "/admission": ("POST", update_admission),
    }

    async def request_context(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await native_app(scope, receive, send)
            return
        request = Request(scope, receive)
        private = private_routes.get(request.url.path)
        if private is not None:
            if not (
                request.client
                and ip_address(request.client.host).is_loopback
                and secrets.compare_digest(
                    request.headers.get("authorization", "").encode(),
                    f"Bearer {internal_token}".encode(),
                )
            ):
                await JSONResponse({"status": "unauthorized"}, status_code=401)(
                    scope, receive, send
                )
                return
            method, handler = private
            if request.method != method:
                await Response(status_code=405, headers={"Allow": method})(
                    scope, receive, send
                )
                return
            fields = {
                "actor": "management",
                "correlationId": request.headers.get("x-correlation-id", "")[:256],
            }
            for field, header in (
                ("userName", "x-toolforge-user-name"),
                ("userEmail", "x-toolforge-user-email"),
            ):
                if header in request.headers:
                    fields[field] = unquote(request.headers[header])
            log_token = telemetry._log_context.set(fields)
            try:
                response = await handler(request)
                await response(scope, receive, send)
            finally:
                telemetry._log_context.reset(log_token)
            return
        if request.url.path.rstrip("/") == "/mcp" and not ready():
            await JSONResponse(
                {
                    "error": "mcp_unavailable",
                    "error_description": "The MCP Application is unavailable.",
                },
                status_code=503,
                headers={"retry-after": "5"},
            )(scope, receive, send)
            return
        await native_app(scope, receive, send)

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
    startup_context = telemetry._log_context.set(
        json.loads(os.environ.pop("TOOLFORGE_INTERNAL_STARTUP_CONTEXT", "{}"))
    )
    telemetry.configure()
    launch = json.loads(os.environ.pop("TOOLFORGE_INTERNAL_APPLICATION"))
    telemetry._process_context = {
        "workerProcessId": launch["processId"],
        "commit": launch["catalogCommit"],
        "environmentRevision": launch["environment"]["appliedRevision"],
    }
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
    # Tool imports may consume and remove environment values before reporting an error.
    secret_values = sorted(
        {
            value
            for name, value in os.environ.items()
            if value
            and (
                name in launch["environment"]["names"]
                or re.search(
                    r"SECRET|TOKEN|PASSWORD|CONNECTION_STRING|IDENTITY_HEADER|MSI_SECRET",
                    name,
                )
            )
        }
        | {internal_token},
        key=len,
        reverse=True,
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
        launch=launch,
        secret_values=secret_values,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )
    telemetry._operator_log("info", "runtime.starting")
    # Construction failures keep their context for the process exception hook.
    telemetry._log_context.reset(startup_context)

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
