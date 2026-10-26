from __future__ import annotations

import json
import logging
import os
import sys
import threading
import traceback
import warnings
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, TypedDict

_log_context: ContextVar[dict[str, str]] = ContextVar("log_context", default={})
_console_stream = sys.stderr
_console_level = logging.INFO
_telemetry_enabled = False
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
_ErrorFields = TypedDict(
    "_ErrorFields",
    {
        "exception.type": str,
        "exception.message": str,
        "exception.stacktrace": str,
    },
    total=False,
)


def _error_fields(error: BaseException | None) -> _ErrorFields:
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
    entry = {
        "timestamp": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "level": "warn" if level == "warning" else level,
        "actor": "system",
        **_log_context.get(),
        "event": event,
        **fields,
        "service": "mcp",
        "pid": os.getpid(),
    }
    if _telemetry_enabled:
        from opentelemetry import trace

        span = trace.get_current_span().get_span_context()
        if span.is_valid:
            entry.update(traceId=f"{span.trace_id:032x}", spanId=f"{span.span_id:016x}")
    _console_stream.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")
    _console_stream.flush()
    if _telemetry_enabled and export:
        body = entry.get("message", entry.get("exception.message", event))
        logging.getLogger("toolforge.operator").log(
            severity,
            body,
            extra={key: value for key, value in entry.items() if key != "message"},
        )


def configure() -> None:
    global _console_level, _telemetry_enabled

    from .invocation import InvocationLogHandler, InvocationOutput

    level = os.environ.pop("TOOLFORGE_LOG_LEVEL", "info").lower()
    if level not in {"debug", "info", "warn", "warning", "error"}:
        raise ValueError("TOOLFORGE_LOG_LEVEL must be debug, info, warning, or error")
    _console_level = _log_levels[level]
    logging.getLogger().handlers = [InvocationLogHandler()]
    logging.getLogger().setLevel(_console_level)

    def format_warning(
        message: Warning | str,
        category: type[Warning],
        filename: str,
        lineno: int,
        line: str | None = None,
    ) -> str:
        return f"{filename}:{lineno}: {category.__name__}: {message}\n"

    # ty rejects direct reassignment of this hook (astral-sh/ty#3962).
    setattr(warnings, "formatwarning", format_warning)
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
    if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        from azure.monitor.opentelemetry import configure_azure_monitor
        from opentelemetry.sdk.resources import Resource

        configure_azure_monitor(
            logger_name="toolforge.operator",
            resource=Resource.create({"service.name": "mcp"}),
            span_processors=[],
            instrumentation_options={
                name: {"enabled": False}
                for name in ("azure_sdk", "psycopg2", "django", "flask", "fastapi")
            },
            enable_live_metrics=False,
        )
        _telemetry_enabled = True
