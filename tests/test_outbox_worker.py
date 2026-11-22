import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.application.outbox.worker import OutboxWorker


def test_worker_run_once() -> None:
    async def scenario() -> None:
        store = SimpleNamespace(
            get_pending=AsyncMock(return_value=[object(), object()])
        )

        worker = OutboxWorker(store=store, batch_size=2)

        result = await worker.run_once()

        assert result == 2
        store.get_pending.assert_awaited_once_with(limit=2)

    asyncio.run(scenario())


def test_worker_start_stop_and_polling() -> None:
    async def scenario() -> None:
        polled_twice = asyncio.Event()
        calls = 0

        async def get_pending(*, limit: int) -> list[object]:
            nonlocal calls
            calls += 1

            if calls >= 2:
                polled_twice.set()

            return []

        store = SimpleNamespace(get_pending=get_pending)
        worker = OutboxWorker(
            store=store,
            poll_interval_seconds=0.01,
        )

        worker.start()

        try:
            await asyncio.wait_for(polled_twice.wait(), timeout=2)
        finally:
            await worker.stop()

        assert calls >= 2

    asyncio.run(scenario())


def test_worker_retries_after_error() -> None:
    async def scenario() -> None:
        retried = asyncio.Event()
        calls = 0

        async def get_pending(*, limit: int) -> list[object]:
            nonlocal calls
            calls += 1

            if calls == 1:
                raise RuntimeError("Temporary database error")

            retried.set()
            return []

        store = SimpleNamespace(get_pending=get_pending)
        worker = OutboxWorker(
            store=store,
            poll_interval_seconds=0.01,
        )

        worker.start()

        try:
            await asyncio.wait_for(retried.wait(), timeout=2)
        finally:
            await worker.stop()

        assert calls >= 2

    asyncio.run(scenario())
