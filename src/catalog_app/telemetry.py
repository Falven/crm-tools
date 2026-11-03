from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import sys
import threading
import traceback
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import anyio

_log_context: ContextVar[dict[str, str]] = ContextVar("log_context", default={})
_reporting_output: ContextVar[bool] = ContextVar("reporting_output", default=False)
_invocation_error: ContextVar[BaseException | None] = ContextVar(
    "invocation_error", default=None
)
_console_stream = sys.stderr
_console_level = logging.INFO
_telemetry_enabled = False
_resource = None
_process_context: dict[str, Any] = {}
_log_levels = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "notice": logging.INFO,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "critical": logging.CRITICAL,
    "alert": logging.CRITICAL,
    "emergency": logging.CRITICAL,
}


def _error_fields(error: BaseException | None) -> dict[str, str]:
    if error is None:
        return {}
    stack = traceback.TracebackException.from_exception(
        error,
        limit=sys.maxsize,
        capture_locals=True,
        compact=True,
        max_group_width=sys.maxsize,
        max_group_depth=sys.maxsize,
    )
    cause = stack
    while cause.__cause__ is not None:
        error = error.__cause__
        cause = cause.__cause__
    return {
        "exception.type": type(error).__name__,
        "exception.message": str(error),
        "exception.stacktrace": "".join(stack.format()).rstrip(),
    }


def _operator_log(
    level: str, event: str, *, export: bool = True, **fields: Any
) -> None:
    severity = _log_levels[level]
    if severity < _console_level:
        return
    timestamp = datetime.now(timezone.utc)
    entry = {
        "timestamp": timestamp.isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        ),
        "level": "warn" if level == "warning" else level,
        "actor": "system",
        **_process_context,
        **_log_context.get(),
        "event": event,
        **fields,
        "service": "mcp",
        "pid": os.getpid(),
    }
    if _resource is not None:
        entry["resource"] = {
            name: value
            for name in ("service.name", "service.instance.id", "service.namespace")
            if (value := _resource.attributes.get(name)) is not None
        }
    if _telemetry_enabled:
        from opentelemetry import context, trace

        otel_context = context.get_current()
        span = trace.get_current_span(otel_context).get_span_context()
        if span.is_valid:
            entry.update(traceId=f"{span.trace_id:032x}", spanId=f"{span.span_id:016x}")
    _console_stream.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")
    _console_stream.flush()
    if _telemetry_enabled and export:
        try:
            from opentelemetry._logs import SeverityNumber, get_logger

            exception = (
                entry.get("exception.type") is not None
                or entry.get("exception.message") is not None
            )
            # Azure otherwise replaces the exception message with the log body.
            body = None
            if not exception:
                body = entry.get("message")
                if body is None or body == "":
                    body = next(
                        (
                            entry[key]
                            for key in ("text", "error")
                            if isinstance(entry.get(key), str) and entry[key]
                        ),
                        entry["event"],
                    )
            attributes = {
                key: value
                for key, value in entry.items()
                if key not in {"timestamp", "resource"}
                and (key != "level" or value in {"notice", "alert", "emergency"})
            }
            native_message = entry.get("exception.message") if exception else body
            if not exception and not isinstance(body, str):
                body = json.dumps(body, ensure_ascii=False)
            if exception:
                native_text = str(native_message)[:32768] if native_message else ""
            else:
                native_text = body[:32768].strip()
            if native_text and (
                attributes.get("message") == native_text
                or (
                    not exception
                    and not isinstance(native_message, str)
                    and attributes.get("message") == native_message
                    and body == native_text
                )
            ):
                attributes.pop("message", None)
            for key in ("text", "error"):
                if (
                    native_text
                    and isinstance(attributes.get(key), str)
                    and attributes[key] == native_text
                ):
                    del attributes[key]
            if exception and isinstance(attributes.get("message"), (dict, list)):
                # OTel attributes cannot preserve arbitrary mixed-type JSON arrays.
                attributes["message"] = json.dumps(
                    attributes["message"], ensure_ascii=False
                )
            if not exception and native_text and attributes["event"] == native_text:
                del attributes["event"]
            if span.is_valid:
                for key, value in (
                    ("traceId", f"{span.trace_id:032x}"),
                    ("spanId", f"{span.span_id:016x}"),
                ):
                    if attributes.get(key) == value:
                        del attributes[key]
            logger = get_logger("toolforge.operator")
            resource = getattr(logger, "resource", None)
            if (
                resource is not None
                and resource.attributes.get("service.name") == entry["service"]
                and not resource.attributes.get("service.namespace")
            ):
                del attributes["service"]
            logger.emit(
                timestamp=int(timestamp.timestamp() * 1_000_000_000),
                context=otel_context,
                severity_number={
                    logging.DEBUG: SeverityNumber.DEBUG,
                    logging.INFO: SeverityNumber.INFO,
                    logging.WARNING: SeverityNumber.WARN,
                    logging.ERROR: SeverityNumber.ERROR,
                    logging.CRITICAL: SeverityNumber.FATAL,
                }[severity],
                severity_text=entry["level"].upper(),
                body=body,
                attributes=attributes,
            )
        except Exception as error:
            _operator_log(
                "error", "telemetry.export.failed", export=False, **_error_fields(error)
            )


