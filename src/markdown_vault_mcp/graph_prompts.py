"""Custom LightRAG prompts for Mergen vault (Turkish domain).

Overrides LightRAG's default entity extraction with domain-specific
entity types and relation examples for the Mergen knowledge system.
"""
from __future__ import annotations

ENTITY_TYPES = [
    "sezgi",        # Intuition/insight (e.g. Sezgi 017)
    "aksiyom",      # Axiom (e.g. Aksiyom 4 - Caller Trust Yok)
    "kor_nokta",    # Blind spot (KN1-KN5)
    "karar",        # Decision document (e.g. TPC kararı)
    "disiplin",     # Discipline system (BKS, D0-D13)
    "prensip",      # Principle (sermaye-zenginlik, vendor bağımsızlık)
    "aktör",        # Actor (Mergen, Mac CC, Web Claude)
    "süreç",        # Process (sprint, handoff, deploy)
    "kavram",       # Concept (otonomi, güven, enrichment)
    "diğer",        # Other
]

# Additional instructions injected into LightRAG's {user_prompt} slot
USER_PROMPT = """
MERGEN Domain-Specific Instructions:
1. Entity recognition rules:
   - "Sezgi NNN" → entity_type: sezgi (e.g. "Sezgi 017", "Sezgi 014")
   - "Aksiyom N" → entity_type: aksiyom (e.g. "Aksiyom 4", "Aksiyom Adayı 1")
   - "KNN" or "Kör Nokta N" → entity_type: kor_nokta (e.g. "KN2", "KN3")
   - "BKS", "Bilgi Kalite Standartları", "D0"-"D13" → entity_type: disiplin
   - "TPC", "Sprint N", "Handoff" → entity_type: karar or süreç
   - "Mac CC", "Web Claude", "Mergen" → entity_type: aktör

2. Relation keywords to prioritize:
   - besler (feeds/supports): sezgi → aksiyom
   - tetikler (triggers): karar → sezgi, KN → aksiyon
   - ihlal (violates): eylem → aksiyom
   - doğrular (validates): kanıt → sezgi
   - bağlıdır (depends on): karar → karar
   - tezahür (manifests): aksiyom → sezgi
   - mitigas (mitigates): disiplin → kor_nokta

3. Always extract relationship_strength (1-10) based on how explicitly
   the text states the relationship.

4. For technical terms (Sprint, handoff, enrichment, FTS, K-6, OAuth),
   keep original casing. Turkish morphology: strip suffixes like -ın, -den,
   -nın, -da for entity name normalization.
"""
