"""Choose how callers prove they can use the Catalog.

Choose the authentication provider in server.py.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import sys
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from contextvars import ContextVar
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol

import jwt
from azure.core.credentials import AccessToken as AzureAccessToken
from azure.core.credentials import TokenCredential
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import ManagedIdentityCredential, OnBehalfOfCredential
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from .credentials import ReauthorizationRequired

_logger = logging.getLogger(__name__)
_http_request: ContextVar[Request] = ContextVar("mise_http_request")


class CatalogAuth(TokenVerifier, Protocol):
    """Check the token sent with an MCP request.

    Optionally provide credentials for the Tool to access other services as that
    caller.
    """

    settings: AuthSettings

    def delegated_credential(
        self, caller: AccessToken
    ) -> AbstractContextManager[TokenCredential] | None:
        """Return a credential that closes when the Tool call ends.

        Return None if this provider cannot supply one for the caller.
        """
        ...


class MiseAuth:
    """Verify Entra access tokens with MISE.

    Use Azure Identity to get tokens for other services on the caller's behalf.
    """

    def __init__(
        self,
        tenant_id: str,
        audience: str,
        permission: str,
        resource_url: str,
        *,
        client_id: str,
        client_secret: str | None = None,
        managed_identity_client_id: str | None = None,
    ) -> None:
        self.tenant_id = tenant_id.lower()
        self.audience = audience
        self.permission = permission
        self.client_id = client_id
        self.client_secret = client_secret
        self.managed_identity_client_id = managed_identity_client_id
        self.settings = AuthSettings(
            issuer_url=AnyHttpUrl(
                f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"
            ),
            resource_server_url=AnyHttpUrl(resource_url),
            required_scopes=[permission],
        )
        if sys.platform == "linux":
            import icukit_pyicu

            library_directory = Path(icukit_pyicu.get_lib())
            for name in ("libicudata.so", "libicuuc.so", "libicui18n.so"):
                ctypes.CDLL(str(library_directory / name), mode=ctypes.RTLD_GLOBAL)
        mise = import_module("mise")
        self.mise = mise.Mise()
        self.validation_input = mise.MiseValidationInput
        configuration = {
            "MiseVersion": "2.0",
            "AzureAd": {
                "Instance": "https://login.microsoftonline.com/",
                "TenantId": tenant_id.lower(),
                "ClientId": client_id,
                "Audiences": [audience],
                "ValidTenantIds": [tenant_id.lower()],
                "Protocols": {
                    "Bearer": {
                        "TokenTypes": {
                            "AccessToken": {"AppToken": True, "UserToken": True}
                        }
                    }
                },
            },
            "Mise": {
                "ClaimsOnlyAuthZModule": {
                    "AuthorizationConfig": {
                        "Policies": [
                            {
                                "Name": "mcp-users",
                                "IsDefaultPolicy": True,
                                "Profile": "NonAuthorizingUser",
                            },
                            {
                                "Name": "mcp-applications",
                                "IsDefaultPolicy": True,
                                "Profile": "NonAuthorizingApp",
                            },
                        ]
                    }
                },
                "TelemetryExporterOptions": {"EnableExportingData": True},
            },
        }
        with self.mise.configure(json.dumps(configuration), "AzureAd") as result:
            if result.error_code:
                raise RuntimeError("MISE configuration failed.")

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            validation_input = self.validation_input()
            validation_input.authorization_header = f"Bearer {token}"
            request = _http_request.get()
            validation_input.original_uri_header = str(request.url)
            validation_input.original_method_header = request.method
            with await asyncio.to_thread(
                self.mise.validate, validation_input
            ) as result:
                if result.http_response_status_code != 200:
                    _logger.warning(
                        "MISE token validation failed: %s",
                        result.http_response_status_code,
                    )
                    return None
                claims = jwt.decode(token, options={"verify_signature": False})

            client_id = claims.get("azp") or claims.get("appid")
            if not isinstance(client_id, str):
                _logger.warning("Authenticated token has no client identity")
                return None
            scopes = str(claims.get("scp", "")).split()
            roles = claims.get("roles", [])
            if isinstance(roles, list) and f"{self.permission}.Application" in roles:
                scopes.append(self.permission)
            return AccessToken(
                token=token,
                client_id=client_id,
                scopes=list(dict.fromkeys(scopes)),
                expires_at=claims["exp"],
                subject=claims.get("sub"),
                claims=claims,
            )
        except Exception as error:  # noqa: BLE001 - every verification failure is an invalid token
            _logger.warning("MISE token validation failed: %s", type(error).__name__)
            return None

    def delegated_credential(
        self, caller: AccessToken
    ) -> AbstractContextManager[TokenCredential] | None:
        claims = caller.claims or {}
        if not (
            isinstance(claims.get("tid"), str)
            and isinstance(claims.get("oid"), str)
            and isinstance(claims.get("scp"), str)
            and claims["scp"].strip()
        ):
            return None
        return self._open_obo(caller.token)

    @contextmanager
    def _open_obo(self, assertion: str) -> Generator[TokenCredential]:
        if self.client_id is None:
            raise ValueError(
                "Set MiseAuth.client_id before requesting a delegated credential."
            )
        with ExitStack() as stack:
            client_assertion_func: Callable[[], str] | None = None
            if not self.client_secret:
                managed_identity = stack.enter_context(
                    ManagedIdentityCredential(client_id=self.managed_identity_client_id)
                )

                def client_assertion() -> str:
                    return managed_identity.get_token(
                        "api://AzureADTokenExchange/.default"
                    ).token

                client_assertion_func = client_assertion
            credential = stack.enter_context(
                OnBehalfOfCredential(
                    tenant_id=self.tenant_id,
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                    client_assertion_func=client_assertion_func,
                    user_assertion=assertion,
                )
            )
            yield _EntraCredential(credential)


class _EntraCredential:
    def __init__(self, credential: OnBehalfOfCredential) -> None:
        self._credential = credential

    def get_token(self, *scopes: str, **kwargs: Any) -> AzureAccessToken:
        try:
            return self._credential.get_token(*scopes, **kwargs)
        except ClientAuthenticationError as error:
            detail: dict[str, object] = {}
            response = getattr(error, "response", None)
            if response is not None:
                try:
                    value = json.loads(response.text())
                    if isinstance(value, dict):
                        detail = value
                except AttributeError, TypeError, ValueError:
                    pass
            claims = detail.get("claims")
            codes = detail.get("error_codes", [])
            if not isinstance(codes, list):
                codes = []
            if (
                isinstance(claims, str)
                or detail.get("error")
                in {"interaction_required", "consent_required", "login_required"}
                or any(code in {50076, 50079, 50158, 65001} for code in codes)
            ):
                raise ReauthorizationRequired(
                    claims if isinstance(claims, str) else None
                ) from None
            raise RuntimeError(
                "ToolForge could not obtain a downstream token. Check downstream authorization configuration."
            ) from None


class MiseRequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        token = _http_request.set(Request(scope))
        try:
            await self.app(scope, receive, send)
        finally:
            _http_request.reset(token)