def configure() -> None:
    global _console_level, _telemetry_enabled, _resource

    level = os.environ.pop("TOOLFORGE_LOG_LEVEL", "info").lower()
    if level not in {"debug", "info", "warn", "warning", "error"}:
        raise ValueError("TOOLFORGE_LOG_LEVEL must be debug, info, warning, or error")
    _console_level = _log_levels[level]
    logging.getLogger().handlers = [InvocationLogHandler()]
    logging.getLogger().setLevel(_console_level)

    logging.captureWarnings(True)
    for name in ("mcp", "uvicorn", "azure", "opentelemetry"):
        logging.getLogger(name).setLevel(_console_level)
    telemetry_logger = logging.getLogger("toolforge.operator")
    telemetry_logger.propagate = False
    telemetry_logger.setLevel(_console_level)
    sys.stdout = InvocationOutput(sys.stdout, "info", "stdout")
    sys.stderr = InvocationOutput(sys.stderr, "error", "stderr")
    sys.excepthook = lambda _kind, error, _tb: _operator_log(
        "error", "runtime.failed", **_error_fields(error)
    )
    threading.excepthook = lambda args: _operator_log(
        "error", "thread.failed", **_error_fields(args.exc_value)
    )
    sys.unraisablehook = lambda args: _operator_log(
        "error", "runtime.unraisable", **_error_fields(args.exc_value)
    )
    from opentelemetry.sdk.resources import OTELResourceDetector, Resource

    # Match the Azure distro resource even when export is disabled.
    os.environ.setdefault(
        "OTEL_EXPERIMENTAL_RESOURCE_DETECTORS", "azure_app_service,azure_vm"
    )
    _resource = Resource.create(
        {"service.name": "mcp"} | dict(OTELResourceDetector().detect().attributes)
    )
    if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        from azure.monitor.opentelemetry import configure_azure_monitor
        from opentelemetry._logs import get_logger

        configure_azure_monitor(
            logger_name="toolforge.operator",
            resource=_resource,
            span_processors=[],
            instrumentation_options={
                name: {"enabled": False}
                for name in ("azure_sdk", "psycopg2", "django", "flask", "fastapi")
            },
            enable_live_metrics=False,
        )
        _resource = get_logger("toolforge.operator").resource
        _telemetry_enabled = True


class InvocationReporter:
    def __init__(self, context: Any, loop: asyncio.AbstractEventLoop) -> None:
        self.context = context
        self.loop = loop
        self.pending: concurrent.futures.Future[None] | None = None
        self.lock = threading.Lock()
        self.aborted = False
        self.active: set[asyncio.Task[None]] = set()

    def enqueue(
        self, output: Callable[[], Awaitable[None]]
    ) -> concurrent.futures.Future[None]:
        with self.lock:
            pending = asyncio.run_coroutine_threadsafe(
                self._send_after(self.pending, output), self.loop
            )
            self.pending = pending
            return pending

    async def _send_after(
        self,
        previous: concurrent.futures.Future[None] | None,
        output: Callable[[], Awaitable[None]],
    ) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.active.add(task)
        try:
            if previous is not None:
                await asyncio.shield(asyncio.wrap_future(previous))
            if not self.aborted:
                with _reporting_output.set(True):
                    await output()
        except asyncio.CancelledError:
            if not self.aborted:
                raise
            # Keep a canceled successor behind its predecessor's async cleanup.
            if previous is not None:
                await asyncio.shield(asyncio.wrap_future(previous))
        finally:
            self.active.remove(task)

    def abort(self) -> None:
        if self.aborted:
            return
        self.aborted = True
        for task in tuple(self.active):
            task.cancel()

    async def wait(self, pending: concurrent.futures.Future[None]) -> None:
        await asyncio.shield(asyncio.wrap_future(pending))

    async def drain(self) -> None:
        with self.lock:
            pending = self.pending
            self.pending = None
        if pending is not None:
            try:
                await self.wait(pending)
            except asyncio.CancelledError:
                self.abort()
                with anyio.CancelScope(shield=True):
                    await self.wait(pending)
                raise

    def log(self, level: str, data: str, logger_name: str) -> None:
        self.enqueue(lambda: self.context.log(level, data, logger_name=logger_name))


_invocation_reporter: ContextVar[InvocationReporter | None] = ContextVar(
    "invocation_reporter", default=None
)


class InvocationOutput:
    def __init__(self, fallback: Any, level: str, logger_name: str) -> None:
        self.fallback = fallback
        self.level = level
        self.logger_name = logger_name

    def write(self, data: str) -> int:
        reporter = _invocation_reporter.get()
        if data:
            _operator_log(
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
        _operator_log(
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
                _error_fields(record.exc_info[1])
                if record.exc_info and record.exc_info[1]
                else {}
            ),
        )
        if reporter is not None and not _reporting_output.get():
            reporter.log(level, message, record.name)
