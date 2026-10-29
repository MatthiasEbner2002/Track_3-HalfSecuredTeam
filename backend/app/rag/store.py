"""Chunk store in SQLite (data/law.db) and cosine-similarity retrieval.

A few hundred chunks fit in memory, so retrieval is a plain scan, no vector index.
"""

import math
import sqlite3
from array import array
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from app.config import LAW_DB_PATH
from app.models import LawParagraph

_COLUMNS = ["id", "kind", "article", "article_title", "text", "lead_in"]


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


def embedding_text(p: LawParagraph) -> str:
    """What gets embedded: id and title for context, the lead-in so a point keeps its meaning."""
    return "\n".join(part for part in (f"{p.id} {p.article_title}".strip(), p.lead_in, p.text) if part)


def save(chunks: list[LawParagraph], embedder: Embedder, db_path: Path = LAW_DB_PATH) -> None:
    """Embed every chunk and write the store from scratch."""
    vectors = embedder.embed_documents([embedding_text(p) for p in chunks])
    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"CREATE TABLE chunks ({' TEXT, '.join(_COLUMNS)} TEXT, embedding BLOB)")
        conn.executemany(
            f"INSERT INTO chunks VALUES ({', '.join('?' * (len(_COLUMNS) + 1))})",
            [
                (*(getattr(p, c) for c in _COLUMNS), array("f", v).tobytes())
                for p, v in zip(chunks, vectors, strict=True)
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


class Retriever:
    def __init__(self, embedder: Embedder, db_path: Path = LAW_DB_PATH):
        self.embedder = embedder
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(f"SELECT {', '.join(_COLUMNS)}, embedding FROM chunks ORDER BY rowid").fetchall()
        finally:
            conn.close()
        self.chunks = [LawParagraph(**dict(zip(_COLUMNS, r[:-1]))) for r in rows]
        self._vectors = [_normalise(array("f", r[-1]).tolist()) for r in rows]

    @classmethod
    def from_chunks(cls, chunks: list[LawParagraph], embedder: Embedder, db_path: Path) -> "Retriever":
        save(chunks, embedder, db_path)
        return cls(embedder, db_path)

    def get(self, chunk_id: str) -> LawParagraph | None:
        return next((c for c in self.chunks if c.id == chunk_id), None)

    def search(
        self, query: str, k: int, where: Callable[[LawParagraph], bool] = lambda c: True
    ) -> list[LawParagraph]:
        """The k chunks matching where that are most similar to query, best first."""
        q = _normalise(self.embedder.embed_query(query))
        scored = [
            (sum(a * b for a, b in zip(q, v)), i)
            for i, (c, v) in enumerate(zip(self.chunks, self._vectors))
            if where(c)
        ]
        scored.sort(key=lambda s: (-s[0], s[1]))
        return [self.chunks[i] for _, i in scored[:k]]
