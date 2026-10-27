from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from app.application.sync.schemas import EventSnapshot
from app.application.sync.state import SyncLease
from app.infrastructure.db.event_repository import (
    PostgresEventRepository,
)
from app.infrastructure.db.sync_state_repository import (
    PostgresSyncStateRepository,
)


class PostgresSyncStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def try_start(
        self,
        *,
        now: datetime,
        stale_before: datetime,
    ) -> SyncLease | None:
        async with self._session_factory.begin() as session:
            repository = PostgresSyncStateRepository(session)

            return await repository.try_start(
                now=now,
                stale_before=stale_before,
            )

    async def save_batch(
        self,
        *,
        lease: SyncLease,
        events: Sequence[EventSnapshot],
        now: datetime,
    ) -> bool:
        async with self._session_factory.begin() as session:
            state_repository = PostgresSyncStateRepository(session)

            active = await state_repository.heartbeat(
                lease=lease,
                now=now,
            )

            if not active:
                return False

            event_repository = PostgresEventRepository(session)

            for event in events:
                await event_repository.upsert(event)

            return True

    async def finish_success(
        self,
        *,
        lease: SyncLease,
        now: datetime,
        newest_changed_at: datetime | None,
    ) -> bool:
        async with self._session_factory.begin() as session:
            repository = PostgresSyncStateRepository(session)

            return await repository.finish_success(
                lease=lease,
                now=now,
                newest_changed_at=newest_changed_at,
            )

    async def finish_failure(
        self,
        *,
        lease: SyncLease,
        now: datetime,
        error: str,
    ) -> bool:
        async with self._session_factory.begin() as session:
            repository = PostgresSyncStateRepository(session)

            return await repository.finish_failure(
                lease=lease,
                now=now,
                error=error,
            )

    async def get_last_successful_sync(self) -> datetime | None:
        async with self._session_factory() as session:
            repository = PostgresSyncStateRepository(session)

            return await repository.get_last_successful_sync()
