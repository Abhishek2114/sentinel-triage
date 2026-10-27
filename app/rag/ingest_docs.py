"""Split runbooks into retrievable chunks.

Runbooks are split on ``##`` headings so a chunk is one coherent section
("Symptoms", "Remediation", …). Each chunk's text starts with the runbook and
section titles, so a "Remediation" section still embeds as *database*
remediation. Sections longer than ``max_words`` are split further.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


def chunk_document(text: str, chunk_size: int = 512, overlap: int = 64) -> list[str]:
    """Split a document into overlapping chunks of ``chunk_size`` words."""
    if not 0 <= overlap < chunk_size:
        # overlap >= chunk_size would never advance and loop forever
        raise ValueError("overlap must be >= 0 and smaller than chunk_size")
    words = text.split()
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            # Last chunk reached the end; another would only repeat its tail
            break
        start += chunk_size - overlap
    return chunks


@dataclass(frozen=True, slots=True)
class RunbookChunk:
    """One retrievable piece of a runbook."""

    source_doc: str
    chunk_index: int
    title: str
    section: str
    text: str


_H1 = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_H2 = re.compile(r"^##\s+(.+)$", re.MULTILINE)


def split_runbook(source_doc: str, markdown: str, max_words: int = 200) -> list[RunbookChunk]:
    """Split one markdown runbook into section chunks."""
    h1 = _H1.search(markdown)
    title = h1.group(1).strip() if h1 else Path(source_doc).stem.replace("_", " ").title()

    headings = list(_H2.finditer(markdown))
    if headings:
        sections = [
            (m.group(1).strip(), markdown[m.end() : nxt.start() if nxt else len(markdown)])
            for m, nxt in zip(headings, [*headings[1:], None], strict=True)
        ]
    else:
        body = markdown[h1.end() :] if h1 else markdown
        sections = [("Overview", body)]

    chunks: list[RunbookChunk] = []
    for section, body in sections:
        if not body.strip():
            continue
        # Keep short sections verbatim so their bullet lists survive
        pieces = (
            [body.strip()]
            if len(body.split()) <= max_words
            else chunk_document(body, chunk_size=max_words, overlap=20)
        )
        for piece in pieces:
            chunks.append(
                RunbookChunk(
                    source_doc=source_doc,
                    chunk_index=len(chunks),
                    title=title,
                    section=section,
                    text=f"{title} – {section}\n{piece}",
                )
            )
    return chunks


def load_runbooks(directory: str | Path) -> list[RunbookChunk]:
    """Load and split every ``*.md`` runbook in a directory."""
    chunks: list[RunbookChunk] = []
    for md_file in sorted(Path(directory).glob("*.md")):
        chunks.extend(split_runbook(md_file.name, md_file.read_text(encoding="utf-8")))
    return chunks
