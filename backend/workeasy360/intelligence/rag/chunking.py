"""The one chunking strategy (Task 28): bounded, deterministic, pure.

Paragraphs are packed greedily into chunks of at most ``RAG_CHUNK_CHARS``
characters; a paragraph longer than that is split at the last sentence end or
whitespace before the limit (a hard cut only if there is none). Each chunk
after the first repeats the tail of the previous one (``RAG_CHUNK_OVERLAP_CHARS``,
word-aligned) so a passage split across a boundary is still retrievable whole.

Three bounds, each enforced before a single embedding is paid for:

- a document is at most ``RAG_MAX_DOCUMENT_CHARS`` characters (refused at
  ingestion - the product splits larger content into several documents);
- it yields at most ``RAG_MAX_CHUNKS_PER_DOCUMENT`` chunks (refused likewise);
- therefore a document costs at most that many embeddings, whatever its
  shape. A 500 MB upload cannot generate millions of vectors: it cannot be
  submitted at all.

Deterministic, so re-chunking the same version (a retry, a crash) produces
the identical chunks - which is what lets indexing be retried without
duplicating anything (Task 41).
"""

from __future__ import annotations

import re
from typing import Final

_PARAGRAPH_BREAK: Final = re.compile(r"\n\s*\n")
_SPACES: Final = re.compile(r"[ \t\f\v]+")
_BOUNDARY: Final = re.compile(r"[.!?](?=\s)|\n|\s")


class TooManyChunks(ValueError):
    pass


def normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def chunk_text(text: str, *, size: int, overlap: int, max_chunks: int) -> list[str]:
    size = max(50, int(size))
    overlap = max(0, min(int(overlap), size // 2))
    paragraphs = [p.strip() for p in _PARAGRAPH_BREAK.split(normalise(text)) if p.strip()]

    pieces: list[str] = []
    for paragraph in paragraphs:
        while len(paragraph) > size:
            cut = _cut_point(paragraph, size)
            pieces.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if paragraph:
            pieces.append(paragraph)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}\n\n{piece}" if current else piece
        if len(candidate) <= size:
            current = candidate
            continue
        chunks.append(current)
        if len(chunks) >= max_chunks:
            raise TooManyChunks(f"More than {max_chunks} chunks.")
        tail = _tail(current, overlap)
        current = (
            f"{tail} {piece}".strip() if tail and len(tail) + len(piece) < size else piece
        )
    if current:
        chunks.append(current)
    if len(chunks) > max_chunks:
        raise TooManyChunks(f"More than {max_chunks} chunks.")
    return chunks


def _cut_point(text: str, size: int) -> int:
    window = text[:size]
    best = -1
    for match in _BOUNDARY.finditer(window):
        if match.end() >= size // 2:
            best = match.end()
    return best if best > 0 else size


def _tail(text: str, overlap: int) -> str:
    if overlap <= 0 or len(text) <= overlap:
        return ""
    tail = text[-overlap:]
    space = tail.find(" ")
    return tail[space + 1 :] if 0 <= space < len(tail) - 1 else tail
