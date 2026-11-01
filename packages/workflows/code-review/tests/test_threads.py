import json

import pytest

from workflows.code_review import ReviewInputError
from workflows.code_review.changes import ChangedFile
from workflows.code_review.formats import Thread
from workflows.code_review.threads import load_threads, route_threads


def thread(id, target):
    return {
        "id": id,
        "target": target,
        "state": "open",
        "messages": [{"author": "a", "body": "b"}],
    }


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_text(json.dumps(data))
    return p


def test_load_merges_files(tmp_path):
    a = write(tmp_path, "a.json", [thread("t1", {"type": "change"})])
    b = write(tmp_path, "b.json", [thread("t2", {"type": "file", "path": "x.py"})])
    assert [t.id for t in load_threads([a, b])] == ["t1", "t2"]


def test_load_nothing():
    assert load_threads([]) == []


@pytest.mark.parametrize(
    "content", ["not json", json.dumps({"id": "t1"}), json.dumps([{"id": "t1"}])]
)
def test_load_rejects_bad_files(tmp_path, content):
    p = tmp_path / "bad.json"
    p.write_text(content)
    with pytest.raises(ReviewInputError, match="bad.json"):
        load_threads([p])


def test_load_rejects_missing_file(tmp_path):
    with pytest.raises(ReviewInputError, match="missing.json"):
        load_threads([tmp_path / "missing.json"])


def test_load_rejects_duplicate_ids(tmp_path):
    a = write(tmp_path, "a.json", [thread("t1", {"type": "change"})])
    b = write(tmp_path, "b.json", [thread("t1", {"type": "change"})])
    with pytest.raises(ReviewInputError, match="t1"):
        load_threads([a, b])


def test_routing():
    batches = [
        [ChangedFile("a.py", "modified", None, False, 1)],
        [ChangedFile("new.py", "renamed", "old.py", False, 0)],
    ]
    threads = [
        Thread.model_validate(
            thread(
                "t1", {"type": "lines", "path": "a.py", "start_line": 1, "end_line": 1}
            )
        ),
        Thread.model_validate(thread("t2", {"type": "change"})),
        Thread.model_validate(thread("t3", {"type": "file", "path": "unchanged.py"})),
    ]
    r = route_threads(threads, batches)
    assert r.batches == [frozenset({"t1"}), frozenset()]
    assert r.overall == {"t2", "t3"}


def test_thread_on_old_path_routes_to_batch():
    batches = [[ChangedFile("new.py", "renamed", "old.py", False, 0)]]
    t = Thread.model_validate(thread("t1", {"type": "file", "path": "old.py"}))
    assert route_threads([t], batches).batches == [frozenset({"t1"})]
