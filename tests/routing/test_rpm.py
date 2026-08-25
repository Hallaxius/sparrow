import asyncio

import pytest

from sparrow.routing.rpm import RpmGovernor


@pytest.mark.asyncio
async def test_acquire_under_limit_returns_immediately():
    governor = RpmGovernor(default_rpm=36)
    await governor.acquire("p1", rpm=10)
    assert True


@pytest.mark.asyncio
async def test_acquire_enforces_per_provider_limit():
    governor = RpmGovernor(default_rpm=2)
    await governor.acquire("p1", rpm=2)
    await governor.acquire("p1", rpm=2)
    start = asyncio.get_event_loop().time()
    wait_task = asyncio.create_task(governor.acquire("p1", rpm=2))
    await asyncio.sleep(0.1)
    assert not wait_task.done()
    await wait_task
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed >= 0.1


@pytest.mark.asyncio
async def test_acquire_isolates_providers():
    governor = RpmGovernor(default_rpm=1)
    await governor.acquire("p1", rpm=1)
    await governor.acquire("p2", rpm=1)
    assert True


@pytest.mark.asyncio
async def test_acquire_zero_rpm_skips():
    governor = RpmGovernor(default_rpm=36)
    await governor.acquire("p1", rpm=0)
    assert True
