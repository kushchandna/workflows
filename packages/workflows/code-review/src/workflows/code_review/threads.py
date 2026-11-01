import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from workflows.code_review.changes import ChangedFile
from workflows.code_review.errors import ReviewInputError
from workflows.code_review.formats import Thread

_THREADS = TypeAdapter(list[Thread])


@dataclass(frozen=True)
class Routing:
    batches: list[frozenset[str]]  # thread ids per batch, by batch index
    overall: frozenset[str]


def load_threads(paths: list[Path]) -> list[Thread]:
    """Read discussion files (each a JSON list of threads) into one list."""
    threads: list[Thread] = []
    seen: set[str] = set()
    for path in paths:
        try:
            loaded = _THREADS.validate_python(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except OSError as e:
            raise ReviewInputError(f"cannot read discussions file {path}: {e}") from e
        except (ValueError, ValidationError) as e:
            raise ReviewInputError(f"invalid discussions file {path}: {e}") from e
        for thread in loaded:
            if thread.id in seen:
                raise ReviewInputError(f"duplicate thread id {thread.id!r} (in {path})")
            seen.add(thread.id)
            threads.append(thread)
    return threads


def route_threads(threads: list[Thread], batches: list[list[ChangedFile]]) -> Routing:
    """A thread on a file or line goes to the batch holding that file (by its path or old
    path). Every other thread, including one on a file outside the change, goes to overall."""
    per_batch: list[set[str]] = [set() for _ in batches]
    overall: set[str] = set()
    for thread in threads:
        target = thread.target
        index = None
        if target.type != "change":
            index = next(
                (
                    i
                    for i, batch in enumerate(batches)
                    if any(target.path in f.paths for f in batch)
                ),
                None,
            )
        (overall if index is None else per_batch[index]).add(thread.id)
    return Routing([frozenset(ids) for ids in per_batch], frozenset(overall))
