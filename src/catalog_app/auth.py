"""The bearer provider selected by server.py; implementations own their claims."""

from contextlib import AbstractContextManager
from typing import Literal, NotRequired, Protocol, TypedDict

from azure.core.credentials import TokenCredential
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings


class CallerIdentity(TypedDict):
    """Safe attribution from a verified token, without assertions or raw claims."""

    principalType: Literal["user", "application"]
    userId: NotRequired[str]
    userName: NotRequired[str]
    userEmail: NotRequired[str]
    entraTenantId: NotRequired[str]
    entraObjectId: NotRequired[str]


class CatalogAuth(TokenVerifier, Protocol):
    """Verify native SDK tokens, project identity, and optionally delegate access."""

    settings: AuthSettings

    def caller_identity(self, caller: AccessToken) -> CallerIdentity:
        """Classify the verified caller explicitly; a subject alone is not a user."""
        ...

    def delegated_credential(
        self, caller: AccessToken
    ) -> AbstractContextManager[TokenCredential] | None:
        """Return a credential context for this call, or None without delegation."""
        ...
