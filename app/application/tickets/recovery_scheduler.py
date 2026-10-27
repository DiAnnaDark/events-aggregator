import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Protocol

logger = logging.getLogger(__name__)


class PreparedOperationsStore(Protocol):
    async def expire_stale_prepared_operations(
        self,
        *,
        stale_before: datetime,
        now: datetime,
        limit: int = 100,
    ) -> int: ...


class TicketRecoveryScheduler:
    def __init__(
        self,
        *,
        store: PreparedOperationsStore,
        clock: Callable[[], datetime] | None = None,
        poll_interval_seconds: float = 3600,
        stale_after: timedelta = timedelta(minutes=30),
        batch_size: int = 100,
        max_batches: int = 10,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("Poll interval must be positive")

        if stale_after <= timedelta(0):
            raise ValueError("Stale interval must be positive")

        if not 1 <= batch_size <= 500:
            raise ValueError("Invalid batch size")

        if max_batches < 1:
            raise ValueError("Max batches must be positive")

        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._poll_interval_seconds = poll_interval_seconds
        self._stale_after = stale_after
        self._batch_size = batch_size
        self._max_batches = max_batches

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

    async def run_once(self) -> int:
        now = self._clock()
        expired_total = 0

        for _ in range(self._max_batches):
            expired = await self._store.expire_stale_prepared_operations(
                stale_before=now - self._stale_after,
                now=now,
                limit=self._batch_size,
            )

            expired_total += expired

            if expired < self._batch_size:
                break

        return expired_total

    async def _run(self) -> None:
        while True:
            try:
                expired = await self.run_once()

                if expired:
                    logger.info(
                        "Expired stale prepared ticket operations: %s",
                        expired,
                    )

            except asyncio.CancelledError:
                raise

            except Exception:
                logger.exception("Ticket recovery scheduler check failed")

            await asyncio.sleep(self._poll_interval_seconds)
