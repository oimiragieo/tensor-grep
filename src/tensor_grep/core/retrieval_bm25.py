"""Okapi BM25 ranking over a chunk corpus -- the lexical leg of hybrid semantic search.

This is a real relevance scorer (IDF + term-frequency saturation + length normalization), unlike
``retrieval_lexical.score_term_overlap`` which is a bare set-membership count. It reuses
``retrieval_lexical.split_terms`` as the tokenizer so the BM25 leg tokenizes identically to the
existing lexical path (camelCase / underscore / hyphen aware, lowercased). Pure Python, no new deps.
"""

from __future__ import annotations

import math
from collections import Counter

from tensor_grep.core.retrieval_chunker import Chunk
from tensor_grep.core.retrieval_lexical import split_terms

# Okapi BM25 defaults; k1 controls term-frequency saturation, b the length normalization.
DEFAULT_K1: float = 1.5
DEFAULT_B: float = 0.75


class Bm25Index:
    """An in-memory BM25 index over a list of :class:`Chunk` documents."""

    def __init__(
        self, chunks: list[Chunk], *, k1: float = DEFAULT_K1, b: float = DEFAULT_B
    ) -> None:
        self.chunks = list(chunks)
        self.k1 = k1
        self.b = b

        doc_terms = [split_terms(chunk.text) for chunk in self.chunks]
        self._tf: list[Counter[str]] = [Counter(terms) for terms in doc_terms]
        self._doc_len: list[int] = [len(terms) for terms in doc_terms]
        n = len(self.chunks)
        self._avgdl: float = (sum(self._doc_len) / n) if n else 0.0

        df: Counter[str] = Counter()
        for terms in doc_terms:
            for term in set(terms):
                df[term] += 1
        # BM25 IDF with +1 smoothing so weights stay non-negative even for very common terms.
        self._idf: dict[str, float] = {
            term: math.log(1.0 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()
        }

    def query(self, query: str, *, top_k: int = 10) -> list[tuple[int, float]]:
        """Rank chunks against ``query``; returns ``(chunk_index, score)`` sorted by score desc.

        Zero-score (non-matching) chunks are excluded. Ties break by chunk index for determinism.
        """
        if not self.chunks or self._avgdl == 0.0:
            return []

        q_terms = split_terms(query)
        if not q_terms:
            return []

        scored: dict[int, float] = {}
        for i, tf in enumerate(self._tf):
            doc_len = self._doc_len[i]
            score = 0.0
            for term in q_terms:
                freq = tf.get(term, 0)
                if freq == 0:
                    continue
                idf = self._idf.get(term, 0.0)
                denom = freq + self.k1 * (1.0 - self.b + self.b * doc_len / self._avgdl)
                score += idf * (freq * (self.k1 + 1.0)) / denom
            if score > 0.0:
                scored[i] = score

        ranked = sorted(scored.items(), key=lambda item: (-item[1], item[0]))
        return ranked[:top_k]
