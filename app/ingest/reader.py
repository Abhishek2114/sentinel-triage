"""File reader – read a log file (full or tail mode) and yield lines."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from app.core.logging import get_logger

log = get_logger(__name__)


async def read_file_lines(path: str | Path) -> AsyncIterator[str]:
    """Read all lines from a file asynchronously."""
    p = Path(path)
    if not p.exists():
        log.error("file_not_found", path=str(p))
        return
    if not p.is_file():
        log.error("not_a_file", path=str(p))
        return

    log.info("reading_file", path=str(p), size_bytes=p.stat().st_size)

    # Read in a thread to avoid blocking the event loop
    content = await asyncio.to_thread(p.read_text, encoding="utf-8", errors="replace")
    for line in content.splitlines():
        yield line


async def tail_file(path: str | Path, poll_interval: float = 0.5) -> AsyncIterator[str]:
    """Tail a file (like `tail -f`), yielding new lines as they appear.

    This is a long-running async generator – use with `async for`.
    """
    p = Path(path)
    if not p.exists():
        log.error("file_not_found", path=str(p))
        return

    log.info("tailing_file", path=str(p))
    offset = p.stat().st_size  # Start from the end of the file

    while True:
        current_size = p.stat().st_size
        if current_size > offset:
            text = await asyncio.to_thread(
                _read_chunk, p, offset, current_size - offset
            )
            offset = current_size
            for line in text.splitlines():
                if line.strip():
                    yield line
        elif current_size < offset:
            # File was truncated – start from the beginning
            offset = 0
        await asyncio.sleep(poll_interval)


def _read_chunk(path: Path, offset: int, size: int) -> str:
    """Read a chunk from a file at a given offset."""
    with open(path, encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        return f.read(size)
