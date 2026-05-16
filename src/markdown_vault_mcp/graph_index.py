"""LightRAG knowledge graph integration for vault-mcp.

Provides async wrapper around LightRAG for entity/relation extraction
and graph-based retrieval. Uses Anthropic Sonnet + NanoVectorDB + NetworkX.

Storage: /data/lightrag/ (docker volume, persisted separately from index.db)

Env vars:
  MARKDOWN_VAULT_MCP_GRAPH_ENABLED    - "true" to enable (default: false)
  MARKDOWN_VAULT_MCP_GRAPH_DIR        - working dir (default: /data/lightrag)
  ANTHROPIC_API_KEY                   - required for LLM extraction
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from markdown_vault_mcp.graph_prompts import ENTITY_TYPES, USER_PROMPT

logger = logging.getLogger(__name__)

_ENV_PREFIX = "MARKDOWN_VAULT_MCP"
_DEFAULT_GRAPH_DIR = "/data/lightrag"
_DEFAULT_LLM_MODEL = "claude-sonnet-4-6"
_GRAPH_QUERY_MODE = "hybrid"  # combines local entity + global relation search


def _env(key: str) -> str | None:
    return os.environ.get(f"{_ENV_PREFIX}_{key}")


def is_graph_enabled() -> bool:
    return (_env("GRAPH_ENABLED") or "").lower() in ("1", "true", "yes")


def get_graph_dir() -> Path:
    raw = (_env("GRAPH_DIR") or _DEFAULT_GRAPH_DIR).strip()
    return Path(raw)


class GraphIndex:
    """LightRAG wrapper for vault-mcp.

    Lazy-initialized — only loads LightRAG if GRAPH_ENABLED=true and
    the working_dir exists (graph has been built).

    Args:
        working_dir: Path to LightRAG working directory.
        api_key: Anthropic API key.
        llm_model: LLM model name for extraction.
    """

    def __init__(
        self,
        working_dir: Path | None = None,
        api_key: str | None = None,
        llm_model: str = _DEFAULT_LLM_MODEL,
    ) -> None:
        self._working_dir = working_dir or get_graph_dir()
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._llm_model = llm_model
        self._rag: Any = None
        self._ready = False

    def _try_init(self) -> bool:
        """Attempt to initialize LightRAG. Returns True if ready."""
        if self._ready:
            return True
        if not self._working_dir.exists():
            logger.debug("graph_index: working_dir %s not found — graph not built yet", self._working_dir)
            return False
        try:
            from lightrag import LightRAG
            from lightrag.llm.anthropic import anthropic_complete
            from lightrag.utils import EmbeddingFunc

            # Use LightRAG's default NanoVectorDB embedding
            # (separate from vault-mcp's e5-large for FTS)
            from lightrag.llm.openai import openai_embedding  # fallback

            async def _anthropic_llm(prompt: str, system_prompt: str | None = None, **kw: Any) -> str:
                return await anthropic_complete(
                    prompt,
                    system_prompt=system_prompt,
                    hashing_kv=kw.get("hashing_kv"),
                    llm_model_name=self._llm_model,
                    api_key=self._api_key,
                    **{k: v for k, v in kw.items() if k not in ("hashing_kv",)},
                )

            self._rag = LightRAG(
                working_dir=str(self._working_dir),
                llm_model_func=_anthropic_llm,
                addon_params={
                    "language": "Turkish",
                    "entity_types": ENTITY_TYPES,
                    "user_prompt": USER_PROMPT,
                },
            )
            self._ready = True
            logger.info("graph_index: LightRAG initialised from %s", self._working_dir)
            return True
        except ImportError as exc:
            logger.warning("graph_index: lightrag not installed: %s", exc)
            return False
        except Exception as exc:
            logger.warning("graph_index: init failed: %s", exc)
            return False

    @property
    def is_ready(self) -> bool:
        return self._ready or self._try_init()

    async def query(
        self,
        query: str,
        mode: str = _GRAPH_QUERY_MODE,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Query the knowledge graph.

        Args:
            query: Natural language query.
            mode: LightRAG mode (hybrid/local/global/mix/naive).
            top_k: Max results.

        Returns:
            List of result dicts with keys: text, path, score, entities.
        """
        if not self.is_ready:
            return []
        try:
            from lightrag.base import QueryParam
            result = await self._rag.aquery(
                query,
                param=QueryParam(mode=mode, top_k=top_k, response_type="Multiple Paragraphs"),
            )
            if not result:
                return []
            return [{"text": result, "path": "__graph__", "score": 1.0, "entities": []}]
        except Exception as exc:
            logger.warning("graph_index: query failed: %s", exc)
            return []

    async def entity_lookup(self, entity_name: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Look up an entity and its related chunks.

        Args:
            entity_name: Entity name to search for.
            top_k: Max results.

        Returns:
            List of result dicts.
        """
        return await self.query(entity_name, mode="local", top_k=top_k)

    async def multi_hop(self, query: str, hops: int = 2, top_k: int = 5) -> list[dict[str, Any]]:
        """Multi-hop traversal via LightRAG global mode.

        Args:
            query: Query describing entities and their relationship.
            hops: Ignored (LightRAG handles depth internally via global mode).
            top_k: Max results.

        Returns:
            List of result dicts from multi-hop traversal.
        """
        return await self.query(query, mode="global", top_k=top_k)


# Module-level singleton — initialized lazily when graph_tools are called
_graph_index: GraphIndex | None = None


def get_graph_index() -> GraphIndex:
    global _graph_index
    if _graph_index is None:
        _graph_index = GraphIndex()
    return _graph_index
