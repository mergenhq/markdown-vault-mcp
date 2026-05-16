"""OAuth 2.1 + PKCE provider for vault-mcp.

Implements:
- Authorization Code grant with PKCE S256 (RFC 7636)
- /.well-known/oauth-authorization-server (RFC 8414)
- /.well-known/oauth-protected-resource (RFC 9728)
- /auth/authorize, /auth/token, /auth/revoke endpoints
- Dual-auth middleware: static Bearer OR OAuth JWT

Design:
- Self-contained OAuth AS (Authorization Server) and RS (Resource Server)
- In-memory token store (single-user, single-process)
- JWT access tokens (HS256), rotating refresh tokens
- PKCE S256 required (no plain allowed)
- Parallel: existing static Bearer remains valid (Bearer auth not broken)

Env vars consumed (all optional — OAuth disabled if OAUTH_JWT_SECRET absent):
  MARKDOWN_VAULT_MCP_OAUTH_JWT_SECRET  JWT signing secret (min 32 chars)
  MARKDOWN_VAULT_MCP_ISSUER_URL        AS issuer (default: https://vault.mergenops.com)
  MARKDOWN_VAULT_MCP_BEARER_TOKEN      Static bearer token (existing, kept)

MCP spec reference: https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

import jwt
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

logger = logging.getLogger(__name__)

_ACCESS_TOKEN_TTL = 3600          # 1 hour
_REFRESH_TOKEN_TTL = 30 * 86400   # 30 days
_AUTH_CODE_TTL = 60               # 60 seconds
_JWT_ALGORITHM = "HS256"


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def _verify_pkce_s256(code_verifier: str, code_challenge: str) -> bool:
    """Verify PKCE S256: SHA-256(verifier) == base64url(challenge)."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return computed == code_challenge


# ---------------------------------------------------------------------------
# In-memory token store
# ---------------------------------------------------------------------------

@dataclass
class _AuthCode:
    code_challenge: str
    redirect_uri: str
    scope: str
    client_id: str
    expires_at: float


@dataclass
class _RefreshToken:
    token: str
    subject: str
    scope: str
    expires_at: float


@dataclass
class _TokenStore:
    _codes: dict[str, _AuthCode] = field(default_factory=dict)
    _refresh: dict[str, _RefreshToken] = field(default_factory=dict)

    def save_code(self, code: str, entry: _AuthCode) -> None:
        self._codes[code] = entry

    def pop_code(self, code: str) -> _AuthCode | None:
        entry = self._codes.pop(code, None)
        if entry and entry.expires_at < time.time():
            return None
        return entry

    def save_refresh(self, rt: _RefreshToken) -> None:
        self._refresh[rt.token] = rt

    def pop_refresh(self, token: str) -> _RefreshToken | None:
        entry = self._refresh.pop(token, None)
        if entry and entry.expires_at < time.time():
            return None
        return entry

    def revoke_refresh(self, token: str) -> None:
        self._refresh.pop(token, None)


# ---------------------------------------------------------------------------
# HTML consent page
# ---------------------------------------------------------------------------

