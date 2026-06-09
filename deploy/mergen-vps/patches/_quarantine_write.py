"""Faz 3 — Scoped Quarantine write-back (env-gated).

READ_ONLY=true tüm write araçlarını gizler. Bu module YALNIZCA `write`'ı
geri açar (edit/delete/rename/fetch gizli kalır), `write`'ı `inbox/wc/`
prefix'ine kısıtlar, boyut-cap uygular (#2) ve her kararı OAuth `login` ile
denetim loglar (#1/D5). OAuth/download-gate ile aynı patch felsefesi.
"""
from __future__ import annotations

import logging
import os
import posixpath

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware

logger = logging.getLogger("markdown_vault_mcp.quarantine_write")

ENV_PREFIX = "MARKDOWN_VAULT_MCP_WRITE_QUARANTINE_PREFIX"
ENV_MAX_BYTES = "MARKDOWN_VAULT_MCP_WRITE_MAX_BYTES"
DEFAULT_MAX_BYTES = 262144  # 256 KiB (#2 yumuşak limit)


def _normalize_prefix(raw: str) -> str:
    # "/inbox/wc/" | "inbox/wc" -> "inbox/wc/"
    return raw.strip().strip("/") + "/"


def _audit_login() -> str | None:
    """OAuth `login` (D5). Patch ortamı/request dışında sessizce None döner."""
    try:
        from fastmcp.server.dependencies import get_access_token
        from markdown_vault_mcp._oauth_github_spike import extract_login
        token = get_access_token()
        return extract_login(token) if token is not None else None
    except Exception:
        return None


def _get_collection():
    """Lifespan'deki Collection'a ulas (get_collection ile ayni yol). Yoksa None."""
    try:
        from fastmcp.server.dependencies import get_context
        return get_context().lifespan_context.get("collection")
    except Exception:
        return None


def _deindex_quarantine(coll, dm, path: str) -> None:
    """D3: quarantine dosyasini diskte birak ama search + link-graf + vector'den cikar.

    `write` dosyayi aninda FTS'e (+links) yazar ve vektor embedding'ini dirty
    kuyruguna alir; bu, onaylanmamis taslagin aramaya/link-graf'a sizmasina yol
    acar. Burada sadece-saglanan silme API'leriyle geri aliyoruz (dosya kalir).
    """
    try:
        fts = getattr(dm, "_fts", None) if dm is not None else None
        if fts is not None and hasattr(fts, "delete_by_path"):
            fts.delete_by_path(path)  # FTS arama + links (cascade)
    except Exception:
        logger.warning("quarantine: fts deindex failed for %r", path, exc_info=True)
    try:
        im = getattr(coll, "_index_mgr", None)
        if im is not None and hasattr(im, "remove_from_dirty"):
            im.remove_from_dirty(path)  # ertelenmis embedding kuyrugundan cikar
    except Exception:
        logger.warning("quarantine: dirty-unqueue failed for %r", path, exc_info=True)
    try:
        vec = getattr(coll, "_vectors", None)
        if vec is not None and hasattr(vec, "delete_by_path"):
            vec.delete_by_path(path)  # embed olduysa vektorden de cikar
    except Exception:
        logger.warning("quarantine: vector deindex failed for %r", path, exc_info=True)


class QuarantineWriteMiddleware(Middleware):
    """`write`'a yalnız `path` prefix altında + boyut sınırında izin verir; her kararı loglar."""

    def __init__(self, prefix: str, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.prefix = _normalize_prefix(prefix)
        self.max_bytes = max_bytes

    async def on_call_tool(self, context, call_next):
        params = context.message
        if params.name != "write":
            return await call_next(context)
        args = params.arguments or {}
        raw_path = str(args.get("path", ""))
        norm = posixpath.normpath(raw_path)
        login = _audit_login()
        outside = (
            raw_path == ""
            or norm.startswith("/")
            or norm == ".."
            or norm.startswith("../")
            or not (norm + "/").startswith(self.prefix)
        )
        if outside:
            logger.warning(
                "quarantine AUDIT DENY tool=write login=%r path=%r norm=%r reason=scope",
                login, raw_path, norm,
            )
            raise ToolError(
                f"write denied: path must be under '{self.prefix}' (got {raw_path!r})"
            )
        size = len((args.get("content") or "").encode("utf-8")) + len(args.get("content_base64") or "")
        if size > self.max_bytes:
            logger.warning(
                "quarantine AUDIT DENY tool=write login=%r path=%r size=%d reason=too_large",
                login, norm, size,
            )
            raise ToolError(
                f"write denied: payload {size}B exceeds limit {self.max_bytes}B"
            )
        logger.info(
            "quarantine AUDIT ALLOW tool=write login=%r path=%r size=%d", login, norm, size
        )
        # Collection READ_ONLY=true ile kurulur; yalniz bu DOGRULANMIS write SIRASINDA
        # yazmayi ac, finally ile hemen geri kilitle (edit/delete/rename/fetch hic acilmaz).
        coll = _get_collection()
        if coll is None:
            return await call_next(context)
        dm = getattr(coll, "_doc_mgr", None)
        prev_c = getattr(coll, "_read_only", None)
        prev_d = getattr(dm, "_read_only", None) if dm is not None else None
        coll._read_only = False
        if dm is not None:
            dm._read_only = False
        try:
            result = await call_next(context)
            # D3: dosya inbox/wc/'de kalir ama search/link-graf/vector'den cikarilir
            _deindex_quarantine(coll, dm, norm)
            return result
        finally:
            if prev_c is not None:
                coll._read_only = prev_c
            if dm is not None and prev_d is not None:
                dm._read_only = prev_d


def install_quarantine_write(mcp) -> None:
    """YALNIZ `write`'ı geri aç + scope/size/audit gate'i tak. Env-gated çağrılır."""
    prefix = os.environ[ENV_PREFIX]
    max_bytes = int(os.environ.get(ENV_MAX_BYTES, DEFAULT_MAX_BYTES))
    mcp.enable(names={"write"})
    mcp.add_middleware(QuarantineWriteMiddleware(prefix, max_bytes))
    logger.info(
        "quarantine write-back AKTİF: yalnız `write` re-enabled, scope=%r, max=%dB",
        _normalize_prefix(prefix), max_bytes,
    )
