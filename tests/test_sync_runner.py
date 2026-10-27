import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.application.sync.runner import SyncRunner
from app.application.sync.service import SyncResult


def test_runner_starts_synchronization() -> None:
    async def scenario() -> None:
        synchronizer = SimpleNamespace(
            run=AsyncMock(
                return_value=SyncResult(
                    processed=3,
                    last_changed_at=None,
                )
            )
        )

        runner = SyncRunner(synchronizer)

        assert runner.trigger() is True

        await asyncio.sleep(0)
        await runner.stop()

        synchronizer.run.assert_awaited_once()

    asyncio.run(scenario())


def test_runner_rejects_parallel_start() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        finish = asyncio.Event()

        async def fake_run() -> SyncResult:
            started.set()
            await finish.wait()

            return SyncResult(
                processed=1,
                last_changed_at=None,
            )

        synchronizer = SimpleNamespace(run=fake_run)
        runner = SyncRunner(synchronizer)

        assert runner.trigger() is True

        await started.wait()

        assert runner.trigger() is False

        finish.set()
        await runner.stop()

    asyncio.run(scenario())


def test_runner_logs_background_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        synchronizer = SimpleNamespace(
            run=AsyncMock(side_effect=RuntimeError("Test failure"))
        )

        runner = SyncRunner(synchronizer)

        assert runner.trigger() is True

        await asyncio.sleep(0)
        await runner.stop()

    asyncio.run(scenario())

    assert "Background synchronization failed" in caplog.text


def test_runner_cancels_running_task() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def fake_run() -> SyncResult:
            started.set()

            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

            return SyncResult(
                processed=0,
                last_changed_at=None,
            )

        synchronizer = SimpleNamespace(run=fake_run)
        runner = SyncRunner(synchronizer)

        assert runner.trigger() is True

        await started.wait()
        await runner.stop()

        assert cancelled.is_set()

    asyncio.run(scenario())
