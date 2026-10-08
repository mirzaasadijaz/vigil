"""Vector-store interface. Everything else in the project talks to this, never to Chroma directly,
so the backend can be swapped (e.g. to pgvector) by writing one new file."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Chunk:
    id: str
    text: str
    metadata: dict = field(default_factory=dict)


@dataclass
class Hit:
    id: str
    text: str
    metadata: dict
    score: float            # 0..1, higher = more similar


class VectorStore(ABC):
    @abstractmethod
    def upsert(self, chunks: list[Chunk]) -> None: ...

    @abstractmethod
    def search(self, query: str, k: int = 4, where: dict | None = None) -> list[Hit]: ...

    @abstractmethod
    def delete(self, where: dict | None = None, ids: list[str] | None = None) -> None: ...

    @abstractmethod
    def count(self) -> int: ...

    @abstractmethod
    def reset(self) -> None:
        """Drop everything and start empty."""
