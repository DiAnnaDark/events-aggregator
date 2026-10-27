from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.events.schemas import EventDetail
from app.infrastructure.db.event_read_repository import (
    PostgresEventReadRepository,
)


class PostgresTicketEventReader:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def get_event(
        self,
        event_id: UUID,
    ) -> EventDetail | None:
        async with self._session_factory() as session:
            repository = PostgresEventReadRepository(session)

            return await repository.get_event(event_id)
