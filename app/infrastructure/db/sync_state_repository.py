from datetime import datetime

from sqlalchemy import case, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.sync.state import SyncLease
from app.infrastructure.db.models import SyncState


class PostgresSyncStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def try_start(
        self,
        *,
        now: datetime,
        stale_before: datetime,
    ) -> SyncLease | None:
        statement = (
            update(SyncState)
            .where(
                SyncState.id == 1,
                or_(
                    SyncState.sync_status != "running",
                    SyncState.updated_at < stale_before,
                ),
            )
            .values(
                sync_status="running",
                started_at=now,
                finished_at=None,
                last_error=None,
                updated_at=now,
            )
            .returning(
                SyncState.started_at,
                SyncState.last_changed_at,
            )
        )

        result = await self._session.execute(statement)
        row = result.one_or_none()

        if row is None:
            return None

        return SyncLease(
            started_at=row[0],
            last_changed_at=row[1],
        )

    async def heartbeat(
        self,
        *,
        lease: SyncLease,
        now: datetime,
    ) -> bool:
        statement = (
            update(SyncState)
            .where(
                SyncState.id == 1,
                SyncState.sync_status == "running",
                SyncState.started_at == lease.started_at,
            )
            .values(updated_at=now)
            .returning(SyncState.id)
        )

        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def finish_success(
        self,
        *,
        lease: SyncLease,
        now: datetime,
        newest_changed_at: datetime | None,
    ) -> bool:
        values = {
            "sync_status": "success",
            "last_sync_time": now,
            "finished_at": now,
            "last_error": None,
            "updated_at": now,
        }

        if newest_changed_at is not None:
            values["last_changed_at"] = case(
                (
                    SyncState.last_changed_at.is_(None),
                    newest_changed_at,
                ),
                (
                    SyncState.last_changed_at < newest_changed_at,
                    newest_changed_at,
                ),
                else_=SyncState.last_changed_at,
            )

        statement = (
            update(SyncState)
            .where(
                SyncState.id == 1,
                SyncState.sync_status == "running",
                SyncState.started_at == lease.started_at,
            )
            .values(**values)
            .returning(SyncState.id)
        )

        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def finish_failure(
        self,
        *,
        lease: SyncLease,
        now: datetime,
        error: str,
    ) -> bool:
        statement = (
            update(SyncState)
            .where(
                SyncState.id == 1,
                SyncState.sync_status == "running",
                SyncState.started_at == lease.started_at,
            )
            .values(
                sync_status="failed",
                finished_at=now,
                last_error=error[:1000],
                updated_at=now,
            )
            .returning(SyncState.id)
        )

        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def get_last_successful_sync(self) -> datetime | None:
        statement = select(SyncState.last_sync_time).where(SyncState.id == 1)

        return await self._session.scalar(statement)
