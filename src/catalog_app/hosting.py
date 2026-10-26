"""Managed hosting behavior for a repository-defined MCP Application."""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext

from . import invocation, telemetry


class HostedMCPServer(MCPServer):
    """Native SDK server with ToolForge registration and call reporting."""

    def __init__(
        self,
        *,
        source_commit: str = "",
        trusted_client_ids: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        self._source_commit = source_commit
        self._trusted_client_ids = frozenset(trusted_client_ids or [])
        self.tool_ids_by_mcp_name: dict[str, str] = {}
        self.load_errors: dict[str, str] = {}
        self._tool_ids_by_registered_name: dict[str, str] = {}
        self._registering_tool_id: str | None = None
        super().__init__(**kwargs)
        self.middleware.append(self._observe_request)

    @contextmanager
    def tool_directory(self, tool_id: str):
        previous = self._registering_tool_id
        self._registering_tool_id = tool_id
        # ponytail: restore pinned SDK registries until it supports registration rollback.
        registries = (
            self._tool_manager._tools,
            self._resource_manager._resources,
            self._resource_manager._templates,
            self._prompt_manager._prompts,
            self._lowlevel_server._request_handlers,
            self.tool_ids_by_mcp_name,
            self._tool_ids_by_registered_name,
        )
        originals = tuple(registry.copy() for registry in registries)
        lists = (self._custom_starlette_routes, self.middleware)
        original_lists = tuple(items.copy() for items in lists)
        try:
            yield
            if tool_id not in self.tool_ids_by_mcp_name.values():
                raise ValueError(
                    f"Tool Directory {tool_id} registered no model-facing Tool"
                )
        except Exception as error:
            for registry, original in zip(registries, originals):
                registry.clear()
                registry.update(original)
            for items, original in zip(lists, original_lists):
                items[:] = original
            self.load_errors[tool_id] = str(error)
            telemetry._operator_log(
                "error",
                "tool.activation.failed",
                toolId=tool_id,
                sourceCommit=self._source_commit,
                **telemetry._error_fields(error),
            )
        finally:
            self._registering_tool_id = previous

    def add_tool(
        self,
        fn,
        name=None,
        title=None,
        description=None,
        annotations=None,
        icons=None,
        meta=None,
        structured_output=None,
    ) -> None:
        mcp_name = name or fn.__name__
        if self._tool_manager.get_tool(mcp_name) is not None:
            raise ValueError(f"MCP Name {mcp_name} is registered more than once")
        tool_id = self._registering_tool_id
        ui = (meta or {}).get("ui")
        visibility = (
            ui.get("visibility", ["model", "app"])
            if isinstance(ui, dict)
            else ["model", "app"]
        )
        if (
            tool_id
            and "model" in visibility
            and tool_id in self.tool_ids_by_mcp_name.values()
        ):
            raise ValueError(
                f"Tool Directory {tool_id} registered more than one model-facing Tool"
            )
        super().add_tool(
            fn,
            name=name,
            title=title,
            description=description,
            annotations=annotations,
            icons=icons,
            meta=meta,
            structured_output=structured_output,
        )
        if tool_id:
            self._tool_ids_by_registered_name[mcp_name] = tool_id
        if tool_id and "model" in visibility:
            self.tool_ids_by_mcp_name[mcp_name] = tool_id

    async def _observe_request(
        self, ctx: ServerRequestContext[dict[str, Any]], call_next: CallNext
    ) -> HandlerResult:
        is_call = ctx.method == "tools/call"
        fields: dict[str, str] = {}
        if is_call:
            headers = getattr(ctx.request, "headers", {})
            caller = get_access_token()
            claims = caller.claims or {} if caller is not None else {}
            scopes = claims.get("scp")
            delegated = isinstance(scopes, str) and bool(scopes.split())
            trusted_context = (
                caller is not None
                and caller.client_id in self._trusted_client_ids
                and (claims.get("azp") or claims.get("appid")) == caller.client_id
            )
            fields = {
                "actor": "mcp",
                "correlationId": headers.get("x-correlation-id", "")[:256]
                or uuid4().hex,
            }
            if trusted_context:
                supplied = (ctx.meta or {}).get("toolforge/context")
                if isinstance(supplied, dict):
                    for key in ("correlationId", "runId", "executionId") + (
                        ("userId", "userName", "userEmail") if delegated else ()
                    ):
                        value = supplied.get(key)
                        if isinstance(value, str) and value:
                            fields[key] = value[:256]
                    if supplied.get("actor") in {"user", "copilot", "mcp", "system"}:
                        fields["actor"] = supplied["actor"]
            if caller is not None:
                fields["clientId"] = caller.client_id
                tenant_id = claims.get("tid")
                object_id = claims.get("oid")
                if isinstance(tenant_id, str) and tenant_id:
                    fields["entraTenantId"] = tenant_id
                if isinstance(object_id, str) and object_id:
                    fields["entraObjectId"] = object_id
                fields["principalType"] = "user" if delegated else "application"
                if delegated:
                    if isinstance(object_id, str) and object_id:
                        fields.setdefault("userId", object_id)
                    name = claims.get("name")
                    if isinstance(name, str) and name:
                        fields["userName"] = name
                    email = claims.get("email") or claims.get("preferred_username")
                    if isinstance(email, str) and email:
                        fields["userEmail"] = email
            name = (ctx.params or {}).get("name")
            if isinstance(name, str):
                fields["toolName"] = name
        token = telemetry._log_context.set(fields)
        error_token = invocation._invocation_error.set(None)
        started = time.perf_counter()
        failure: BaseException | None = None
        result = None
        try:
            if is_call:
                name = fields.get("toolName", "")
                if tool_id := self._tool_ids_by_registered_name.get(name):
                    fields["toolId"] = tool_id
                    if self._source_commit:
                        fields["version"] = self._source_commit
                telemetry._operator_log("info", "tool.started")
            result = await call_next(ctx)
            return result
        except BaseException as error:
            failure = error
            if not is_call:
                telemetry._operator_log(
                    "error",
                    "mcp.request.failed",
                    method=ctx.method,
                    **telemetry._error_fields(error),
                )
            raise
        finally:
            if is_call:
                failure = failure or invocation._invocation_error.get()
                failed = failure is not None or (
                    result.get("isError", False)
                    if isinstance(result, dict)
                    else getattr(result, "is_error", False)
                )
                telemetry._operator_log(
                    "error" if failed else "info",
                    "tool.completed",
                    status="failed" if failed else "completed",
                    isError=bool(failed),
                    durationMs=round((time.perf_counter() - started) * 1000, 2),
                    **telemetry._error_fields(failure),
                )
                if telemetry._telemetry_enabled and failed:
                    from opentelemetry import trace
                    from opentelemetry.trace import StatusCode

                    trace.get_current_span().set_status(StatusCode.ERROR)
            invocation._invocation_error.reset(error_token)
            telemetry._log_context.reset(token)

    async def call_tool(self, name: str, arguments: dict[str, Any], context=None):
        if context is None:
            return await super().call_tool(name, arguments, context)
        reporter = invocation.InvocationReporter(context, asyncio.get_running_loop())
        session = context.request_context.session
        outbound = session._request_outbound
        session._request_outbound = invocation.InvocationOutbound(outbound, reporter)
        token = invocation._invocation_reporter.set(reporter)
        try:
            return await super().call_tool(name, arguments, context)
        except Exception as error:
            invocation._invocation_error.set(error)
            raise
        finally:
            invocation._invocation_reporter.reset(token)
            try:
                await reporter.drain()
            finally:
                session._request_outbound = outbound
