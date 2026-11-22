import asyncio
import logging
from collections.abc import Sequence
from contextlib import suppress
from typing import Protocol

logger = logging.getLogger(__name__)


class PendingOutboxStore(Protocol):
    async def get_pending(
        self,
        limit: int = 100,
    ) -> Sequence[object]: ...


class OutboxWorker:
    def __init__(
        self,
        *,
        store: PendingOutboxStore,
        poll_interval_seconds: float = 30.0,
        batch_size: int = 100,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("Poll interval must be positive")

        if batch_size <= 0:
            raise ValueError("Batch size must be positive")

        self._store = store
        self._poll_interval_seconds = poll_interval_seconds
        self._batch_size = batch_size
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
        pending = await self._store.get_pending(
            limit=self._batch_size,
        )
        return len(pending)

    async def _run(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Outbox worker poll failed")

            await asyncio.sleep(self._poll_interval_seconds)
