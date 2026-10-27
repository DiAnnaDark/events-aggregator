import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from app.application.sync.schemas import EventSnapshot
from app.application.sync.state import SyncLease

logger = logging.getLogger(__name__)


class SyncAlreadyRunning(RuntimeError):
    pass


class SyncLeaseLost(RuntimeError):
    pass


class EventsSource(Protocol):
    def events_since(
        self,
        changed_at: date,
    ) -> AsyncIterator[dict[str, Any]]: ...


class SyncStore(Protocol):
    async def try_start(
        self,
        *,
        now: datetime,
        stale_before: datetime,
    ) -> SyncLease | None: ...

    async def save_batch(
        self,
        *,
        lease: SyncLease,
        events: Sequence[EventSnapshot],
        now: datetime,
    ) -> bool: ...

    async def finish_success(
        self,
        *,
        lease: SyncLease,
        now: datetime,
        newest_changed_at: datetime | None,
    ) -> bool: ...

    async def finish_failure(
        self,
        *,
        lease: SyncLease,
        now: datetime,
        error: str,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class SyncResult:
    processed: int
    last_changed_at: datetime | None


class SyncService:
    def __init__(
        self,
        *,
        source: EventsSource,
        store: SyncStore,
        clock: Callable[[], datetime] | None = None,
        batch_size: int = 100,
    ) -> None:
        if batch_size < 1:
            raise ValueError("Batch size must be positive")

        self._source = source
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._batch_size = batch_size

    async def run(self) -> SyncResult:
        now = self._clock()

        lease = await self._store.try_start(
            now=now,
            stale_before=now - timedelta(minutes=5),
        )

        if lease is None:
            raise SyncAlreadyRunning

        changed_at = lease.last_changed_at
        since = changed_at.date() if changed_at else date(2000, 1, 1)

        processed = 0
        batch: list[EventSnapshot] = []

        try:
            async for raw_event in self._source.events_since(since):
                event = EventSnapshot.model_validate(raw_event)

                if changed_at is None or event.changed_at > changed_at:
                    changed_at = event.changed_at

                batch.append(event)
                processed += 1

                if len(batch) >= self._batch_size:
                    await self._save_batch(lease, batch)
                    batch.clear()

            if batch:
                await self._save_batch(lease, batch)

            completed = await self._store.finish_success(
                lease=lease,
                now=self._clock(),
                newest_changed_at=changed_at,
            )

            if not completed:
                raise SyncLeaseLost

        except (Exception, asyncio.CancelledError) as exc:
            try:
                await self._store.finish_failure(
                    lease=lease,
                    now=self._clock(),
                    error=f"{type(exc).__name__}: {exc}"[:1000],
                )
            except Exception:
                logger.exception("Failed to record sync failure")

            raise

        logger.info("Events sync completed: processed=%s", processed)

        return SyncResult(
            processed=processed,
            last_changed_at=changed_at,
        )

    async def _save_batch(
        self,
        lease: SyncLease,
        events: Sequence[EventSnapshot],
    ) -> None:
        saved = await self._store.save_batch(
            lease=lease,
            events=tuple(events),
            now=self._clock(),
        )

        if not saved:
            raise SyncLeaseLost
