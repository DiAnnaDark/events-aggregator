import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Protocol

logger = logging.getLogger(__name__)


class SyncTrigger(Protocol):
    def trigger(self) -> bool: ...


class SyncHistory(Protocol):
    async def get_last_successful_sync(
        self,
    ) -> datetime | None: ...


class DailySyncScheduler:
    def __init__(
        self,
        *,
        runner: SyncTrigger,
        history: SyncHistory,
        clock: Callable[[], datetime] | None = None,
        poll_interval_seconds: float = 3600,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("Poll interval must be positive")

        self._runner = runner
        self._history = history
        self._clock = clock or (lambda: datetime.now(UTC))
        self._poll_interval_seconds = poll_interval_seconds

        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return

        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task = self._task

        if task is None:
            return

        task.cancel()

        with suppress(asyncio.CancelledError):
            await task

        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                now = self._clock()
                last_sync = await self._history.get_last_successful_sync()

                synchronization_due = last_sync is None or now - last_sync >= timedelta(
                    days=1
                )

                if synchronization_due:
                    if self._runner.trigger():
                        logger.info("Scheduled daily synchronization started")
                    else:
                        logger.info(
                            "Scheduled synchronization skipped: already running"
                        )

            except asyncio.CancelledError:
                raise

            except Exception:
                logger.exception("Daily synchronization scheduler check failed")

            await asyncio.sleep(self._poll_interval_seconds)
