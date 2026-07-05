import asyncio

import pytest

from app.realtime import PollerHub


@pytest.mark.asyncio
async def test_subscriber_receives_initial_value_quickly():
    hub = PollerHub(interval_seconds=0.05)
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        return {"score": 10}

    queue = await hub.subscribe("board:1", fetch)
    value = await asyncio.wait_for(queue.get(), timeout=1)
    assert value == {"score": 10}
    await hub.unsubscribe("board:1", queue)


@pytest.mark.asyncio
async def test_no_push_when_value_unchanged():
    hub = PollerHub(interval_seconds=0.02)

    async def fetch():
        return {"score": 42}

    queue = await hub.subscribe("board:2", fetch)
    first = await asyncio.wait_for(queue.get(), timeout=1)
    assert first == {"score": 42}

    # Value never changes — queue should stay empty across several poll ticks.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue.get(), timeout=0.2)

    await hub.unsubscribe("board:2", queue)


@pytest.mark.asyncio
async def test_push_on_change_only():
    hub = PollerHub(interval_seconds=0.02)
    state = {"score": 1}

    async def fetch():
        return dict(state)

    queue = await hub.subscribe("board:3", fetch)
    first = await asyncio.wait_for(queue.get(), timeout=1)
    assert first == {"score": 1}

    state["score"] = 2
    second = await asyncio.wait_for(queue.get(), timeout=1)
    assert second == {"score": 2}

    await hub.unsubscribe("board:3", queue)


@pytest.mark.asyncio
async def test_multiple_subscribers_to_same_key_share_one_poll_task():
    hub = PollerHub(interval_seconds=0.02)
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        return {"score": 7}

    q1 = await hub.subscribe("board:shared", fetch)
    q2 = await hub.subscribe("board:shared", fetch)

    v1 = await asyncio.wait_for(q1.get(), timeout=1)
    v2 = await asyncio.wait_for(q2.get(), timeout=1)
    assert v1 == v2 == {"score": 7}
    assert len(hub.active_keys) == 1

    await hub.unsubscribe("board:shared", q1)
    assert "board:shared" in hub.active_keys  # q2 still subscribed
    await hub.unsubscribe("board:shared", q2)
    assert "board:shared" not in hub.active_keys  # task cancelled, no subscribers left


@pytest.mark.asyncio
async def test_fetch_errors_dont_crash_the_poll_loop():
    hub = PollerHub(interval_seconds=0.02)
    state = {"fail": True}

    async def fetch():
        if state["fail"]:
            raise RuntimeError("transient db error")
        return {"ok": True}

    queue = await hub.subscribe("board:flaky", fetch)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue.get(), timeout=0.15)

    state["fail"] = False
    value = await asyncio.wait_for(queue.get(), timeout=1)
    assert value == {"ok": True}

    await hub.unsubscribe("board:flaky", queue)


@pytest.mark.asyncio
async def test_unrelated_keys_are_independent():
    hub = PollerHub(interval_seconds=0.02)

    async def fetch_a():
        return {"board": "a"}

    async def fetch_b():
        return {"board": "b"}

    qa = await hub.subscribe("a", fetch_a)
    qb = await hub.subscribe("b", fetch_b)

    va = await asyncio.wait_for(qa.get(), timeout=1)
    vb = await asyncio.wait_for(qb.get(), timeout=1)
    assert va == {"board": "a"}
    assert vb == {"board": "b"}

    await hub.unsubscribe("a", qa)
    await hub.unsubscribe("b", qb)
