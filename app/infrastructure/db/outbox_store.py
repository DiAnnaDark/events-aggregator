from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.db.models import Outbox


class PostgresOutboxStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def get_pending(self, limit: int = 100) -> list[Outbox]:
        async with self._session_factory() as session:
            statement = (
                select(Outbox)
                .where(Outbox.status == "pending")
                .order_by(Outbox.created_at, Outbox.id)
                .limit(limit)
            )

            result = await session.scalars(statement)
            return list(result.all())

    async def mark_sent(self, event_id: UUID) -> None:
        async with self._session_factory.begin() as session:
            statement = (
                update(Outbox)
                .where(
                    Outbox.id == event_id,
                    Outbox.status == "pending",
                )
                .values(status="sent")
            )
            await session.execute(statement)
