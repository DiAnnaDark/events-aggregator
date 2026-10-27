import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.application.sync.scheduler import DailySyncScheduler

NOW = datetime(2026, 10, 27, 18, tzinfo=UTC)


@pytest.mark.parametrize(
    ("last_sync", "expected_trigger"),
    [
        (None, True),
        (NOW - timedelta(days=1), True),
        (NOW - timedelta(hours=23), False),
    ],
)
def test_scheduler_checks_last_successful_sync(
    last_sync: datetime | None,
    expected_trigger: bool,
) -> None:
    async def scenario() -> None:
        checked = asyncio.Event()

        async def get_last_sync() -> datetime | None:
            checked.set()
            return last_sync

        trigger = Mock(return_value=True)

        scheduler = DailySyncScheduler(
            runner=SimpleNamespace(trigger=trigger),
            history=SimpleNamespace(get_last_successful_sync=get_last_sync),
            clock=lambda: NOW,
        )

        scheduler.start()

        await asyncio.wait_for(checked.wait(), timeout=1)
        await scheduler.stop()

        if expected_trigger:
            trigger.assert_called_once_with()
        else:
            trigger.assert_not_called()

    asyncio.run(scenario())


def test_scheduler_does_not_start_twice() -> None:
    async def scenario() -> None:
        checked = asyncio.Event()

        async def get_last_sync() -> datetime:
            checked.set()
            return NOW

        history = SimpleNamespace(
            get_last_successful_sync=AsyncMock(side_effect=get_last_sync)
        )

        scheduler = DailySyncScheduler(
            runner=SimpleNamespace(trigger=Mock()),
            history=history,
            clock=lambda: NOW,
        )

        scheduler.start()
        scheduler.start()

        await asyncio.wait_for(checked.wait(), timeout=1)
        await scheduler.stop()

        history.get_last_successful_sync.assert_awaited_once()

    asyncio.run(scenario())


def test_scheduler_rechecks_after_interval() -> None:
    async def scenario() -> None:
        triggered = asyncio.Event()
        checks = 0

        async def get_last_sync() -> datetime | None:
            nonlocal checks
            checks += 1

            if checks == 1:
                return NOW

            return None

        def trigger() -> bool:
            triggered.set()
            return True

        scheduler = DailySyncScheduler(
            runner=SimpleNamespace(trigger=trigger),
            history=SimpleNamespace(get_last_successful_sync=get_last_sync),
            clock=lambda: NOW,
            poll_interval_seconds=0.001,
        )

        scheduler.start()

        await asyncio.wait_for(triggered.wait(), timeout=1)
        await scheduler.stop()

        assert checks >= 2

    asyncio.run(scenario())


def test_scheduler_cancels_running_check() -> None:
    async def scenario() -> None:
        started = asyncio.Event()

        async def get_last_sync() -> None:
            started.set()
            await asyncio.Event().wait()

        scheduler = DailySyncScheduler(
            runner=SimpleNamespace(trigger=Mock()),
            history=SimpleNamespace(get_last_successful_sync=get_last_sync),
        )

        scheduler.start()

        await asyncio.wait_for(started.wait(), timeout=1)
        await asyncio.wait_for(scheduler.stop(), timeout=1)

        await scheduler.stop()

    asyncio.run(scenario())


def test_scheduler_logs_database_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        checked = asyncio.Event()

        async def get_last_sync() -> None:
            checked.set()
            raise RuntimeError("Database unavailable")

        scheduler = DailySyncScheduler(
            runner=SimpleNamespace(trigger=Mock()),
            history=SimpleNamespace(get_last_successful_sync=get_last_sync),
        )

        scheduler.start()

        await asyncio.wait_for(checked.wait(), timeout=1)
        await scheduler.stop()

    asyncio.run(scenario())

    assert "Daily synchronization scheduler check failed" in caplog.text
