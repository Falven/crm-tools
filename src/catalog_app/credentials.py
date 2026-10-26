"""Manage credentials that let Tools access other services as the caller.

Close them when the Tool call ends and tell the client if sign-in is needed.
"""

from __future__ import annotations

from collections.abc import Awaitable
from contextlib import ExitStack
from contextvars import ContextVar
from threading import RLock
from typing import TYPE_CHECKING, Any

from azure.core.credentials import AccessToken as AzureAccessToken
from azure.core.credentials import TokenCredential
from mcp.server.auth.provider import AccessToken as MCPAccessToken
from mcp_types import CallToolResult, InputRequiredResult, TextContent

if TYPE_CHECKING:
    from .auth import CatalogAuth


class ReauthorizationRequired(Exception):
    def __init__(self, claims: str | None = None) -> None:
        super().__init__("The caller must sign in again before this tool can continue.")
        self.claims = claims


class _Invocation:
    def __init__(self, auth: CatalogAuth, caller: MCPAccessToken | None) -> None:
        self._auth = auth
        self._caller = caller
        self._stack = ExitStack()
        self._lock = RLock()
        self._credential: TokenCredential | None = None
        self._closed = False

    def credential(self) -> TokenCredential:
        with self._lock:
            if self._closed:
                raise RuntimeError("The delegated Tool credential is no longer active.")
            if self._credential is None:
                manager = (
                    self._auth.delegated_credential(self._caller)
                    if self._caller is not None
                    else None
                )
                if manager is None:
                    raise RuntimeError(
                        "A delegated MCP caller is required for this credential."
                    )
                self._credential = self._stack.enter_context(manager)
            return self

    def get_token(self, *scopes: str, **kwargs: Any) -> AzureAccessToken:
        with self._lock:
            if self._closed:
                raise RuntimeError("The delegated Tool credential is no longer active.")
            credential = self._credential
        if credential is None:
            raise RuntimeError(
                "A delegated MCP caller is required for this credential."
            )
        return credential.get_token(*scopes, **kwargs)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._stack.close()


_caller: ContextVar[_Invocation | None] = ContextVar("catalog_caller", default=None)


def get_caller_credential() -> TokenCredential:
    """Return a credential for accessing other services as the caller.

    It works only during this Tool call.
    """
    invocation = _caller.get()
    if invocation is None:
        raise RuntimeError("A delegated MCP caller is required for this credential.")
    return invocation.credential()


async def run_with_caller(
    auth: CatalogAuth,
    caller: MCPAccessToken | None,
    tool_call: Awaitable[CallToolResult | InputRequiredResult],
) -> CallToolResult | InputRequiredResult:
    """Run the Tool with the caller's identity and close its credential afterward.

    Tell the client if the caller needs to sign in again.
    """
    invocation = _Invocation(auth, caller)
    token = _caller.set(invocation)
    try:
        return await tool_call
    except Exception as error:
        # A sign-in error may be inside another Tool error.
        current: BaseException | None = error
        while current is not None:
            if isinstance(current, ReauthorizationRequired):
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text="The caller must sign in again before this tool can continue.",
                        )
                    ],
                    is_error=True,
                    _meta={
                        "toolforge/reauthorization": {
                            **({"claims": current.claims} if current.claims else {})
                        }
                    },
                )
            current = current.__cause__ or current.__context__
        raise
    finally:
        try:
            invocation.close()
        finally:
            _caller.reset(token)
