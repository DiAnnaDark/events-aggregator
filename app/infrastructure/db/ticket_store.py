from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.tickets.schemas import TicketCreate
from app.application.tickets.state import TicketCancellationSnapshot
from app.infrastructure.db.ticket_repository import PostgresTicketRepository


class PostgresTicketStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def reserve_registration(
        self,
        *,
        registration_id: UUID,
        operation_id: UUID,
        request: TicketCreate,
        request_fingerprint: str,
        now: datetime,
    ) -> bool:
        async with self._session_factory.begin() as session:
            repository = PostgresTicketRepository(session)

            return await repository.reserve_registration(
                registration_id=registration_id,
                operation_id=operation_id,
                request=request,
                request_fingerprint=request_fingerprint,
                now=now,
            )

    async def mark_registration_in_flight(
        self,
        *,
        registration_id: UUID,
        operation_id: UUID,
        now: datetime,
    ) -> bool:
        async with self._session_factory.begin() as session:
            repository = PostgresTicketRepository(session)

            return await repository.mark_registration_in_flight(
                registration_id=registration_id,
                operation_id=operation_id,
                now=now,
            )

    async def finish_registration_success(
        self,
        *,
        registration_id: UUID,
        operation_id: UUID,
        provider_ticket_id: UUID,
        now: datetime,
    ) -> bool:
        async with self._session_factory.begin() as session:
            repository = PostgresTicketRepository(session)

            return await repository.finish_registration_success(
                registration_id=registration_id,
                operation_id=operation_id,
                provider_ticket_id=provider_ticket_id,
                now=now,
            )

    async def record_registration_error(
        self,
        *,
        registration_id: UUID,
        operation_id: UUID,
        outcome: Literal["failed", "unknown"],
        provider_http_status: int | None,
        error_code: str,
        now: datetime,
    ) -> bool:
        async with self._session_factory.begin() as session:
            repository = PostgresTicketRepository(session)

            return await repository.record_registration_error(
                registration_id=registration_id,
                operation_id=operation_id,
                outcome=outcome,
                provider_http_status=provider_http_status,
                error_code=error_code,
                now=now,
            )

    async def expire_prepared_registration(
        self,
        *,
        operation_id: UUID,
        stale_before: datetime,
        now: datetime,
    ) -> bool:
        async with self._session_factory.begin() as session:
            repository = PostgresTicketRepository(session)

            return await repository.expire_prepared_registration(
                operation_id=operation_id,
                stale_before=stale_before,
                now=now,
            )

    async def get_ticket_for_cancellation(
        self,
        *,
        ticket_id: UUID,
    ) -> TicketCancellationSnapshot | None:
        async with self._session_factory() as session:
            repository = PostgresTicketRepository(session)

            return await repository.get_ticket_for_cancellation(
                ticket_id=ticket_id,
            )