_CONSENT_HTML = """\
<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<title>Mergen Vault — Erişim İzni</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 480px; margin: 80px auto; padding: 24px; }}
  h1 {{ font-size: 1.4rem; }}
  .scope {{ background: #f4f4f4; padding: 12px; border-radius: 6px; margin: 16px 0; }}
  .btn {{ padding: 10px 24px; font-size: 1rem; border: none; border-radius: 6px; cursor: pointer; }}
  .allow {{ background: #2563eb; color: #fff; }}
  .deny {{ background: #e5e7eb; color: #374151; margin-left: 8px; }}
</style>
</head>
<body>
  <h1>Mergen Vault Erişim İzni</h1>
  <p><strong>{client_id}</strong> aşağıdaki izinleri istiyor:</p>
  <div class="scope">{scope}</div>
  <form method="POST" action="/auth/consent">
    <input type="hidden" name="code" value="{code}">
    <input type="hidden" name="state" value="{state}">
    <input type="hidden" name="redirect_uri" value="{redirect_uri}">
    <button class="btn allow" type="submit" name="decision" value="allow">İzin Ver</button>
    <button class="btn deny" type="submit" name="decision" value="deny">Reddet</button>
  </form>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# OAuthProvider
# ---------------------------------------------------------------------------

class OAuthProvider:
    """Self-contained OAuth 2.1 Authorization Server for vault-mcp.

    Args:
        jwt_secret: HS256 signing secret (min 32 chars). Required for JWT issuance.
        issuer_url: OAuth issuer URL (e.g. https://vault.mergenops.com).
        static_bearer_token: Existing static bearer token (kept for backwards compat).
        mcp_path: MCP endpoint path (e.g. /mcp).
    """

    def __init__(
        self,
        jwt_secret: str,
        issuer_url: str,
        static_bearer_token: str | None = None,
        mcp_path: str = "/mcp",
    ) -> None:
        if len(jwt_secret) < 32:
            raise ValueError("OAUTH_JWT_SECRET must be at least 32 characters")
        self._secret = jwt_secret
        self._issuer = issuer_url.rstrip("/")
        self._static_token = static_bearer_token
        self._mcp_url = f"{self._issuer}{mcp_path}"
        self._store = _TokenStore()
        logger.info(
            "OAuthProvider initialised: issuer=%s mcp=%s static_bearer=%s",
            self._issuer,
            self._mcp_url,
            "set" if self._static_token else "NOT SET",
        )

    # ------------------------------------------------------------------
    # JWT helpers
    # ------------------------------------------------------------------

    def _issue_access_token(self, subject: str, scope: str) -> str:
        now = int(time.time())
        payload = {
            "iss": self._issuer,
            "sub": subject,
            "aud": self._mcp_url,
            "iat": now,
            "exp": now + _ACCESS_TOKEN_TTL,
            "scope": scope,
        }
        return jwt.encode(payload, self._secret, algorithm=_JWT_ALGORITHM)

    def _issue_refresh_token(self, subject: str, scope: str) -> str:
        rt_value = secrets.token_urlsafe(64)
        rt = _RefreshToken(
            token=rt_value,
            subject=subject,
            scope=scope,
            expires_at=time.time() + _REFRESH_TOKEN_TTL,
        )
        self._store.save_refresh(rt)
        return rt_value

    def validate_jwt(self, token: str) -> dict[str, Any] | None:
        """Decode and validate a JWT access token. Returns claims or None."""
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[_JWT_ALGORITHM],
                audience=self._mcp_url,
                issuer=self._issuer,
            )
            return claims
        except jwt.ExpiredSignatureError:
            logger.debug("OAuth: JWT expired")
            return None
        except jwt.InvalidTokenError as exc:
            logger.debug("OAuth: JWT invalid: %s", exc)
            return None

    def is_static_bearer(self, token: str) -> bool:
        return bool(self._static_token and token == self._static_token)

    # ------------------------------------------------------------------
    # Well-known metadata (STEP 6)
    # ------------------------------------------------------------------

    @property
    def authorization_server_metadata(self) -> dict[str, Any]:
        """RFC 8414 Authorization Server Metadata."""
        return {
            "issuer": self._issuer,
            "authorization_endpoint": f"{self._issuer}/auth/authorize",
            "token_endpoint": f"{self._issuer}/auth/token",
            "revocation_endpoint": f"{self._issuer}/auth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["mcp:read", "mcp:tools"],
        }

    @property
    def protected_resource_metadata(self) -> dict[str, Any]:
        """RFC 9728 Protected Resource Metadata."""
        return {
            "resource": self._mcp_url,
            "authorization_servers": [self._issuer],
            "scopes_supported": ["mcp:read", "mcp:tools"],
            "bearer_methods_supported": ["header"],
        }

    def www_authenticate_header(self, error: str = "", description: str = "") -> str:
        """Build RFC 9728 Section 5.1 compliant WWW-Authenticate header."""
        parts = [
            "Bearer",
            f'resource_metadata="{self._issuer}/.well-known/oauth-protected-resource"',
        ]
        if error:
            parts.append(f'error="{error}"')
        if description:
            parts.append(f'error_description="{description}"')
        return ", ".join(parts)

    # ------------------------------------------------------------------
    # Authorization Code flow helpers (STEP 5)
    # ------------------------------------------------------------------

    def _issue_auth_code(
        self,
        code_challenge: str,
        redirect_uri: str,
        scope: str,
        client_id: str,
    ) -> str:
        code = secrets.token_urlsafe(32)
        self._store.save_code(
            code,
            _AuthCode(
                code_challenge=code_challenge,
                redirect_uri=redirect_uri,
                scope=scope,
                client_id=client_id,
                expires_at=time.time() + _AUTH_CODE_TTL,
            ),
        )
        return code

    # ------------------------------------------------------------------
    # Starlette route handlers
    # ------------------------------------------------------------------

    async def _well_known_as(self, request: Request) -> JSONResponse:
        """GET /.well-known/oauth-authorization-server"""
        return JSONResponse(
            self.authorization_server_metadata,
            headers={"Cache-Control": "no-store"},
        )

    async def _well_known_resource(self, request: Request) -> JSONResponse:
        """GET /.well-known/oauth-protected-resource"""
        return JSONResponse(
            self.protected_resource_metadata,
            headers={"Cache-Control": "no-store"},
        )

    async def _authorize(self, request: Request) -> Response:
        """GET /auth/authorize — show consent page or validate params."""
        p = request.query_params

        response_type = p.get("response_type", "")
        code_challenge = p.get("code_challenge", "")
        code_challenge_method = p.get("code_challenge_method", "")
        redirect_uri = p.get("redirect_uri", "")
        scope = p.get("scope", "mcp:read mcp:tools")
        state = p.get("state", "")
        client_id = p.get("client_id", "")

        # Validate required params
        if response_type != "code":
            return JSONResponse({"error": "unsupported_response_type"}, status_code=400)
        if not code_challenge:
            return JSONResponse({"error": "invalid_request", "error_description": "code_challenge required"}, status_code=400)
        if code_challenge_method != "S256":
            return JSONResponse({"error": "invalid_request", "error_description": "code_challenge_method must be S256"}, status_code=400)
        if not redirect_uri:
            return JSONResponse({"error": "invalid_request", "error_description": "redirect_uri required"}, status_code=400)

        # Issue auth code (pending consent)
        code = self._issue_auth_code(code_challenge, redirect_uri, scope, client_id)
        logger.info("OAuth: authorize request client=%s scope=%s state=%s", client_id, scope, state)

        # Show consent page
        html = _CONSENT_HTML.format(
            client_id=client_id or "claude.ai",
            scope=scope,
            code=code,
            state=state,
            redirect_uri=redirect_uri,
        )
        return HTMLResponse(html)

    async def _consent(self, request: Request) -> Response:
        """POST /auth/consent — process user consent decision."""
        form = await request.form()
        decision = form.get("decision", "deny")
        code = form.get("code", "")
        state = form.get("state", "")
        redirect_uri = form.get("redirect_uri", "")

        if decision != "allow":
            # User denied → redirect with error
            sep = "&" if "?" in redirect_uri else "?"
            return Response(
                status_code=302,
                headers={"Location": f"{redirect_uri}{sep}error=access_denied&state={state}"},
            )

        # Verify code is still valid
        entry = self._store.pop_code(code)
        if not entry:
            return JSONResponse({"error": "invalid_grant", "error_description": "Authorization code expired"}, status_code=400)

        # Re-issue code so it can be exchanged (we popped it above, reinsert)
        new_code = self._issue_auth_code(
            entry.code_challenge, entry.redirect_uri, entry.scope, entry.client_id
        )
        sep = "&" if "?" in redirect_uri else "?"
        location = f"{redirect_uri}{sep}code={new_code}&state={state}"
        logger.info("OAuth: consent ALLOW → redirect code=%s...", new_code[:8])
        return Response(status_code=302, headers={"Location": location})

    async def _token(self, request: Request) -> JSONResponse:
        """POST /auth/token — exchange code or refresh token."""
        try:
            body = await request.form()
        except Exception:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        grant_type = body.get("grant_type", "")

        if grant_type == "authorization_code":
            code = body.get("code", "")
            redirect_uri = body.get("redirect_uri", "")
            code_verifier = body.get("code_verifier", "")

            if not code or not code_verifier:
                return JSONResponse({"error": "invalid_request", "error_description": "code and code_verifier required"}, status_code=400)

            entry = self._store.pop_code(code)
            if not entry:
                logger.warning("OAuth: token exchange: code not found or expired")
                return JSONResponse({"error": "invalid_grant"}, status_code=400)

            if not _verify_pkce_s256(code_verifier, entry.code_challenge):
                logger.warning("OAuth: token exchange: PKCE verification failed")
                return JSONResponse({"error": "invalid_grant", "error_description": "PKCE verification failed"}, status_code=400)

            if entry.redirect_uri != redirect_uri:
                return JSONResponse({"error": "invalid_grant", "error_description": "redirect_uri mismatch"}, status_code=400)

            access_token = self._issue_access_token("mergen", entry.scope)
            refresh_token = self._issue_refresh_token("mergen", entry.scope)
            logger.info("OAuth: token issued scope=%s", entry.scope)
            return JSONResponse({
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "Bearer",
                "expires_in": _ACCESS_TOKEN_TTL,
                "scope": entry.scope,
            })

        elif grant_type == "refresh_token":
            refresh_token_val = body.get("refresh_token", "")
            if not refresh_token_val:
                return JSONResponse({"error": "invalid_request"}, status_code=400)

            rt = self._store.pop_refresh(refresh_token_val)
            if not rt:
                return JSONResponse({"error": "invalid_grant", "error_description": "Refresh token invalid or expired"}, status_code=400)

            # Rotation: old token consumed above, issue new pair
            access_token = self._issue_access_token(rt.subject, rt.scope)
            new_refresh = self._issue_refresh_token(rt.subject, rt.scope)
            logger.info("OAuth: refresh rotation for subject=%s", rt.subject)
            return JSONResponse({
                "access_token": access_token,
                "refresh_token": new_refresh,
                "token_type": "Bearer",
                "expires_in": _ACCESS_TOKEN_TTL,
                "scope": rt.scope,
            })

        else:
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    async def _revoke(self, request: Request) -> Response:
        """POST /auth/revoke — token revocation (RFC 7009)."""
        body = await request.form()
        token = body.get("token", "")
        self._store.revoke_refresh(token)
        return Response(status_code=200)

    # ------------------------------------------------------------------
    # ASGI app builders
    # ------------------------------------------------------------------

    def make_auth_app(self) -> Starlette:
        """Build Starlette app for /auth/* routes."""
        return Starlette(
            routes=[
                Route("/authorize", self._authorize, methods=["GET"]),
                Route("/consent", self._consent, methods=["POST"]),
                Route("/token", self._token, methods=["POST"]),
                Route("/revoke", self._revoke, methods=["POST"]),
            ]
        )

    def make_well_known_app(self) -> Starlette:
        """Build Starlette app for /.well-known/* routes."""
        return Starlette(
            routes=[
                Route("/oauth-authorization-server", self._well_known_as, methods=["GET"]),
                Route("/oauth-protected-resource", self._well_known_resource, methods=["GET"]),
            ]
        )


# ---------------------------------------------------------------------------
# Dual-auth ASGI middleware (STEP 7)
# ---------------------------------------------------------------------------

class DualAuthMiddleware:
    """ASGI middleware: accepts static Bearer OR OAuth JWT.

    If the token is neither → 401 with proper WWW-Authenticate.
    If the token is a valid JWT → rewrite Authorization header to static
    Bearer so downstream fastmcp_pvl_core auth sees a valid token.

    Args:
        app: The inner ASGI app (FastMCP http_app).
        provider: Configured OAuthProvider instance.
        static_bearer: The static bearer token value (same as pvl_core config).
        mcp_path_prefix: Only apply auth check to paths starting with this.
    """

    def __init__(
        self,
        app: Any,
        provider: OAuthProvider,
        static_bearer: str | None = None,
        mcp_path_prefix: str = "/mcp",
    ) -> None:
        self.app = app
        self.provider = provider
        self.static_bearer = static_bearer
        self.prefix = mcp_path_prefix

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path.startswith(self.prefix):
                result = self._check_auth(scope)
                if result == "deny":
                    await self._send_401(scope, receive, send)
                    return
                elif result == "jwt":
                    # Rewrite to static bearer so pvl_core sees it as valid
                    scope = dict(scope)
                    new_headers = []
                    for name, value in scope["headers"]:
                        if name.lower() == b"authorization" and self.static_bearer:
                            new_headers.append((b"authorization", f"Bearer {self.static_bearer}".encode()))
                        else:
                            new_headers.append((name, value))
                    scope["headers"] = new_headers
                # result == "static" → pass through unchanged

        await self.app(scope, receive, send)

    def _check_auth(self, scope: dict) -> str:
        """Returns 'static', 'jwt', or 'deny'."""
        headers = dict(scope.get("headers", []))
        auth = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
        if not auth.lower().startswith("bearer "):
            return "deny"
        token = auth[7:].strip()
        if self.provider.is_static_bearer(token):
            return "static"
        claims = self.provider.validate_jwt(token)
        if claims is not None:
            logger.debug("OAuth: JWT auth accepted sub=%s", claims.get("sub"))
            return "jwt"
        return "deny"

    async def _send_401(self, scope: dict, receive: Any, send: Any) -> None:
        www_auth = self.provider.www_authenticate_header(
            error="invalid_token",
            description="Valid Bearer or OAuth JWT required",
        )
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                [b"www-authenticate", www_auth.encode()],
                [b"content-type", b"application/json"],
                [b"content-length", b"53"],
            ],
        })
        await send({
            "type": "http.response.body",
            "body": b'{"error":"invalid_token","error_description":"Unauthorized"}',
        })


# ---------------------------------------------------------------------------
# Factory: build provider from environment
# ---------------------------------------------------------------------------

def build_oauth_provider(
    env_prefix: str = "MARKDOWN_VAULT_MCP",
    mcp_path: str = "/mcp",
) -> OAuthProvider | None:
    """Build OAuthProvider from environment variables.

    Returns None if OAUTH_JWT_SECRET is not set (OAuth disabled).
    """

    def _env(key: str) -> str | None:
        return os.environ.get(f"{env_prefix}_{key}")

    jwt_secret = (_env("OAUTH_JWT_SECRET") or "").strip()
    if not jwt_secret:
        logger.info("OAuth: OAUTH_JWT_SECRET not set — OAuth disabled")
        return None

    issuer_url = (_env("ISSUER_URL") or "https://vault.mergenops.com").strip()
    static_bearer = (_env("BEARER_TOKEN") or "").strip() or None

    try:
        return OAuthProvider(
            jwt_secret=jwt_secret,
            issuer_url=issuer_url,
            static_bearer_token=static_bearer,
            mcp_path=mcp_path,
        )
    except ValueError as exc:
        logger.error("OAuth: provider init failed: %s — OAuth disabled", exc)
        return None
