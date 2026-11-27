from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.tickets.schemas import TicketCreate
from app.application.tickets.state import TicketCancellationSnapshot
from app.core.enums import (
    OperationStatus,
    OperationType,
    OutboxEventType,
    OutboxStatus,
    TicketStatus,
)
from app.infrastructure.db.models import Event, Outbox, Ticket, TicketOperation


class TicketStateConflict(RuntimeError):
    """Ticket state does not allow the requested transition."""


class PostgresTicketRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_registration_by_key(
        self,
        key: str,
        *,
        ttl_hours: int = 24,
    ) -> TicketOperation | None:
        expired_before = datetime.now(UTC) - timedelta(hours=ttl_hours)

        expire_statement = (
            update(TicketOperation)
            .where(
                TicketOperation.idempotency_key == key,
                TicketOperation.operation_type == OperationType.REGISTER,
                TicketOperation.status == OperationStatus.SUCCESS,
                TicketOperation.finished_at.is_not(None),
                TicketOperation.finished_at <= expired_before,
            )
            .values(idempotency_key=None)
        )

        await self._session.execute(expire_statement)

        statement = select(TicketOperation).where(
            TicketOperation.idempotency_key == key,
            TicketOperation.operation_type == OperationType.REGISTER,
        )

        return await self._session.scalar(statement)

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
                status=TicketStatus.PENDING,
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
            operation_type=OperationType.REGISTER,
            idempotency_key=request.idempotency_key,
            request_fingerprint=request_fingerprint,
            status=OperationStatus.PREPARED,
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
                Ticket.status == TicketStatus.PENDING,
            )
            .exists()
        )

        statement = (
            update(TicketOperation)
            .where(
                TicketOperation.id == operation_id,
                TicketOperation.registration_id == registration_id,
                TicketOperation.operation_type == OperationType.REGISTER,
                TicketOperation.status == OperationStatus.PREPARED,
                pending_ticket,
            )
            .values(
                status=OperationStatus.IN_FLIGHT,
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
                TicketOperation.operation_type == OperationType.REGISTER,
                TicketOperation.status == OperationStatus.IN_FLIGHT,
            )
            .values(
                status=OperationStatus.SUCCESS,
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
                Ticket.status == TicketStatus.PENDING,
            )
            .values(
                provider_ticket_id=provider_ticket_id,
                status=TicketStatus.ACTIVE,
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

        event_name = await self._session.scalar(
            select(Event.name)
            .join(Ticket, Ticket.event_id == Event.id)
            .where(Ticket.id == registration_id)
        )

        if event_name is None:
            raise TicketStateConflict(
                "Event not found during registration confirmation"
            )

        self._session.add(
            Outbox(
                id=uuid4(),
                event_type=OutboxEventType.TICKET_PURCHASED,
                payload={
                    "ticket_id": str(registration_id),
                    "message": (
                        f"Вы успешно зарегистрированы на мероприятие «{event_name}»."
                    ),
                },
                status=OutboxStatus.PENDING,
                created_at=now,
            )
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

        ticket_status = (
            TicketStatus.FAILED if outcome == "failed" else TicketStatus.UNCERTAIN
        )

        finished_at = now if outcome == "failed" else None

        operation_statement = (
            update(TicketOperation)
            .where(
                TicketOperation.id == operation_id,
                TicketOperation.registration_id == registration_id,
                TicketOperation.operation_type == OperationType.REGISTER,
                TicketOperation.status == OperationStatus.IN_FLIGHT,
            )
            .values(
                status=OperationStatus(outcome),
                provider_http_status=provider_http_status,
                error_code=error_code,
                updated_at=now,
                finished_at=finished_at,
            )
            .returning(TicketOperation.id)
        )

        if outcome == "failed":
            operation_statement = operation_statement.values(idempotency_key=None)

        operation_result = await self._session.execute(operation_statement)

        if operation_result.scalar_one_or_none() is None:
            return False

        ticket_statement = (
            update(Ticket)
            .where(
                Ticket.id == registration_id,
                Ticket.status == TicketStatus.PENDING,
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
                TicketOperation.operation_type == OperationType.REGISTER,
                TicketOperation.status == OperationStatus.PREPARED,
                TicketOperation.updated_at < stale_before,
            )
            .values(
                status=OperationStatus.FAILED,
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
                Ticket.status == TicketStatus.PENDING,
            )
            .values(
                status=TicketStatus.FAILED,
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
                Ticket.status == TicketStatus.ACTIVE,
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
                operation_type=OperationType.CANCEL,
                idempotency_key=None,
                request_fingerprint=request_fingerprint,
                status=OperationStatus.PREPARED,
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
                Ticket.status == TicketStatus.ACTIVE,
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
                TicketOperation.operation_type == OperationType.CANCEL,
                TicketOperation.status == OperationStatus.PREPARED,
                active_ticket,
            )
            .values(
                status=OperationStatus.IN_FLIGHT,
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
                TicketOperation.operation_type == OperationType.CANCEL,
                TicketOperation.status == OperationStatus.IN_FLIGHT,
            )
            .values(
                status=OperationStatus.SUCCESS,
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
                Ticket.status == TicketStatus.ACTIVE,
                Ticket.provider_ticket_id.is_not(None),
            )
            .values(
                status=TicketStatus.CANCELLED,
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
                TicketOperation.operation_type == OperationType.CANCEL,
                TicketOperation.status == OperationStatus.IN_FLIGHT,
            )
            .values(
                status=OperationStatus(outcome),
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
                TicketOperation.operation_type == OperationType.CANCEL,
                TicketOperation.status == OperationStatus.PREPARED,
            )
            .values(
                status=OperationStatus.FAILED,
                error_code="cancellation_not_started",
                updated_at=now,
                finished_at=now,
            )
            .returning(TicketOperation.id)
        )

        result = await self._session.execute(statement)

        return result.scalar_one_or_none() is not None

    async def expire_prepared_cancellation(
        self,
        *,
        operation_id: UUID,
        stale_before: datetime,
        now: datetime,
    ) -> bool:
        statement = (
            update(TicketOperation)
            .where(
                TicketOperation.id == operation_id,
                TicketOperation.operation_type == OperationType.CANCEL,
                TicketOperation.status == OperationStatus.PREPARED,
                TicketOperation.updated_at < stale_before,
            )
            .values(
                status=OperationStatus.FAILED,
                error_code="stale_prepared",
                updated_at=now,
                finished_at=now,
            )
            .returning(TicketOperation.id)
        )

        result = await self._session.execute(statement)

        return result.scalar_one_or_none() is not None

    async def expire_stale_prepared_operations(
        self,
        *,
        stale_before: datetime,
        now: datetime,
        limit: int = 100,
    ) -> int:
        if not 1 <= limit <= 500:
            raise ValueError("Cleanup limit must be between 1 and 500")

        statement = (
            select(
                TicketOperation.id,
                TicketOperation.operation_type,
            )
            .where(
                TicketOperation.status == OperationStatus.PREPARED,
                TicketOperation.updated_at < stale_before,
                TicketOperation.operation_type.in_(
                    (OperationType.REGISTER, OperationType.CANCEL)
                ),
            )
            .order_by(
                TicketOperation.updated_at,
                TicketOperation.id,
            )
            .limit(limit)
            .with_for_update(
                of=TicketOperation,
                skip_locked=True,
            )
        )

        result = await self._session.execute(statement)

        expired = 0

        for operation_id, operation_type in result.all():
            if operation_type == OperationType.REGISTER:
                completed = await self.expire_prepared_registration(
                    operation_id=operation_id,
                    stale_before=stale_before,
                    now=now,
                )
            else:
                completed = await self.expire_prepared_cancellation(
                    operation_id=operation_id,
                    stale_before=stale_before,
                    now=now,
                )

            if completed:
                expired += 1

        return expired
