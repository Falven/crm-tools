"""Managed hosting behavior for a repository-defined MCP Application."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.shared.dispatcher import CallOptions

from . import credentials, telemetry
from .auth import CatalogAuth


class InvocationOutbound:
    def __init__(self, outbound: Any, reporter: telemetry.InvocationReporter) -> None:
        self.outbound = outbound
        self.reporter = reporter

    async def notify(
        self,
        method: str,
        params: Mapping[str, Any] | None,
        opts: CallOptions | None = None,
    ) -> None:
        if telemetry._reporting_output.get():
            await self.outbound.notify(method, params, opts)
            return
        if method == "notifications/message" and params is not None:
            telemetry._operator_log(
                params["level"],
                "tool.log",
                message=params["data"],
                logger=params.get("logger"),
            )
        await self.reporter.wait(
            self.reporter.enqueue(lambda: self.outbound.notify(method, params, opts))
        )

    async def progress(
        self, progress: float, total: float | None = None, message: str | None = None
    ) -> None:
        if telemetry._reporting_output.get():
            await self.outbound.progress(progress, total, message)
            return
        telemetry._operator_log(
            "info", "tool.progress", progress=progress, total=total, message=message
        )
        await self.reporter.wait(
            self.reporter.enqueue(
                lambda: self.outbound.progress(progress, total, message)
            )
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self.outbound, name)


class HostedMCPServer(MCPServer):
    """Native SDK server with ToolForge registration and call reporting."""

    def __init__(
        self,
        *,
        authentication: CatalogAuth | None = None,
        source_commit: str = "",
        trusted_client_ids: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        self._authentication = authentication
        self._source_commit = source_commit
        self._trusted_client_ids = frozenset(trusted_client_ids or [])
        self.tool_ids_by_mcp_name: dict[str, str] = {}
        self.load_errors: dict[str, str] = {}
        self._tool_ids_by_registered_name: dict[str, str] = {}
        self._registering_tool_id: str | None = None
        if authentication is None:
            super().__init__(**kwargs)
        else:
            super().__init__(
                auth=authentication.settings, token_verifier=authentication, **kwargs
            )
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
            identity = (
                self._authentication.caller_identity(caller)
                if self._authentication is not None and caller is not None
                else {}
            )
            invalid_identity = (
                self._authentication is not None
                and caller is not None
                and (
                    not isinstance(identity, dict)
                    or identity.get("principalType") not in {"user", "application"}
                )
            )
            if invalid_identity:
                identity = {}
            delegated = identity.get("principalType") == "user"
            trusted_context = (
                caller is not None and caller.client_id in self._trusted_client_ids
            )
            fields = {
                "actor": "mcp",
                "correlationId": headers.get("x-correlation-id", "")[:256]
                or uuid4().hex,
                **identity,
            }
            if caller is not None:
                fields["clientId"] = caller.client_id
            if trusted_context:
                supplied = (ctx.meta or {}).get("toolforge/context")
                if isinstance(supplied, dict):
                    if delegated and any(
                        key in supplied for key in ("userId", "userName", "userEmail")
                    ):
                        for key in ("userId", "userName", "userEmail"):
                            fields.pop(key, None)
                    for key in ("correlationId", "runId", "executionId") + (
                        ("userId", "userName", "userEmail") if delegated else ()
                    ):
                        value = supplied.get(key)
                        if isinstance(value, str) and value:
                            fields[key] = value[:256]
                    if supplied.get("actor") in {"user", "agent", "mcp", "system"}:
                        fields["actor"] = supplied["actor"]
            name = (ctx.params or {}).get("name")
            if isinstance(name, str):
                fields["toolName"] = name
        with telemetry._log_context.set(fields), telemetry._invocation_error.set(None):
            started = time.perf_counter()
            failure: BaseException | None = None
            result = None
            try:
                if is_call and invalid_identity:
                    raise ValueError(
                        "Authentication provider caller_identity must return principalType 'user' or 'application'."
                    )
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
                    failure = failure or telemetry._invocation_error.get()
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

    async def call_tool(self, name: str, arguments: dict[str, Any], context=None):
        if context is None:
            return await super().call_tool(name, arguments, context)
        return await credentials.run_with_caller(
            self._authentication,
            get_access_token(),
            self._call_with_reporting(name, arguments, context),
        )

    async def _call_with_reporting(self, name, arguments, context):
        reporter = telemetry.InvocationReporter(context, asyncio.get_running_loop())
        session = context.request_context.session
        outbound = session._request_outbound
        session._request_outbound = InvocationOutbound(outbound, reporter)
        try:
            with telemetry._invocation_reporter.set(reporter):
                try:
                    return await super().call_tool(name, arguments, context)
                except BaseException as error:
                    telemetry._invocation_error.set(error)
                    if isinstance(error, asyncio.CancelledError):
                        reporter.abort()
                    raise
        finally:
            try:
                await reporter.drain()
            finally:
                session._request_outbound = outbound
