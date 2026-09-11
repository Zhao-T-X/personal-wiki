from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    index: int
    content: str
    start_offset: int
    end_offset: int


def chunk_text(text: str, max_chars: int = 1800, overlap: int = 200) -> list[Chunk]:
    if max_chars <= 0 or overlap < 0 or overlap >= max_chars:
        raise ValueError('Require max_chars > 0 and 0 <= overlap < max_chars')
    if not text:
        return []

    chunks: list[Chunk] = []
    start = 0
    index = 0
    n = len(text)
    while start < n:
        hard_end = min(n, start + max_chars)
        end = hard_end
        if hard_end < n:
            window = text[start:hard_end]
            candidates = [window.rfind('\n\n'), window.rfind('\n'), window.rfind(' ')]
            boundary = max(candidates)
            if boundary > max_chars // 2:
                end = start + boundary + (2 if window[boundary:boundary+2] == '\n\n' else 1)
        chunks.append(Chunk(index, text[start:end], start, end))
        if end >= n:
            break
        start = max(0, end - overlap)
        index += 1
    return chunks
