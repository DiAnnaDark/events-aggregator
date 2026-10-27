import asyncio
import logging
from contextlib import suppress
from typing import Protocol

from app.application.sync.service import (
    SyncAlreadyRunning,
    SyncResult,
)

logger = logging.getLogger(__name__)


class Synchronizer(Protocol):
    async def run(self) -> SyncResult: ...


class SyncRunner:
    def __init__(self, synchronizer: Synchronizer) -> None:
        self._synchronizer = synchronizer
        self._task: asyncio.Task[None] | None = None

    def trigger(self) -> bool:
        if self._task is not None and not self._task.done():
            return False

        self._task = asyncio.create_task(self._execute())
        return True

    async def stop(self) -> None:
        task = self._task

        if task is None:
            return

        if not task.done():
            task.cancel()

        with suppress(asyncio.CancelledError):
            await task

    async def _execute(self) -> None:
        try:
            result = await self._synchronizer.run()

            logger.info(
                "Synchronization completed: processed=%s",
                result.processed,
            )

        except SyncAlreadyRunning:
            logger.info("Synchronization skipped: another process is running")

        except asyncio.CancelledError:
            logger.info("Synchronization cancelled")
            raise

        except Exception:
            logger.exception("Background synchronization failed")
