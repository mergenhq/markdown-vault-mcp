"""MCP tool registrations for LightRAG knowledge graph search.

Three tools (Option C — separate from FTS search):
  graph_search      — hybrid entity+relation search via LightRAG
  entity_lookup     — entity-centric local search
  multi_hop_query   — 2-hop graph traversal for connected concepts

All tools are no-ops when GRAPH_ENABLED is not set or graph not built.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)


def register_graph_tools(mcp: "FastMCP") -> None:
    """Register graph search tools on the FastMCP instance."""
    from markdown_vault_mcp.graph_index import get_graph_index, is_graph_enabled

    if not is_graph_enabled():
        logger.info("graph_tools: GRAPH_ENABLED not set — graph tools disabled")
        return

    @mcp.tool(
        description=(
            "Search the Mergen knowledge graph using entity and relation-aware retrieval. "
            "Better than 'search' for conceptual queries, connections between ideas, "
            "and multi-entity reasoning (e.g. 'which axioms relate to Sezgi 017?'). "
            "Returns synthesized answer from the knowledge graph. "
            "Modes: hybrid (default), local (entity-focused), global (relation-focused)."
        )
    )
    async def graph_search(query: str, mode: str = "hybrid", top_k: int = 5) -> str:
        """Search the knowledge graph with entity+relation awareness.

        Args:
            query: Natural language query about concepts, entities, or relationships.
            mode: 'hybrid' (default), 'local' (entity-centric), 'global' (relation-centric).
            top_k: Maximum number of results (default: 5).
        """
        graph = get_graph_index()
        if not graph.is_ready:
            return "Knowledge graph not available. Graph may not be built yet (run graph build first)."
        results = await graph.query(query, mode=mode, top_k=top_k)
        if not results:
            return "No results found in knowledge graph."
        return "\n\n---\n\n".join(r["text"] for r in results)

    @mcp.tool(
        description=(
            "Look up a specific entity in the Mergen knowledge graph and retrieve related content. "
            "Use for named entities: 'Sezgi 017', 'Aksiyom 4', 'KN2', 'TPC', 'Mac CC'. "
            "Returns chunks and relations directly connected to the entity."
        )
    )
    async def entity_lookup(entity_name: str, top_k: int = 5) -> str:
        """Find an entity and its directly connected content.

        Args:
            entity_name: Entity name (e.g. 'Sezgi 017', 'Aksiyom 4', 'TPC kararı').
            top_k: Maximum results (default: 5).
        """
        graph = get_graph_index()
        if not graph.is_ready:
            return "Knowledge graph not available."
        results = await graph.entity_lookup(entity_name, top_k=top_k)
        if not results:
            return f"Entity '{entity_name}' not found in knowledge graph."
        return "\n\n---\n\n".join(r["text"] for r in results)

    @mcp.tool(
        description=(
            "Multi-hop reasoning query across the Mergen knowledge graph. "
            "Traces connections between concepts across 2+ hops. "
            "Ideal for: 'What connects Sezgi 014 and Sezgi 017?', "
            "'Which axioms underlie the TPC decision?', "
            "'How does KN2 relate to Mac CC autonomy?'. "
            "Returns synthesized answer tracing the connection path."
        )
    )
    async def multi_hop_query(query: str, top_k: int = 5) -> str:
        """Trace multi-hop connections in the knowledge graph.

        Args:
            query: Relationship/connection query (e.g. 'What connects X and Y?').
            top_k: Maximum results (default: 5).
        """
        graph = get_graph_index()
        if not graph.is_ready:
            return "Knowledge graph not available."
        results = await graph.multi_hop(query, top_k=top_k)
        if not results:
            return "No multi-hop connections found."
        return "\n\n---\n\n".join(r["text"] for r in results)

    logger.info("graph_tools: registered graph_search, entity_lookup, multi_hop_query")
