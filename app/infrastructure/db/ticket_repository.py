from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.tickets.schemas import TicketCreate
from app.application.tickets.state import TicketCancellationSnapshot
from app.infrastructure.db.models import Event, Ticket, TicketOperation


class TicketStateConflict(RuntimeError):
    """Ticket state does not allow the requested transition."""


class PostgresTicketRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reserve_registration(
        self,
        *,
        registration_id: UUID,
        operation_id: UUID,
        request: TicketCreate,
        request_fingerprint: str,
        now: datetime,
    ) -> bool:
        ticket_insert = (
            insert(Ticket)
            .values(
                id=registration_id,
                provider_ticket_id=None,
                event_id=request.event_id,
                first_name=request.first_name,
                last_name=request.last_name,
                email=str(request.email),
                seat=request.seat,
                status="pending",
                created_at=now,
                registered_at=None,
                updated_at=now,
                cancelled_at=None,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    Ticket.event_id,
                    Ticket.seat,
                ],
                index_where=text("status IN ('pending', 'active', 'uncertain')"),
            )
            .returning(Ticket.id)
        )

        result = await self._session.execute(ticket_insert)

        if result.scalar_one_or_none() is None:
            return False

        operation_insert = insert(TicketOperation).values(
            id=operation_id,
            registration_id=registration_id,
            operation_type="register",
            idempotency_key=None,
            request_fingerprint=request_fingerprint,
            status="prepared",
            provider_http_status=None,
            error_code=None,
            created_at=now,
            updated_at=now,
            finished_at=None,
        )

        await self._session.execute(operation_insert)

        return True

    async def mark_registration_in_flight(
        self,
        *,
        registration_id: UUID,
        operation_id: UUID,
        now: datetime,
    ) -> bool:
        pending_ticket = (
            select(Ticket.id)
            .where(
                Ticket.id == registration_id,
                Ticket.status == "pending",
            )
            .exists()
        )

        statement = (
            update(TicketOperation)
            .where(
                TicketOperation.id == operation_id,
                TicketOperation.registration_id == registration_id,
                TicketOperation.operation_type == "register",
                TicketOperation.status == "prepared",
                pending_ticket,
            )
            .values(
                status="in_flight",
                updated_at=now,
            )
            .returning(TicketOperation.id)
        )

        result = await self._session.execute(statement)

        return result.scalar_one_or_none() is not None

    async def finish_registration_success(
        self,
        *,
        registration_id: UUID,
        operation_id: UUID,
        provider_ticket_id: UUID,
        now: datetime,
    ) -> bool:
        operation_statement = (
            update(TicketOperation)
            .where(
                TicketOperation.id == operation_id,
                TicketOperation.registration_id == registration_id,
                TicketOperation.operation_type == "register",
                TicketOperation.status == "in_flight",
            )
            .values(
                status="success",
                provider_http_status=201,
                error_code=None,
                updated_at=now,
                finished_at=now,
            )
            .returning(TicketOperation.id)
        )

        operation_result = await self._session.execute(operation_statement)

        if operation_result.scalar_one_or_none() is None:
            return False

        ticket_statement = (
            update(Ticket)
            .where(
                Ticket.id == registration_id,
                Ticket.status == "pending",
            )
            .values(
                provider_ticket_id=provider_ticket_id,
                status="active",
                registered_at=now,
                updated_at=now,
            )
            .returning(Ticket.id)
        )

        ticket_result = await self._session.execute(ticket_statement)

        if ticket_result.scalar_one_or_none() is None:
            raise TicketStateConflict(
                "Ticket state changed during registration confirmation"
            )

        return True

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
        if outcome not in ("failed", "unknown"):
            raise ValueError("Unsupported registration outcome")

        ticket_status = "failed" if outcome == "failed" else "uncertain"

        finished_at = now if outcome == "failed" else None

        operation_statement = (
            update(TicketOperation)
            .where(
                TicketOperation.id == operation_id,
                TicketOperation.registration_id == registration_id,
                TicketOperation.operation_type == "register",
                TicketOperation.status == "in_flight",
            )
            .values(
                status=outcome,
                provider_http_status=provider_http_status,
                error_code=error_code,
                updated_at=now,
                finished_at=finished_at,
            )
            .returning(TicketOperation.id)
        )

        operation_result = await self._session.execute(operation_statement)

        if operation_result.scalar_one_or_none() is None:
            return False

        ticket_statement = (
            update(Ticket)
            .where(
                Ticket.id == registration_id,
                Ticket.status == "pending",
            )
            .values(
                status=ticket_status,
                updated_at=now,
            )
            .returning(Ticket.id)
        )

        ticket_result = await self._session.execute(ticket_statement)

        if ticket_result.scalar_one_or_none() is None:
            raise TicketStateConflict(
                "Ticket state changed during registration error handling"
            )

        return True

    async def expire_prepared_registration(
        self,
        *,
        operation_id: UUID,
        stale_before: datetime,
        now: datetime,
    ) -> bool:
        operation_statement = (
            update(TicketOperation)
            .where(
                TicketOperation.id == operation_id,
                TicketOperation.operation_type == "register",
                TicketOperation.status == "prepared",
                TicketOperation.updated_at < stale_before,
            )
            .values(
                status="failed",
                error_code="stale_prepared",
                updated_at=now,
                finished_at=now,
            )
            .returning(TicketOperation.registration_id)
        )

        operation_result = await self._session.execute(operation_statement)

        registration_id = operation_result.scalar_one_or_none()

        if registration_id is None:
            return False

        ticket_statement = (
            update(Ticket)
            .where(
                Ticket.id == registration_id,
                Ticket.status == "pending",
            )
            .values(
                status="failed",
                updated_at=now,
            )
            .returning(Ticket.id)
        )

        ticket_result = await self._session.execute(ticket_statement)

        if ticket_result.scalar_one_or_none() is None:
            raise TicketStateConflict(
                "Ticket state changed during stale reservation cleanup"
            )

        return True

    async def get_ticket_for_cancellation(
        self,
        *,
        ticket_id: UUID,
    ) -> TicketCancellationSnapshot | None:
        statement = (
            select(
                Ticket.id.label("ticket_id"),
                Ticket.event_id,
                Ticket.provider_ticket_id,
                Ticket.status,
                Event.event_time,
            )
            .join(
                Event,
                Event.id == Ticket.event_id,
            )
            .where(Ticket.id == ticket_id)
        )

        result = await self._session.execute(statement)

        data = result.mappings().one_or_none()

        if data is None:
            return None

        return TicketCancellationSnapshot(**data)

    async def prepare_cancellation(
        self,
        *,
        ticket_id: UUID,
        operation_id: UUID,
        request_fingerprint: str,
        now: datetime,
    ) -> bool:
        ticket_statement = (
            select(Ticket.id)
            .join(Event, Event.id == Ticket.event_id)
            .where(
                Ticket.id == ticket_id,
                Ticket.status == "active",
                Ticket.provider_ticket_id.is_not(None),
                Event.event_time > now,
            )
            .with_for_update(of=Ticket)
        )

        ticket_result = await self._session.execute(ticket_statement)

        if ticket_result.scalar_one_or_none() is None:
            return False

        operation_statement = (
            insert(TicketOperation)
            .values(
                id=operation_id,
                registration_id=ticket_id,
                operation_type="cancel",
                idempotency_key=None,
                request_fingerprint=request_fingerprint,
                status="prepared",
                provider_http_status=None,
                error_code=None,
                created_at=now,
                updated_at=now,
                finished_at=None,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    TicketOperation.registration_id,
                ],
                index_where=text("status IN ('prepared', 'in_flight', 'unknown')"),
            )
            .returning(TicketOperation.id)
        )

        operation_result = await self._session.execute(operation_statement)

        return operation_result.scalar_one_or_none() is not None

    async def mark_cancellation_in_flight(
        self,
        *,
        ticket_id: UUID,
        operation_id: UUID,
        now: datetime,
    ) -> bool:
        active_ticket = (
            select(Ticket.id)
            .join(Event, Event.id == Ticket.event_id)
            .where(
                Ticket.id == ticket_id,
                Ticket.status == "active",
                Ticket.provider_ticket_id.is_not(None),
                Event.event_time > now,
            )
            .exists()
        )

        statement = (
            update(TicketOperation)
            .where(
                TicketOperation.id == operation_id,
                TicketOperation.registration_id == ticket_id,
                TicketOperation.operation_type == "cancel",
                TicketOperation.status == "prepared",
                active_ticket,
            )
            .values(
                status="in_flight",
                updated_at=now,
            )
            .returning(TicketOperation.id)
        )

        result = await self._session.execute(statement)

        return result.scalar_one_or_none() is not None

    async def finish_cancellation_success(
        self,
        *,
        ticket_id: UUID,
        operation_id: UUID,
        now: datetime,
    ) -> bool:
        operation_statement = (
            update(TicketOperation)
            .where(
                TicketOperation.id == operation_id,
                TicketOperation.registration_id == ticket_id,
                TicketOperation.operation_type == "cancel",
                TicketOperation.status == "in_flight",
            )
            .values(
                status="success",
                provider_http_status=200,
                error_code=None,
                updated_at=now,
                finished_at=now,
            )
            .returning(TicketOperation.id)
        )

        operation_result = await self._session.execute(operation_statement)

        if operation_result.scalar_one_or_none() is None:
            return False

        ticket_statement = (
            update(Ticket)
            .where(
                Ticket.id == ticket_id,
                Ticket.status == "active",
                Ticket.provider_ticket_id.is_not(None),
            )
            .values(
                status="cancelled",
                cancelled_at=now,
                updated_at=now,
            )
            .returning(Ticket.id)
        )

        ticket_result = await self._session.execute(ticket_statement)

        if ticket_result.scalar_one_or_none() is None:
            raise TicketStateConflict(
                "Ticket state changed during cancellation confirmation"
            )

        return True

    async def record_cancellation_error(
        self,
        *,
        ticket_id: UUID,
        operation_id: UUID,
        outcome: Literal["failed", "unknown"],
        provider_http_status: int | None,
        error_code: str,
        now: datetime,
    ) -> bool:
        if outcome not in ("failed", "unknown"):
            raise ValueError("Unsupported cancellation outcome")

        finished_at = now if outcome == "failed" else None

        statement = (
            update(TicketOperation)
            .where(
                TicketOperation.id == operation_id,
                TicketOperation.registration_id == ticket_id,
                TicketOperation.operation_type == "cancel",
                TicketOperation.status == "in_flight",
            )
            .values(
                status=outcome,
                provider_http_status=provider_http_status,
                error_code=error_code,
                updated_at=now,
                finished_at=finished_at,
            )
            .returning(TicketOperation.id)
        )

        result = await self._session.execute(statement)

        return result.scalar_one_or_none() is not None

    async def abort_prepared_cancellation(
        self,
        *,
        ticket_id: UUID,
        operation_id: UUID,
        now: datetime,
    ) -> bool:
        statement = (
            update(TicketOperation)
            .where(
                TicketOperation.id == operation_id,
                TicketOperation.registration_id == ticket_id,
                TicketOperation.operation_type == "cancel",
                TicketOperation.status == "prepared",
            )
            .values(
                status="failed",
                error_code="cancellation_not_started",
                updated_at=now,
                finished_at=now,
            )
            .returning(TicketOperation.id)
        )

        result = await self._session.execute(statement)

        return result.scalar_one_or_none() is not None
