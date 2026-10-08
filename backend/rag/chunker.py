"""Split text into overlapping chunks that respect paragraph / sentence boundaries."""
from __future__ import annotations

import re

from backend.rag.stores.base import Chunk


def _units(text: str, size: int) -> list[str]:
    """Paragraphs, with any paragraph longer than `size` cut at a sentence end (or hard cut)."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text):
        para = re.sub(r"[ \t]+", " ", para).strip()
        while len(para) > size:
            cut = max(para.rfind(". ", 0, size), para.rfind("\n", 0, size))
            cut = cut + 1 if cut > size // 2 else size
            out.append(para[:cut].strip())
            para = para[cut:].strip()
        if para:
            out.append(para)
    return out


def _tail(text: str, n: int) -> str:
    """Last ~n chars, starting at a word boundary."""
    if n <= 0 or len(text) <= n:
        return text if n > 0 else ""
    t = text[-n:]
    sp = t.find(" ")
    return t[sp + 1:] if 0 <= sp < n // 2 else t


def chunk_text(text: str, source: str, *, size: int = 900, overlap: int = 150,
               meta: dict | None = None) -> list[Chunk]:
    """Greedy-pack units into chunks <= ~size chars; each new chunk starts with the tail of
    the previous one so a sentence cut at the boundary is still retrievable."""
    units = _units(text, size)
    pieces: list[str] = []
    buf = ""
    for u in units:
        if buf and len(buf) + len(u) + 2 > size:
            pieces.append(buf)
            buf = (_tail(buf, overlap) + "\n\n" + u).strip()
        else:
            buf = (buf + "\n\n" + u).strip() if buf else u
    if buf:
        pieces.append(buf)
    return [Chunk(id=f"{source}#{i}", text=p,
                  metadata={**(meta or {}), "source": source, "chunk_index": i})
            for i, p in enumerate(pieces)]
