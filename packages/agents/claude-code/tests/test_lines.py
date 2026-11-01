import asyncio

from workflows.agent_claude_code.lines import iter_lines


def reader(data: bytes) -> asyncio.StreamReader:
    r = asyncio.StreamReader()
    r.feed_data(data)
    r.feed_eof()
    return r


async def collect(data: bytes, chunk_size: int = 65536) -> list[str]:
    return [line async for line in iter_lines(reader(data), chunk_size)]


async def test_splits_lines_and_keeps_final_partial_line():
    assert await collect(b"a\nb\n\nc") == ["a", "b", "c"]


async def test_line_longer_than_default_limit():
    big = "x" * 1_000_000
    assert await collect(f"{big}\nend\n".encode()) == [big, "end"]


async def test_utf8_split_across_chunks():
    data = "ab€cd\n".encode()  # € is 3 bytes at offsets 2..4
    assert await collect(data, chunk_size=3) == ["ab€cd"]
