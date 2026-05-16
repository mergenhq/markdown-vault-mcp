"""Contextual chunk enrichment via Claude Haiku for improved FTS retrieval.

Based on Anthropic's contextual retrieval technique (September 2024):
each chunk is enriched with a short document-level context before FTS indexing,
improving retrieval accuracy (~69% error reduction per Anthropic).

Optimizations (v2):
- Prompt caching: system prompt + document context cached (5-min TTL)
- Batch API: async batch processing (50% cheaper, 24h max wait)
- Document grouping: chunks sorted by document path → consecutive chunks
  from same doc share cache hits

Ref: https://www.anthropic.com/news/contextual-retrieval
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from markdown_vault_mcp.scanner import ParsedNote
    from markdown_vault_mcp.types import Chunk

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_DOC_TOKENS = 2000
_MAX_CHUNK_TOKENS = 800
_CONTEXT_MAX_TOKENS = 150
_BATCH_POLL_INTERVAL = 300  # 5 min
_BATCH_MAX_WAIT = 86400     # 24h


@dataclass
class EnrichmentStats:
    calls: int = 0
    cache_writes: int = 0
    cache_reads: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def cache_hit_rate(self) -> float:
        if self.input_tokens == 0:
            return 0.0
        return self.cache_read_tokens / (self.input_tokens + self.cache_read_tokens)

    @property
    def effective_cost_usd(self) -> float:
        """Approximate cost at Haiku 4.5 pricing."""
        input_cost = self.input_tokens * 1.00 / 1_000_000
        output_cost = self.output_tokens * 5.00 / 1_000_000
        cache_write_cost = self.cache_write_tokens * 1.25 / 1_000_000
        cache_read_cost = self.cache_read_tokens * 0.10 / 1_000_000
        return input_cost + output_cost + cache_write_cost + cache_read_cost


def _build_doc_summary(note: "ParsedNote") -> str:
    full_text = "\n".join(c.content for c in note.chunks)
    return full_text[:_MAX_DOC_TOKENS]


def _system_prompt(language_hint: str) -> str:
    lang = f" Always respond in {language_hint}." if language_hint else ""
    return (
        "You generate concise search context for document chunks to improve "
        f"retrieval accuracy.{lang} Answer only with the context, nothing else."
    )


class ContextualEnricher:
    """Generate per-chunk context via Claude Haiku with prompt caching.

    v2 optimizations:
    - Prompt caching on system prompt + document context (reduces cost ~65-70%)
    - enrich_batch() for async batch API (additional 50% discount)
    - Chunks sorted by document path for maximum cache hit rate

    Args:
        api_key: Anthropic API key.
        model: Claude model (default: Haiku 4.5).
        language_hint: Response language hint (default: "Turkish").
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
        self._system_text = _system_prompt(language_hint)
        self.stats = EnrichmentStats()

    def _build_messages(self, note: "ParsedNote", chunk: "Chunk") -> list:
        doc_summary = _build_doc_summary(note)
        chunk_text = chunk.content[:_MAX_CHUNK_TOKENS]
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"<document>\n"
                            f"File: {note.path}\n"
                            f"Title: {note.title}\n"
                            f"Content:\n{doc_summary}\n"
                            f"</document>"
                        ),
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": (
                            f"<chunk>\n{chunk_text}\n</chunk>\n\n"
                            f"Generate a concise 50-100 word context that situates "
                            f"this chunk within the document for improved search retrieval."
                        ),
                    },
                ],
            }
        ]

    def _update_stats(self, usage) -> None:
        self.stats.calls += 1
        self.stats.input_tokens += getattr(usage, "input_tokens", 0)
        self.stats.output_tokens += getattr(usage, "output_tokens", 0)
        cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cr = getattr(usage, "cache_read_input_tokens", 0) or 0
        self.stats.cache_write_tokens += cw
        self.stats.cache_read_tokens += cr
        if cw:
            self.stats.cache_writes += 1
        if cr:
            self.stats.cache_reads += 1

    def enrich(self, note: "ParsedNote", chunk: "Chunk") -> str:
        """Enrich one chunk with prompt caching (sync).

        Document context is cached for 5 minutes — consecutive chunks
        from the same document will be cache hits.
        """
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=_CONTEXT_MAX_TOKENS,
                system=[
                    {
                        "type": "text",
                        "text": self._system_text,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=self._build_messages(note, chunk),
                extra_headers={"anthropic-beta": "prompt-caching-2024-07-31"},
            )
            self._update_stats(response.usage)
            context = response.content[0].text.strip()
            if self.stats.calls % 50 == 0:
                logger.info(
                    "contextual_enricher: %d enriched | cache hit rate %.1f%% | ~$%.4f",
                    self.stats.calls,
                    self.stats.cache_hit_rate * 100,
                    self.stats.effective_cost_usd,
                )
            return f"{context}\n\n{chunk.content}"
        except Exception:
            logger.warning(
                "contextual_enricher: Haiku call failed for %s, using original",
                note.path,
                exc_info=True,
            )
            return chunk.content

    def enrich_batch(
        self,
        items: list[tuple[str, "ParsedNote", "Chunk"]],
    ) -> dict[str, str]:
        """Enrich chunks via Batch API (async, 50% cheaper, up to 24h).

        Args:
            items: List of (custom_id, note, chunk) tuples.
                   Sort by note.path beforehand for max cache hits.

        Returns:
            Dict mapping custom_id → enriched content string.
        """
        try:
            from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
            from anthropic.types.messages.batch_create_params import Request
        except ImportError:
            logger.warning("Batch API types not available, falling back to sync")
            return {cid: self.enrich(note, chunk) for cid, note, chunk in items}

        requests = [
            Request(
                custom_id=custom_id,
                params=MessageCreateParamsNonStreaming(
                    model=self._model,
                    max_tokens=_CONTEXT_MAX_TOKENS,
                    system=[
                        {
                            "type": "text",
                            "text": self._system_text,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=self._build_messages(note, chunk),
                ),
            )
            for custom_id, note, chunk in items
        ]

        logger.info("contextual_enricher: submitting batch of %d requests", len(requests))
        batch = self._client.messages.batches.create(
            requests=requests,
            extra_headers={"anthropic-beta": "message-batches-2024-09-24,prompt-caching-2024-07-31"},
        )
        logger.info("contextual_enricher: batch %s submitted, polling...", batch.id)

        deadline = time.time() + _BATCH_MAX_WAIT
        while time.time() < deadline:
            time.sleep(_BATCH_POLL_INTERVAL)
            status = self._client.messages.batches.retrieve(batch.id)
            logger.info(
                "contextual_enricher: batch %s status=%s (%d/%d done)",
                batch.id,
                status.processing_status,
                status.request_counts.succeeded + status.request_counts.errored,
                len(requests),
            )
            if status.processing_status == "ended":
                break

        results: dict[str, str] = {}
        for result in self._client.messages.batches.results(batch.id):
            cid = result.custom_id
            if result.result.type == "succeeded":
                msg = result.result.message
                self._update_stats(msg.usage)
                context = msg.content[0].text.strip()
                # Find original chunk content
                orig = next((ch.content for rid, _, ch in items if rid == cid), "")
                results[cid] = f"{context}\n\n{orig}"
            else:
                logger.warning("Batch result %s failed: %s", cid, result.result.type)
                results[cid] = next((ch.content for rid, _, ch in items if rid == cid), "")

        logger.info(
            "contextual_enricher: batch done | %d results | cache hit %.1f%% | ~$%.4f",
            len(results),
            self.stats.cache_hit_rate * 100,
            self.stats.effective_cost_usd,
        )
        return results

    @property
    def call_count(self) -> int:
        return self.stats.calls
