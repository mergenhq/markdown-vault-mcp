"""Contextual chunk enrichment via Claude Haiku for improved FTS retrieval.

Based on Anthropic's contextual retrieval technique (September 2024):
each chunk is enriched with a short document-level context before FTS indexing,
improving retrieval accuracy (~69% error reduction per Anthropic).

Ref: https://www.anthropic.com/news/contextual-retrieval
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from markdown_vault_mcp.scanner import ParsedNote
    from markdown_vault_mcp.types import Chunk

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_DOC_TOKENS = 2000   # doc summary length fed to Haiku
_MAX_CHUNK_TOKENS = 800  # chunk content fed to Haiku
_CONTEXT_MAX_TOKENS = 150


class ContextualEnricher:
    """Generate per-chunk context via Claude Haiku and prepend to FTS content.

    Args:
        api_key: Anthropic API key.
        model: Claude model for context generation (default: Haiku).
        language_hint: Hint for response language (e.g. "Turkish"). When
            set, the prompt instructs Haiku to respond in that language.
    """

    def __init__(
        self,
        api_key: str,
        model: str = _DEFAULT_MODEL,
        language_hint: str = "Turkish",
    ) -> None:
        try:
            import anthropic as _anthropic
        except ImportError as exc:
            raise ImportError(
                "anthropic package is required for contextual enrichment. "
                "Install it with: pip install anthropic"
            ) from exc

        self._client = _anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._language_hint = language_hint
        self._call_count = 0

    def enrich(self, note: "ParsedNote", chunk: "Chunk") -> str:
        """Return context-enriched content for *chunk* within *note*.

        Calls Claude Haiku to produce a short situating context, then
        returns ``f"{context}\\n\\n{chunk.content}"``.  On API error the
        original chunk content is returned unchanged so indexing continues.

        Args:
            note: The parsed document the chunk belongs to.
            chunk: The specific chunk to enrich.

        Returns:
            Enriched string suitable for FTS5 insertion.
        """
        doc_summary = _build_doc_summary(note)
        chunk_text = chunk.content[:_MAX_CHUNK_TOKENS]

        lang_instruction = (
            f" Respond in {self._language_hint}." if self._language_hint else ""
        )
        prompt = (
            f"<document>\n"
            f"File: {note.path}\n"
            f"Title: {note.title}\n"
            f"Content:\n{doc_summary}\n"
            f"</document>\n\n"
            f"<chunk>\n{chunk_text}\n</chunk>\n\n"
            f"Generate a concise 50-100 word context that situates this chunk "
            f"within the document for improved search retrieval.{lang_instruction} "
            f"Answer only with the context, nothing else."
        )

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=_CONTEXT_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            context = response.content[0].text.strip()
            self._call_count += 1
            if self._call_count % 50 == 0:
                logger.info(
                    "contextual_enricher: %d chunks enriched so far", self._call_count
                )
            return f"{context}\n\n{chunk.content}"
        except Exception:
            logger.warning(
                "contextual_enricher: Haiku call failed for %s (chunk %r), "
                "using original content",
                note.path,
                chunk.heading,
                exc_info=True,
            )
            return chunk.content

    @property
    def call_count(self) -> int:
        """Total number of successful Haiku calls made."""
        return self._call_count


def _build_doc_summary(note: "ParsedNote") -> str:
    """Build a truncated document summary for the Haiku prompt."""
    full_text = "\n".join(c.content for c in note.chunks)
    return full_text[:_MAX_DOC_TOKENS]
