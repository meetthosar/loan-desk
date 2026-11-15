"""Split policy pages by section heading, then by size.

Every chunk carries `doc` and `section_id` metadata. The retrieval eval labels
answers at section level, so its scores do not depend on chunk size.
"""

from __future__ import annotations

import re
from pathlib import Path

from loandesk.vectorstores.base import Chunk

MAX_CHARS = 900
OVERLAP_CHARS = 150


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def split_sections(markdown: str) -> tuple[str, list[tuple[str, str]]]:
    title_match = re.search(r"^# (.+)$", markdown, re.M)
    title = title_match.group(1).strip() if title_match else ""
    parts = re.split(r"^## (.+)$", markdown, flags=re.M)
    intro = parts[0].replace(title_match.group(0), "") if title_match else parts[0]
    sections = [("overview", intro)]
    sections += [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts), 2)]
    return title, [(name, body.strip()) for name, body in sections if body.strip()]


def split_by_size(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text]
    paragraphs, pieces, current = text.split("\n\n"), [], ""
    for para in paragraphs:
        if current and len(current) + len(para) > MAX_CHARS:
            pieces.append(current.strip())
            current = current[-OVERLAP_CHARS:]
        current += "\n\n" + para
    if current.strip():
        pieces.append(current.strip())
    return pieces


def chunk_document(doc: str, markdown: str) -> list[Chunk]:
    title, sections = split_sections(markdown)
    chunks = []
    for section, body in sections:
        section_id = f"{doc}#{slug(section)}"
        for n, piece in enumerate(split_by_size(body)):
            chunks.append(Chunk(
                id=f"{section_id}-{n}",
                # The heading goes into the text so the embedding knows what the passage is about.
                text=f"{title} — {section}\n\n{piece}",
                metadata={"doc": doc, "section_id": section_id, "title": title},
            ))
    return chunks


def load_policies(directory: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(directory.glob("*.md")):
        chunks += chunk_document(path.stem, path.read_text())
    return chunks
