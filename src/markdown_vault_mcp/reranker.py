"""BGE Cross-Encoder reranker for post-retrieval score improvement.

Applies a cross-encoder model (BAAI/bge-reranker-base by default) to
re-score search results after initial retrieval.  The reranker evaluates
(query, chunk) pairs jointly — capturing deeper relevance signals than
bi-encoder vector search or BM25 alone.

Expected improvement: ~27% precision gain (Anthropic / BEIR benchmarks).
bge-reranker-base (110M params) chosen for CPU production viability (~50ms/query).
"""

from __future__ import annotations

import logging
from dataclasses import replace as _dc_replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from markdown_vault_mcp.types import GroupedResult

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "BAAI/bge-reranker-base"
_DEFAULT_CANDIDATE_LIMIT = 50  # retrieve this many before reranking


class Reranker:
    """Cross-encoder reranker backed by sentence-transformers.

    Loads the model once at construction time.  Call :meth:`rerank` for
    each search request.

    Args:
        model_name: HuggingFace model identifier (default: ``BAAI/bge-reranker-v2-m3``).
        device: Torch device string, e.g. ``"cpu"`` or ``"cuda:0"``.
        candidate_limit: Number of candidates to retrieve before reranking.
    """

    def __init__(
        self,
        model_name: str = _DEFAULT_MODEL,
        device: str = "cpu",
        candidate_limit: int = _DEFAULT_CANDIDATE_LIMIT,
    ) -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is required for the reranker. "
                "Install it with: pip install sentence-transformers"
            ) from exc

        logger.info("reranker: loading %s on device=%s", model_name, device)
        self._model = CrossEncoder(model_name, device=device)
        self._model_name = model_name
        self._candidate_limit = candidate_limit
        logger.info("reranker: %s loaded", model_name)

    def rerank(
        self,
        query: str,
        results: list["GroupedResult"],
        top_n: int,
    ) -> list["GroupedResult"]:
        """Rerank *results* by cross-encoder score and return top *top_n*.

        Each result's top section is scored against *query*.  Results are
        sorted by descending reranker score; the ``score`` field of each
        returned :class:`~markdown_vault_mcp.types.GroupedResult` is replaced
        with the cross-encoder logit for transparent downstream display.

        Args:
            query: Original search query.
            results: Candidate results from keyword/semantic/hybrid search.
            top_n: Maximum number of results to return after reranking.

        Returns:
            Reranked list of :class:`~markdown_vault_mcp.types.GroupedResult`,
            at most *top_n* items.
        """
        if not results:
            return results

        # Build (query, text) pairs — one per result using top section content.
        pairs: list[list[str]] = []
        for result in results:
            best_content = result.sections[0].content if result.sections else result.path
            pairs.append([query, best_content])

        try:
            raw_scores = self._model.predict(pairs)
        except Exception:
            logger.warning(
                "reranker: prediction failed for query %r, returning unranked",
                query[:80],
                exc_info=True,
            )
            return results[:top_n]

        # Pair scores with results and sort descending.
        scored = sorted(
            zip(raw_scores, results),
            key=lambda x: float(x[0]),
            reverse=True,
        )

        reranked = []
        for raw_score, result in scored[:top_n]:
            reranked.append(_dc_replace(result, score=float(raw_score)))

        logger.debug(
            "reranker: %d → %d results, top score=%.3f",
            len(results),
            len(reranked),
            float(scored[0][0]) if scored else 0.0,
        )
        return reranked

    @property
    def candidate_limit(self) -> int:
        """Candidate pool size for pre-reranking retrieval."""
        return self._candidate_limit
