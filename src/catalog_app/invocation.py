from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any, Awaitable, Callable

from mcp.shared.context import BaseContext
from mcp.shared.dispatcher import CallOptions, DispatchContext
from mcp.shared.message import MessageMetadata
from mcp.shared.transport_context import TransportContext
from mcp_types import RequestId

from . import telemetry

_reporting_output: ContextVar[bool] = ContextVar("reporting_output", default=False)
_invocation_error: ContextVar[Exception | None] = ContextVar(
    "invocation_error", default=None
)


class InvocationReporter:
    def __init__(self, context: Any, loop: asyncio.AbstractEventLoop) -> None:
        self.context = context
        self.loop = loop
        self.pending: asyncio.Task[None] | concurrent.futures.Future[None] | None = None
        self.lock = threading.Lock()

    def enqueue(
        self, output: Callable[[], Awaitable[None]]
    ) -> asyncio.Task[None] | concurrent.futures.Future[None]:
        with self.lock:
            previous = self.pending
            coroutine = self._send_after(previous, output)
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            if running_loop is self.loop:
                pending = self.loop.create_task(coroutine)
            else:
                pending = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
            self.pending = pending
            return pending

    def log(self, level: str, data: str, logger_name: str) -> None:
        self.enqueue(lambda: self.context.log(level, data, logger_name=logger_name))

    async def _send_after(
        self,
        previous: asyncio.Task[None] | concurrent.futures.Future[None] | None,
        output: Callable[[], Awaitable[None]],
    ) -> None:
        if previous:
            await (
                previous
                if isinstance(previous, asyncio.Task)
                else asyncio.wrap_future(previous)
            )
        token = _reporting_output.set(True)
        try:
            await output()
        finally:
            _reporting_output.reset(token)

    async def wait(
        self, pending: asyncio.Task[None] | concurrent.futures.Future[None]
    ) -> None:
        await (
            pending
            if isinstance(pending, asyncio.Task)
            else asyncio.wrap_future(pending)
        )

    async def drain(self) -> None:
        with self.lock:
            pending = self.pending
            self.pending = None
        if pending is not None:
            await self.wait(pending)


_invocation_reporter: ContextVar[InvocationReporter | None] = ContextVar(
    "invocation_reporter", default=None
)


class InvocationOutbound(BaseContext[TransportContext]):
    def __init__(
        self, outbound: DispatchContext[TransportContext], reporter: InvocationReporter
    ) -> None:
        super().__init__(outbound)
        self.outbound = outbound
        self.reporter = reporter

    @property
    def request_id(self) -> RequestId | None:
        return self.outbound.request_id

    @property
    def message_metadata(self) -> MessageMetadata:
        return self.outbound.message_metadata

    async def notify(
        self,
        method: str,
        params: Mapping[str, Any] | None,
        opts: CallOptions | None = None,
    ) -> None:
        if _reporting_output.get():
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
        if _reporting_output.get():
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


class InvocationOutput:
    def __init__(self, fallback: Any, level: str, logger_name: str) -> None:
        self.fallback = fallback
        self.level = level
        self.logger_name = logger_name

    def write(self, data: str) -> int:
        reporter = _invocation_reporter.get()
        if data:
            telemetry._operator_log(
                self.level, "python.output", stream=self.logger_name, message=data
            )
        if data and reporter is not None and not _reporting_output.get():
            reporter.log(self.level, data, self.logger_name)
        return len(data)

    def flush(self) -> None:
        self.fallback.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.fallback, name)


class InvocationLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        # Wire dumps copy MCP payloads and recursively log the management log stream itself.
        if record.name == "sse_starlette.sse" and record.msg in (
            "chunk: %s",
            "ping: %s",
        ):
            return
        reporter = _invocation_reporter.get()
        # Provider payloads belong in operator diagnostics, not client notifications.
        authentication_diagnostic = record.name.startswith(("azure.identity", "msal"))
        message = (
            "Authentication provider diagnostic omitted."
            if authentication_diagnostic
            else record.getMessage()
        )
        level = (
            "critical"
            if record.levelno >= logging.CRITICAL
            else "error"
            if record.levelno >= logging.ERROR
            else "warning"
            if record.levelno >= logging.WARNING
            else "info"
            if record.levelno >= logging.INFO
            else "debug"
        )
        telemetry._operator_log(
            level,
            getattr(record, "event", "python.log"),
            export=not record.name.startswith(("azure", "opentelemetry")),
            logger=record.name,
            message=record.getMessage(),
            **{
                key: record.__dict__[key]
                for key in ("actor", "reason", "statusCode", "errorType")
                if key in record.__dict__
            },
            **(
                telemetry._error_fields(record.exc_info[1])
                if record.exc_info and record.exc_info[1]
                else {}
            ),
        )
        if reporter is not None and not _reporting_output.get():
            reporter.log(level, message, record.name)
