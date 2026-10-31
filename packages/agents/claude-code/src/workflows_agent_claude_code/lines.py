import asyncio
from collections.abc import AsyncIterator


async def iter_lines(
    stream: asyncio.StreamReader, chunk_size: int = 65536
) -> AsyncIterator[str]:
    """Yield the stream's non-empty lines, however long they are.

    Whole lines are decoded, so a character split across chunks stays intact.
    """
    pending: list[bytes] = []
    while chunk := await stream.read(chunk_size):
        *complete, rest = chunk.split(b"\n")
        for part in complete:
            pending.append(part)
            if text := _decode(b"".join(pending)):
                yield text
            pending = []
        pending.append(rest)
    if text := _decode(b"".join(pending)):
        yield text


def _decode(line: bytes) -> str:
    return line.decode("utf-8", errors="replace").rstrip("\r")
