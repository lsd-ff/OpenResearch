import asyncio

import pytest

from openresearch.executor.gates import Gatekeeper, ResourceConfig, WeightedSemaphore


async def test_weighted_queue_is_fifo_and_small_requests_cannot_starve_a_large_one():
    semaphore = WeightedSemaphore(3)
    await semaphore.acquire(3)
    large = asyncio.create_task(semaphore.acquire(3))
    small = asyncio.create_task(semaphore.acquire(1))
    await asyncio.sleep(0)
    semaphore.release(1)
    assert not large.done() and not small.done()
    semaphore.release(2)
    await large
    assert semaphore.usage.in_use == 3 and not small.done()
    semaphore.release(3)
    await small
    assert semaphore.usage.in_use == 1
    semaphore.release(1)
    assert semaphore.usage.in_use == semaphore.usage.waiting == 0


async def test_canceling_head_waiter_wakes_next_fitting_request():
    semaphore = WeightedSemaphore(2)
    await semaphore.acquire(1)
    large = asyncio.create_task(semaphore.acquire(2))
    small = asyncio.create_task(semaphore.acquire(1))
    await asyncio.sleep(0)
    large.cancel()
    with pytest.raises(asyncio.CancelledError):
        await large
    await small
    assert semaphore.usage.in_use == 2 and semaphore.usage.waiting == 0
    semaphore.release(2)


async def test_canceling_after_grant_returns_reserved_weight_exactly_once():
    semaphore = WeightedSemaphore(1)
    await semaphore.acquire()
    waiting = asyncio.create_task(semaphore.acquire())
    await asyncio.sleep(0)
    semaphore.release()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert semaphore.usage.in_use == semaphore.usage.waiting == 0
    async with semaphore.hold():
        assert semaphore.usage.in_use == 1
    assert semaphore.usage.in_use == 0


async def test_exception_inside_reservation_releases_all_weights():
    gates = Gatekeeper(ResourceConfig(max_concurrent_io=3, max_gpu_tasks=2))
    with pytest.raises(RuntimeError, match="business failure"):
        async with gates.reserve(io_weight=2, gpu_weight=2):
            assert gates.io_usage.in_use == gates.gpu_usage.in_use == 2
            raise RuntimeError("business failure")
    assert gates.io_usage.in_use == gates.gpu_usage.in_use == 0


async def test_canceling_gpu_wait_does_not_leak_already_acquired_io_weight():
    gates = Gatekeeper(ResourceConfig(max_concurrent_io=1, max_gpu_tasks=1))
    await gates.acquire_gpu()
    async with gates.reserve():
        assert gates.io_usage.in_use == 1 and gates.gpu_usage.in_use == 1

    # 明确请求 GPU；CPU 路径的 gpu_weight=0 不应等待 GPU。
    async def gpu_waiting():
        async with gates.reserve(io_weight=1, gpu_weight=1):
            pytest.fail("GPU capacity is still held")

    task = asyncio.create_task(gpu_waiting())
    await asyncio.sleep(0)
    assert gates.io_usage.in_use == 1 and gates.gpu_usage.waiting == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gates.io_usage.in_use == 0 and gates.gpu_usage.in_use == 1
    gates.release_gpu()


@pytest.mark.parametrize("weight", [-1, 3, True, 0.5])
async def test_invalid_or_impossible_weight_fails_without_changing_capacity(weight):
    semaphore = WeightedSemaphore(2)
    with pytest.raises(ValueError):
        await semaphore.acquire(weight)
    assert semaphore.usage.in_use == semaphore.usage.waiting == 0
    with pytest.raises(ValueError):
        semaphore.release(1)


async def test_cpu_only_capacity_and_manual_go_style_gate_methods():
    gates = Gatekeeper(ResourceConfig(max_concurrent_io=2, max_gpu_tasks=0))
    async with gates.reserve(io_weight=2, gpu_weight=0):
        assert gates.io_usage.in_use == 2 and gates.gpu_usage.in_use == 0
    await gates.acquire_io(2)
    gates.release_io(2)
    with pytest.raises(ValueError):
        async with gates.reserve(io_weight=1, gpu_weight=1):
            pytest.fail("unavailable GPU weight cannot be acquired")
    assert gates.io_usage.in_use == 0


def test_gate_rejects_reuse_from_another_event_loop():
    semaphore = WeightedSemaphore(1)

    async def use():
        async with semaphore.hold():
            pass

    asyncio.run(use())
    with pytest.raises(RuntimeError, match="event loop"):
        asyncio.run(use())


async def test_many_weighted_tasks_never_exceed_capacity():
    semaphore = WeightedSemaphore(5)
    active = 0
    peak = 0

    async def work(weight: int):
        nonlocal active, peak
        async with semaphore.hold(weight):
            active += weight
            peak = max(active, peak)
            await asyncio.sleep(0)
            assert active <= 5
            active -= weight

    await asyncio.gather(*(work(index % 3 + 1) for index in range(50)))
    assert 1 <= peak <= 5 and active == 0
    assert semaphore.usage.in_use == semaphore.usage.waiting == 0
