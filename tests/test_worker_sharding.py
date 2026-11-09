from __future__ import annotations

import pytest

from worker.worker import select_shard


def test_shards_partition_every_command_exactly_once():
    commands = [{"id": f"C{i:04d}", "text": f"command {i}"} for i in range(1, 3001)]
    count = 4
    shards = [select_shard(commands, index, count) for index in range(count)]
    ids = [[c["id"] for c in shard] for shard in shards]
    assert sum(len(x) for x in ids) == 3000
    assert len(set().union(*map(set, ids))) == 3000
    for left in range(count):
        for right in range(left + 1, count):
            assert set(ids[left]).isdisjoint(ids[right])


def test_shard_membership_uses_line_number():
    commands = [{"id": f"C{i}", "text": str(i)} for i in range(10)]
    shard = select_shard(commands, 2, 3)
    assert [c["id"] for c in shard] == ["C2", "C5", "C8"]


def test_two_workers_are_disjoint():
    commands = [{"id": f"C{i}", "text": str(i)} for i in range(101)]
    a = {c["id"] for c in select_shard(commands, 0, 2)}
    b = {c["id"] for c in select_shard(commands, 1, 2)}
    assert a | b == {c["id"] for c in commands}
    assert a.isdisjoint(b)


def test_invalid_shard_index_is_rejected():
    with pytest.raises(ValueError):
        select_shard([], 4, 4)
    with pytest.raises(ValueError):
        select_shard([], -1, 4)
