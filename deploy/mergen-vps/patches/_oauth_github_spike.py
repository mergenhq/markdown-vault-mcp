"""Temporary spike module (Phase 0): GitHub OAuth + mergenhq allowlist + MultiAuth.

Connects the claude.ai web connector via OAuth by using ``GitHubProvider``
(an ``OAuthProxy``); delegates login to GitHub; enforces a ``mergenhq``
allowlist; and preserves the CLI bearer channel via ``MultiAuth``. To be made
permanent in Phase 1 or moved upstream into ``fastmcp-pvl-core``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastmcp.server.auth import MultiAuth, StaticTokenVerifier
from fastmcp.server.auth.providers.github import GitHubProvider
from key_value.aio.stores.filetree import FileTreeStore

logger = logging.getLogger(__name__)

# Persistent store for DCR client registrations + tokens so the claude.ai
# connection survives container restarts (vault restarts on every reindex).
# Lives on the mounted /data volume; overridable for tests.
_CLIENT_STORAGE_DIR = os.environ.get(
    "MARKDOWN_VAULT_MCP_OAUTH_CLIENT_STORAGE_DIR", "/data/oauth-clients"
)

# The only GitHub identity allowed to reach the vault over OAuth.
ALLOWLIST: set[str] = {"mergenhq"}


def is_login_allowed(login: str | None, allowlist: set[str]) -> bool:
    """Return whether a GitHub login is in the allowlist (case-insensitive).

    Args:
        login: GitHub username/login (e.g. ``"mergenhq"``) or ``None``.
        allowlist: Permitted logins (e.g. ``{"mergenhq"}``).

    Returns:
        ``True`` when ``login`` is in the allowlist, else ``False``.
        Empty/``None`` login returns ``False``.
    """
    if not login:
        return False
    return login.strip().lower() in {a.lower() for a in allowlist}


def extract_login(access_token: Any) -> str | None:
    """Extract the GitHub login from an ``AccessToken``.

    Evidence (fastmcp 3.2.4 source introspection): ``GitHubTokenVerifier``
    stores the login in ``AccessToken.claims["login"]`` and
    ``load_access_token`` returns the validated token with claims preserved.
    This helper makes that contract unit-testable.
    """
    claims = getattr(access_token, "claims", None) or {}
    login = claims.get("login")
    return login if isinstance(login, str) else None


class MergenGitHubProvider(GitHubProvider):
    """``GitHubProvider`` that enforces the ``mergenhq`` allowlist.

    After GitHub token-swap validation, checks ``claims["login"]`` against the
    allowlist; a non-allowlisted login yields token rejection (``None``). This
    gate is mandatory because the vault holds sensitive financial/security
    content.
    """

    async def verify_token(self, token: str):  # type: ignore[override]
        result = await super().verify_token(token)
        if result is None:
            return None
        login = extract_login(result)
        if not is_login_allowed(login, ALLOWLIST):
            logger.warning("oauth_denied login=%r (not in allowlist)", login)
            return None
        return result


def build_github_auth() -> Any:
    """Build the spike auth provider: GitHub OAuth + optional CLI bearer.

    Required env: ``MARKDOWN_VAULT_MCP_GITHUB_CLIENT_ID`` /
    ``..._GITHUB_CLIENT_SECRET`` / ``..._BASE_URL``.
    Optional env: when ``..._BEARER_TOKEN`` is set, the CLI bearer channel is
    preserved alongside OAuth via ``MultiAuth``.
    """
    cid = os.environ["MARKDOWN_VAULT_MCP_GITHUB_CLIENT_ID"]
    csec = os.environ["MARKDOWN_VAULT_MCP_GITHUB_CLIENT_SECRET"]
    base = os.environ["MARKDOWN_VAULT_MCP_BASE_URL"].rstrip("/")
    store = FileTreeStore(data_directory=_CLIENT_STORAGE_DIR)
    gh = MergenGitHubProvider(
        client_id=cid, client_secret=csec, base_url=base, client_storage=store
    )

    bearer = os.environ.get("MARKDOWN_VAULT_MCP_BEARER_TOKEN", "").strip()
    if bearer:
        # client_id is REQUIRED by StaticTokenVerifier (AccessToken(client_id=...));
        # "user" scope mirrors the GitHub provider's required_scopes so the bearer
        # passes MultiAuth's inherited scope gate.
        verifier = StaticTokenVerifier(
            {bearer: {"client_id": "cli", "scopes": ["user"]}}
        )
        logger.info("github_auth: MultiAuth (GitHub OAuth + CLI bearer preserved)")
        return MultiAuth(server=gh, verifiers=[verifier])
    logger.info("github_auth: GitHub OAuth only (no bearer set)")
    return gh
