import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.application.tickets.recovery_scheduler import (
    TicketRecoveryScheduler,
)

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def test_scheduler_runs_on_startup() -> None:
    async def scenario() -> None:
        checked = asyncio.Event()

        async def cleanup(**kwargs):
            checked.set()
            return 3

        store = SimpleNamespace(
            expire_stale_prepared_operations=AsyncMock(side_effect=cleanup)
        )

        scheduler = TicketRecoveryScheduler(
            store=store,
            clock=lambda: NOW,
        )

        scheduler.start()

        await asyncio.wait_for(checked.wait(), timeout=1)
        await scheduler.stop()

        store.expire_stale_prepared_operations.assert_awaited_once_with(
            stale_before=NOW - timedelta(minutes=30),
            now=NOW,
            limit=100,
        )

    asyncio.run(scenario())


def test_scheduler_processes_multiple_batches() -> None:
    async def scenario() -> None:
        store = SimpleNamespace(
            expire_stale_prepared_operations=AsyncMock(side_effect=[100, 3])
        )

        scheduler = TicketRecoveryScheduler(
            store=store,
            clock=lambda: NOW,
        )

        total = await scheduler.run_once()

        assert total == 103
        assert store.expire_stale_prepared_operations.await_count == 2

    asyncio.run(scenario())


def test_scheduler_retries_after_database_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        recovered = asyncio.Event()
        calls = 0

        async def cleanup(**kwargs):
            nonlocal calls
            calls += 1

            if calls == 1:
                raise RuntimeError("Database unavailable")

            recovered.set()
            return 0

        store = SimpleNamespace(
            expire_stale_prepared_operations=AsyncMock(side_effect=cleanup)
        )

        scheduler = TicketRecoveryScheduler(
            store=store,
            poll_interval_seconds=0.001,
        )

        scheduler.start()

        await asyncio.wait_for(recovered.wait(), timeout=1)
        await scheduler.stop()

        assert calls >= 2

    asyncio.run(scenario())

    assert "Ticket recovery scheduler check failed" in caplog.text


def test_scheduler_stops_running_cleanup() -> None:
    async def scenario() -> None:
        started = asyncio.Event()

        async def cleanup(**kwargs):
            started.set()
            await asyncio.Event().wait()

        store = SimpleNamespace(
            expire_stale_prepared_operations=AsyncMock(side_effect=cleanup)
        )

        scheduler = TicketRecoveryScheduler(store=store)

        scheduler.start()

        await asyncio.wait_for(started.wait(), timeout=1)

        await asyncio.wait_for(scheduler.stop(), timeout=1)

        # Repeated shutdown must be safe.
        await scheduler.stop()

    asyncio.run(scenario())
