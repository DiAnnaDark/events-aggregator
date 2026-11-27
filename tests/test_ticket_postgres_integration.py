import asyncio
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.tickets import get_ticket_cancellation_service
from app.application.tickets.cancellation_service import TicketCancellationService
from app.application.tickets.registration_service import TicketRegistrationService
from app.application.tickets.schemas import TicketCreate
from app.core.enums import (
    EventStatus,
    OperationStatus,
    OperationType,
    OutboxEventType,
    OutboxStatus,
    SyncStatus,
    TicketStatus,
)
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import (
    Event,
    Outbox,
    Place,
    SyncState,
    Ticket,
    TicketOperation,
)
from app.infrastructure.db.outbox_store import PostgresOutboxStore
from app.infrastructure.db.sync_state_repository import (
    PostgresSyncStateRepository,
)
from app.infrastructure.db.ticket_repository import TicketStateConflict
from app.infrastructure.db.ticket_store import PostgresTicketStore
from app.main import create_app

TEST_DATABASE_URL = os.environ.get("EVENTS_AGGREGATOR_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="Isolated PostgreSQL test database is not configured",
)


def get_safe_test_url() -> str:
    if not TEST_DATABASE_URL:
        raise RuntimeError("Test database URL is required")

    url = make_url(TEST_DATABASE_URL)

    if (
        url.drivername != "postgresql+asyncpg"
        or url.host not in ("127.0.0.1", "localhost")
        or url.port != 55433
        or url.database != "events_aggregator_test"
    ):
        raise RuntimeError("Refusing to use a non-isolated test database")

    return TEST_DATABASE_URL


async def prepare_database() -> tuple[
    AsyncEngine,
    async_sessionmaker[AsyncSession],
    UUID,
]:
    engine = create_async_engine(get_safe_test_url())

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )

    now = datetime.now(UTC)

    place_id = uuid4()
    event_id = uuid4()

    async with session_factory.begin() as session:
        session.add(
            Place(
                id=place_id,
                name="Integration Test Place",
                city="Test City",
                address="Test Street",
                seats_pattern="A1-100",
                created_at=now,
                changed_at=now,
            )
        )

        await session.flush()

        session.add(
            Event(
                id=event_id,
                place_id=place_id,
                name="Integration Test Event",
                event_time=now + timedelta(days=7),
                registration_deadline=now + timedelta(days=6),
                status="published",
                number_of_visitors=0,
                changed_at=now,
                created_at=now,
                status_changed_at=now,
            )
        )

    return engine, session_factory, event_id


async def try_reserve(
    store: PostgresTicketStore,
    *,
    event_id: UUID,
    seat: str = "A15",
) -> tuple[bool, UUID, UUID]:
    registration_id = uuid4()
    operation_id = uuid4()

    request = TicketCreate(
        event_id=event_id,
        first_name="Anna",
        last_name="Test",
        email="anna@example.com",
        seat=seat,
    )

    reserved = await store.reserve_registration(
        registration_id=registration_id,
        operation_id=operation_id,
        request=request,
        request_fingerprint="integration-test",
        now=datetime.now(UTC),
    )

    return reserved, registration_id, operation_id


def test_postgres_successful_registration() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)

            reserved, registration_id, operation_id = await try_reserve(
                store,
                event_id=event_id,
            )

            assert reserved is True

            started = await store.mark_registration_in_flight(
                registration_id=registration_id,
                operation_id=operation_id,
                now=datetime.now(UTC),
            )

            assert started is True

            provider_ticket_id = uuid4()

            confirmed = await store.finish_registration_success(
                registration_id=registration_id,
                operation_id=operation_id,
                provider_ticket_id=provider_ticket_id,
                now=datetime.now(UTC),
            )

            assert confirmed is True

            async with factory() as session:
                ticket = await session.get(Ticket, registration_id)
                operation = await session.get(
                    TicketOperation,
                    operation_id,
                )

                assert ticket is not None
                assert operation is not None

                assert ticket.status == "active"
                assert ticket.status is TicketStatus.ACTIVE
                assert ticket.provider_ticket_id == provider_ticket_id
                assert operation.operation_type is OperationType.REGISTER
                assert operation.status == "success"
                assert operation.status is OperationStatus.SUCCESS

                outbox_events = (
                    await session.scalars(
                        select(Outbox).where(
                            Outbox.payload["ticket_id"].astext == str(registration_id)
                        )
                    )
                ).all()

                assert len(outbox_events) == 1

                outbox_event = outbox_events[0]

                assert outbox_event.event_type == "ticket_purchased"
                assert outbox_event.event_type is OutboxEventType.TICKET_PURCHASED
                assert outbox_event.status == "pending"
                assert "Integration Test Event" in outbox_event.payload["message"]

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_outbox_failure_rolls_back_registration() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)

            reserved, registration_id, operation_id = await try_reserve(
                store,
                event_id=event_id,
            )

            assert reserved is True

            assert await store.mark_registration_in_flight(
                registration_id=registration_id,
                operation_id=operation_id,
                now=datetime.now(UTC),
            )

            def invalidate_outbox_status(
                _mapper: object,
                _connection: object,
                target: Outbox,
            ) -> None:
                if target.payload["ticket_id"] == str(registration_id):
                    target.status = "invalid"

            event.listen(Outbox, "before_insert", invalidate_outbox_status)

            try:
                with pytest.raises(IntegrityError):
                    await store.finish_registration_success(
                        registration_id=registration_id,
                        operation_id=operation_id,
                        provider_ticket_id=uuid4(),
                        now=datetime.now(UTC),
                    )
            finally:
                event.remove(
                    Outbox,
                    "before_insert",
                    invalidate_outbox_status,
                )

            async with factory() as session:
                ticket = await session.get(Ticket, registration_id)
                operation = await session.get(
                    TicketOperation,
                    operation_id,
                )

                assert ticket is not None
                assert operation is not None

                assert ticket.status == "pending"
                assert ticket.status is TicketStatus.PENDING
                assert ticket.provider_ticket_id is None
                assert operation.status == "in_flight"
                assert operation.status is OperationStatus.IN_FLIGHT

                outbox_count = await session.scalar(
                    select(func.count())
                    .select_from(Outbox)
                    .where(Outbox.payload["ticket_id"].astext == str(registration_id))
                )

                assert outbox_count == 0

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_concurrent_seat_reservation() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)

            first, second = await asyncio.wait_for(
                asyncio.gather(
                    try_reserve(store, event_id=event_id),
                    try_reserve(store, event_id=event_id),
                ),
                timeout=15.0,
            )

            assert sorted([first[0], second[0]]) == [False, True]

            async with factory() as session:
                count = await session.scalar(
                    select(func.count())
                    .select_from(TicketOperation)
                    .where(TicketOperation.registration_id.in_([first[1], second[1]]))
                )

                assert count == 1

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_failed_registration_releases_seat() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)

            reserved, registration_id, operation_id = await try_reserve(
                store,
                event_id=event_id,
            )

            assert reserved is True

            assert await store.mark_registration_in_flight(
                registration_id=registration_id,
                operation_id=operation_id,
                now=datetime.now(UTC),
            )

            assert await store.record_registration_error(
                registration_id=registration_id,
                operation_id=operation_id,
                outcome="failed",
                provider_http_status=400,
                error_code="provider_rejected",
                now=datetime.now(UTC),
            )

            second, _, _ = await try_reserve(
                store,
                event_id=event_id,
            )

            assert second is True

            async with factory() as session:
                ticket = await session.get(Ticket, registration_id)

                assert ticket is not None
                assert ticket.status == "failed"
                assert ticket.status is TicketStatus.FAILED

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_unknown_registration_keeps_seat() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)

            reserved, registration_id, operation_id = await try_reserve(
                store,
                event_id=event_id,
            )

            assert reserved is True

            assert await store.mark_registration_in_flight(
                registration_id=registration_id,
                operation_id=operation_id,
                now=datetime.now(UTC),
            )

            assert await store.record_registration_error(
                registration_id=registration_id,
                operation_id=operation_id,
                outcome="unknown",
                provider_http_status=500,
                error_code="provider_error",
                now=datetime.now(UTC),
            )

            second, _, _ = await try_reserve(
                store,
                event_id=event_id,
            )

            assert second is False

            async with factory() as session:
                ticket = await session.get(Ticket, registration_id)
                operation = await session.get(
                    TicketOperation,
                    operation_id,
                )

                assert ticket is not None
                assert operation is not None

                assert ticket.status == "uncertain"
                assert ticket.status is TicketStatus.UNCERTAIN
                assert operation.status == "unknown"
                assert operation.status is OperationStatus.UNKNOWN

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_transaction_rolls_back_on_conflict() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)

            reserved, registration_id, operation_id = await try_reserve(
                store,
                event_id=event_id,
            )

            assert reserved is True

            assert await store.mark_registration_in_flight(
                registration_id=registration_id,
                operation_id=operation_id,
                now=datetime.now(UTC),
            )

            async with factory.begin() as session:
                await session.execute(
                    update(Ticket)
                    .where(Ticket.id == registration_id)
                    .values(
                        status="cancelled",
                        updated_at=datetime.now(UTC),
                    )
                )

            with pytest.raises(TicketStateConflict):
                await store.finish_registration_success(
                    registration_id=registration_id,
                    operation_id=operation_id,
                    provider_ticket_id=uuid4(),
                    now=datetime.now(UTC),
                )

            async with factory() as session:
                operation = await session.get(
                    TicketOperation,
                    operation_id,
                )

                assert operation is not None

                # The first UPDATE must have been rolled back.
                assert operation.status == "in_flight"
                assert operation.status is OperationStatus.IN_FLIGHT

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_stale_prepared_releases_seat() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)

            reserved, registration_id, operation_id = await try_reserve(
                store,
                event_id=event_id,
            )

            assert reserved is True

            now = datetime.now(UTC)
            old_time = now - timedelta(hours=2)

            async with factory.begin() as session:
                await session.execute(
                    update(TicketOperation)
                    .where(TicketOperation.id == operation_id)
                    .values(updated_at=old_time)
                )

            expired = await store.expire_prepared_registration(
                operation_id=operation_id,
                stale_before=now - timedelta(minutes=30),
                now=now,
            )

            assert expired is True

            async with factory() as session:
                ticket = await session.get(Ticket, registration_id)
                operation = await session.get(
                    TicketOperation,
                    operation_id,
                )

                assert ticket is not None
                assert operation is not None

                assert ticket.status == "failed"
                assert ticket.status is TicketStatus.FAILED
                assert operation.status == "failed"
                assert operation.status is OperationStatus.FAILED
                assert operation.error_code == "stale_prepared"

            second, _, _ = await try_reserve(
                store,
                event_id=event_id,
            )

            assert second is True

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_in_flight_cannot_be_expired() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)

            reserved, registration_id, operation_id = await try_reserve(
                store,
                event_id=event_id,
            )

            assert reserved is True

            now = datetime.now(UTC)
            old_time = now - timedelta(hours=2)

            started = await store.mark_registration_in_flight(
                registration_id=registration_id,
                operation_id=operation_id,
                now=old_time,
            )

            assert started is True

            expired = await store.expire_prepared_registration(
                operation_id=operation_id,
                stale_before=now - timedelta(minutes=30),
                now=now,
            )

            assert expired is False

            second, _, _ = await try_reserve(
                store,
                event_id=event_id,
            )

            assert second is False

            async with factory() as session:
                operation = await session.get(
                    TicketOperation,
                    operation_id,
                )

                assert operation is not None
                assert operation.status == "in_flight"
                assert operation.status is OperationStatus.IN_FLIGHT

        finally:
            await engine.dispose()

    asyncio.run(scenario())


async def create_active_ticket(
    store: PostgresTicketStore,
    event_id: UUID,
) -> UUID:
    reserved, ticket_id, registration_operation_id = await try_reserve(
        store,
        event_id=event_id,
    )

    assert reserved is True

    assert await store.mark_registration_in_flight(
        registration_id=ticket_id,
        operation_id=registration_operation_id,
        now=datetime.now(UTC),
    )

    assert await store.finish_registration_success(
        registration_id=ticket_id,
        operation_id=registration_operation_id,
        provider_ticket_id=uuid4(),
        now=datetime.now(UTC),
    )

    return ticket_id


def test_postgres_prepare_cancellation() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)
            ticket_id = await create_active_ticket(store, event_id)
            operation_id = uuid4()

            prepared = await store.prepare_cancellation(
                ticket_id=ticket_id,
                operation_id=operation_id,
                request_fingerprint="test-cancellation",
                now=datetime.now(UTC),
            )

            assert prepared is True

            async with factory() as session:
                ticket = await session.get(Ticket, ticket_id)
                operation = await session.get(
                    TicketOperation,
                    operation_id,
                )

                assert ticket is not None
                assert operation is not None

                assert ticket.status == "active"
                assert ticket.status is TicketStatus.ACTIVE
                assert ticket.provider_ticket_id is not None

                assert operation.registration_id == ticket_id
                assert operation.operation_type == "cancel"
                assert operation.operation_type is OperationType.CANCEL
                assert operation.status == "prepared"
                assert operation.status is OperationStatus.PREPARED

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_concurrent_cancellation() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)
            ticket_id = await create_active_ticket(store, event_id)

            async def attempt() -> bool:
                return await store.prepare_cancellation(
                    ticket_id=ticket_id,
                    operation_id=uuid4(),
                    request_fingerprint="test-cancellation",
                    now=datetime.now(UTC),
                )

            first, second = await asyncio.wait_for(
                asyncio.gather(attempt(), attempt()),
                timeout=15.0,
            )

            assert sorted((first, second)) == [False, True]

            async with factory() as session:
                count = await session.scalar(
                    select(func.count())
                    .select_from(TicketOperation)
                    .where(
                        TicketOperation.registration_id == ticket_id,
                        TicketOperation.operation_type == "cancel",
                    )
                )

                assert count == 1

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_pending_ticket_cannot_be_cancelled() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)

            reserved, ticket_id, _ = await try_reserve(
                store,
                event_id=event_id,
            )

            assert reserved is True

            prepared = await store.prepare_cancellation(
                ticket_id=ticket_id,
                operation_id=uuid4(),
                request_fingerprint="test-cancellation",
                now=datetime.now(UTC),
            )

            assert prepared is False

            async with factory() as session:
                ticket = await session.get(Ticket, ticket_id)

                assert ticket is not None
                assert ticket.status == "pending"
                assert ticket.status is TicketStatus.PENDING

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_past_event_cannot_be_cancelled() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)
            ticket_id = await create_active_ticket(store, event_id)

            async with factory.begin() as session:
                await session.execute(
                    update(Event)
                    .where(Event.id == event_id)
                    .values(
                        event_time=datetime.now(UTC) - timedelta(days=1),
                    )
                )

            prepared = await store.prepare_cancellation(
                ticket_id=ticket_id,
                operation_id=uuid4(),
                request_fingerprint="test-cancellation",
                now=datetime.now(UTC),
            )

            assert prepared is False

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_cancellation_state_transitions() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)
            ticket_id = await create_active_ticket(store, event_id)
            operation_id = uuid4()
            now = datetime.now(UTC)

            assert await store.prepare_cancellation(
                ticket_id=ticket_id,
                operation_id=operation_id,
                request_fingerprint="test-cancellation",
                now=now,
            )

            assert await store.mark_cancellation_in_flight(
                ticket_id=ticket_id,
                operation_id=operation_id,
                now=now,
            )

            assert await store.finish_cancellation_success(
                ticket_id=ticket_id,
                operation_id=operation_id,
                now=now,
            )

            async with factory() as session:
                ticket = await session.get(Ticket, ticket_id)
                operation = await session.get(
                    TicketOperation,
                    operation_id,
                )

                assert ticket is not None
                assert operation is not None

                assert ticket.status == "cancelled"
                assert ticket.status is TicketStatus.CANCELLED
                assert ticket.cancelled_at is not None
                assert operation.status == "success"
                assert operation.status is OperationStatus.SUCCESS
                assert operation.provider_http_status == 200
                assert operation.finished_at is not None

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_cancellation_cannot_finish_before_start() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)
            ticket_id = await create_active_ticket(store, event_id)
            operation_id = uuid4()

            assert await store.prepare_cancellation(
                ticket_id=ticket_id,
                operation_id=operation_id,
                request_fingerprint="test-cancellation",
                now=datetime.now(UTC),
            )

            confirmed = await store.finish_cancellation_success(
                ticket_id=ticket_id,
                operation_id=operation_id,
                now=datetime.now(UTC),
            )

            assert confirmed is False

            async with factory() as session:
                ticket = await session.get(Ticket, ticket_id)
                operation = await session.get(
                    TicketOperation,
                    operation_id,
                )

                assert ticket.status == "active"
                assert ticket.status is TicketStatus.ACTIVE
                assert operation.status == "prepared"
                assert operation.status is OperationStatus.PREPARED

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_concurrent_cancellation_start() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)
            ticket_id = await create_active_ticket(store, event_id)
            operation_id = uuid4()

            assert await store.prepare_cancellation(
                ticket_id=ticket_id,
                operation_id=operation_id,
                request_fingerprint="test-cancellation",
                now=datetime.now(UTC),
            )

            first, second = await asyncio.wait_for(
                asyncio.gather(
                    store.mark_cancellation_in_flight(
                        ticket_id=ticket_id,
                        operation_id=operation_id,
                        now=datetime.now(UTC),
                    ),
                    store.mark_cancellation_in_flight(
                        ticket_id=ticket_id,
                        operation_id=operation_id,
                        now=datetime.now(UTC),
                    ),
                ),
                timeout=15.0,
            )

            assert sorted([first, second]) == [False, True]

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_cancellation_start_checks_event_time() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)
            ticket_id = await create_active_ticket(store, event_id)
            operation_id = uuid4()

            assert await store.prepare_cancellation(
                ticket_id=ticket_id,
                operation_id=operation_id,
                request_fingerprint="test-cancellation",
                now=datetime.now(UTC),
            )

            async with factory.begin() as session:
                await session.execute(
                    update(Event)
                    .where(Event.id == event_id)
                    .values(
                        event_time=datetime.now(UTC) - timedelta(days=1),
                    )
                )

            started = await store.mark_cancellation_in_flight(
                ticket_id=ticket_id,
                operation_id=operation_id,
                now=datetime.now(UTC),
            )

            assert started is False

            async with factory() as session:
                operation = await session.get(
                    TicketOperation,
                    operation_id,
                )

                assert operation.status == "prepared"
                assert operation.status is OperationStatus.PREPARED

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_cancellation_confirmation_rolls_back() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)
            ticket_id = await create_active_ticket(store, event_id)
            operation_id = uuid4()

            assert await store.prepare_cancellation(
                ticket_id=ticket_id,
                operation_id=operation_id,
                request_fingerprint="test-cancellation",
                now=datetime.now(UTC),
            )

            assert await store.mark_cancellation_in_flight(
                ticket_id=ticket_id,
                operation_id=operation_id,
                now=datetime.now(UTC),
            )

            # Simulate an unexpected ticket state change.
            async with factory.begin() as session:
                await session.execute(
                    update(Ticket)
                    .where(Ticket.id == ticket_id)
                    .values(
                        status="uncertain",
                        updated_at=datetime.now(UTC),
                    )
                )

            with pytest.raises(TicketStateConflict):
                await store.finish_cancellation_success(
                    ticket_id=ticket_id,
                    operation_id=operation_id,
                    now=datetime.now(UTC),
                )

            async with factory() as session:
                operation = await session.get(
                    TicketOperation,
                    operation_id,
                )

                assert operation.status == "in_flight"
                assert operation.status is OperationStatus.IN_FLIGHT

        finally:
            await engine.dispose()

    asyncio.run(scenario())


async def create_started_cancellation(
    store: PostgresTicketStore,
    event_id: UUID,
) -> tuple[UUID, UUID]:
    ticket_id = await create_active_ticket(store, event_id)
    operation_id = uuid4()

    assert await store.prepare_cancellation(
        ticket_id=ticket_id,
        operation_id=operation_id,
        request_fingerprint="test-cancellation",
        now=datetime.now(UTC),
    )

    assert await store.mark_cancellation_in_flight(
        ticket_id=ticket_id,
        operation_id=operation_id,
        now=datetime.now(UTC),
    )

    return ticket_id, operation_id


async def create_started_cancellation_for_errors(
    store: PostgresTicketStore,
    event_id: UUID,
) -> tuple[UUID, UUID]:
    ticket_id = await create_active_ticket(store, event_id)
    operation_id = uuid4()

    assert await store.prepare_cancellation(
        ticket_id=ticket_id,
        operation_id=operation_id,
        request_fingerprint="test-cancellation",
        now=datetime.now(UTC),
    )

    assert await store.mark_cancellation_in_flight(
        ticket_id=ticket_id,
        operation_id=operation_id,
        now=datetime.now(UTC),
    )

    return ticket_id, operation_id


def test_postgres_cancellation_failure_keeps_ticket_active() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)

            ticket_id, operation_id = await create_started_cancellation_for_errors(
                store,
                event_id,
            )

            recorded = await store.record_cancellation_error(
                ticket_id=ticket_id,
                operation_id=operation_id,
                outcome="failed",
                provider_http_status=400,
                error_code="provider_rejected",
                now=datetime.now(UTC),
            )

            assert recorded is True

            async with factory() as session:
                ticket = await session.get(Ticket, ticket_id)
                operation = await session.get(
                    TicketOperation,
                    operation_id,
                )

                assert ticket is not None
                assert operation is not None
                assert ticket.status == "active"
                assert ticket.status is TicketStatus.ACTIVE
                assert operation.status == "failed"
                assert operation.status is OperationStatus.FAILED

            retry = await store.prepare_cancellation(
                ticket_id=ticket_id,
                operation_id=uuid4(),
                request_fingerprint="retry-cancellation",
                now=datetime.now(UTC),
            )

            assert retry is True

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_unknown_cancellation_blocks_retry() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)

            ticket_id, operation_id = await create_started_cancellation_for_errors(
                store,
                event_id,
            )

            recorded = await store.record_cancellation_error(
                ticket_id=ticket_id,
                operation_id=operation_id,
                outcome="unknown",
                provider_http_status=500,
                error_code="provider_error",
                now=datetime.now(UTC),
            )

            assert recorded is True

            async with factory() as session:
                ticket = await session.get(Ticket, ticket_id)
                operation = await session.get(
                    TicketOperation,
                    operation_id,
                )

                assert ticket is not None
                assert operation is not None
                assert ticket.status == "active"
                assert ticket.status is TicketStatus.ACTIVE
                assert operation.status == "unknown"
                assert operation.status is OperationStatus.UNKNOWN

            retry = await store.prepare_cancellation(
                ticket_id=ticket_id,
                operation_id=uuid4(),
                request_fingerprint="retry-cancellation",
                now=datetime.now(UTC),
            )

            assert retry is False

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_cancellation_error_requires_in_flight() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)
            ticket_id = await create_active_ticket(store, event_id)
            operation_id = uuid4()

            assert await store.prepare_cancellation(
                ticket_id=ticket_id,
                operation_id=operation_id,
                request_fingerprint="test-cancellation",
                now=datetime.now(UTC),
            )

            recorded = await store.record_cancellation_error(
                ticket_id=ticket_id,
                operation_id=operation_id,
                outcome="failed",
                provider_http_status=400,
                error_code="provider_rejected",
                now=datetime.now(UTC),
            )

            assert recorded is False

            async with factory() as session:
                operation = await session.get(
                    TicketOperation,
                    operation_id,
                )

                assert operation is not None
                assert operation.status == "prepared"
                assert operation.status is OperationStatus.PREPARED

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_aborted_prepared_cancellation_allows_retry() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)
            ticket_id = await create_active_ticket(store, event_id)
            operation_id = uuid4()

            assert await store.prepare_cancellation(
                ticket_id=ticket_id,
                operation_id=operation_id,
                request_fingerprint="first-attempt",
                now=datetime.now(UTC),
            )

            aborted = await store.abort_prepared_cancellation(
                ticket_id=ticket_id,
                operation_id=operation_id,
                now=datetime.now(UTC),
            )

            assert aborted is True

            async with factory() as session:
                ticket = await session.get(Ticket, ticket_id)
                operation = await session.get(
                    TicketOperation,
                    operation_id,
                )

                assert ticket is not None
                assert operation is not None
                assert ticket.status == "active"
                assert ticket.status is TicketStatus.ACTIVE
                assert operation.status == "failed"
                assert operation.status is OperationStatus.FAILED
                assert operation.error_code == "cancellation_not_started"

            retry = await store.prepare_cancellation(
                ticket_id=ticket_id,
                operation_id=uuid4(),
                request_fingerprint="second-attempt",
                now=datetime.now(UTC),
            )

            assert retry is True

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_in_flight_cancellation_cannot_be_aborted() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)
            ticket_id = await create_active_ticket(store, event_id)
            operation_id = uuid4()

            assert await store.prepare_cancellation(
                ticket_id=ticket_id,
                operation_id=operation_id,
                request_fingerprint="first-attempt",
                now=datetime.now(UTC),
            )

            assert await store.mark_cancellation_in_flight(
                ticket_id=ticket_id,
                operation_id=operation_id,
                now=datetime.now(UTC),
            )

            aborted = await store.abort_prepared_cancellation(
                ticket_id=ticket_id,
                operation_id=operation_id,
                now=datetime.now(UTC),
            )

            assert aborted is False

            retry = await store.prepare_cancellation(
                ticket_id=ticket_id,
                operation_id=uuid4(),
                request_fingerprint="second-attempt",
                now=datetime.now(UTC),
            )

            assert retry is False

            async with factory() as session:
                ticket = await session.get(Ticket, ticket_id)
                operation = await session.get(
                    TicketOperation,
                    operation_id,
                )

                assert ticket is not None
                assert operation is not None
                assert ticket.status == "active"
                assert ticket.status is TicketStatus.ACTIVE
                assert operation.status == "in_flight"
                assert operation.status is OperationStatus.IN_FLIGHT

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_http_cancellation_with_real_postgres() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)
            ticket_id = await create_active_ticket(store, event_id)

            ticket_before = await store.get_ticket_for_cancellation(
                ticket_id=ticket_id,
            )

            assert ticket_before is not None
            assert ticket_before.provider_ticket_id is not None

            provider = SimpleNamespace(
                unregister=AsyncMock(),
            )

            seats = SimpleNamespace(
                invalidate=AsyncMock(),
            )

            service = TicketCancellationService(
                store=store,
                provider=provider,
                seats=seats,
            )

            application = create_app(enable_sync=False)

            application.dependency_overrides[get_ticket_cancellation_service] = lambda: (
                service
            )

            transport = httpx.ASGITransport(app=application)

            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.delete(f"/api/tickets/{ticket_id}")

                assert response.status_code == 200
                assert response.json() == {"success": True}

                # A repeated request must not call the provider again.
                repeated = await client.delete(f"/api/tickets/{ticket_id}")

                assert repeated.status_code == 200
                assert repeated.json() == {"success": True}

            provider.unregister.assert_awaited_once_with(
                event_id=event_id,
                ticket_id=ticket_before.provider_ticket_id,
            )

            seats.invalidate.assert_awaited_once_with(event_id)

            async with factory() as session:
                ticket = await session.get(Ticket, ticket_id)

                operation_count = await session.scalar(
                    select(func.count())
                    .select_from(TicketOperation)
                    .where(
                        TicketOperation.registration_id == ticket_id,
                        TicketOperation.operation_type == "cancel",
                    )
                )

                assert ticket is not None
                assert ticket.status == "cancelled"
                assert ticket.status is TicketStatus.CANCELLED
                assert ticket.cancelled_at is not None
                assert operation_count == 1

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_cleanup_releases_stale_prepared_registration() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)
            reserved, ticket_id, operation_id = await try_reserve(
                store,
                event_id=event_id,
            )
            assert reserved is True

            now = datetime.now(UTC)

            async with factory.begin() as session:
                await session.execute(
                    update(TicketOperation)
                    .where(TicketOperation.id == operation_id)
                    .values(updated_at=now - timedelta(days=365))
                )

            expired = await store.expire_stale_prepared_operations(
                stale_before=now - timedelta(minutes=30),
                now=now,
                limit=1,
            )

            assert expired == 1

            async with factory() as session:
                ticket = await session.get(Ticket, ticket_id)
                operation = await session.get(
                    TicketOperation,
                    operation_id,
                )

                assert ticket.status == "failed"
                assert ticket.status is TicketStatus.FAILED
                assert operation.status == "failed"
                assert operation.status is OperationStatus.FAILED
                assert operation.error_code == "stale_prepared"

            reserved_again, _, _ = await try_reserve(
                store,
                event_id=event_id,
            )
            assert reserved_again is True

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_cleanup_closes_stale_prepared_cancellation() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)
            ticket_id = await create_active_ticket(store, event_id)
            operation_id = uuid4()
            now = datetime.now(UTC)

            assert await store.prepare_cancellation(
                ticket_id=ticket_id,
                operation_id=operation_id,
                request_fingerprint="cleanup-test",
                now=now,
            )

            async with factory.begin() as session:
                await session.execute(
                    update(TicketOperation)
                    .where(TicketOperation.id == operation_id)
                    .values(updated_at=now - timedelta(days=365))
                )

            expired = await store.expire_stale_prepared_operations(
                stale_before=now - timedelta(minutes=30),
                now=now,
                limit=1,
            )

            assert expired == 1

            async with factory() as session:
                ticket = await session.get(Ticket, ticket_id)
                operation = await session.get(
                    TicketOperation,
                    operation_id,
                )

                assert ticket.status == "active"
                assert ticket.status is TicketStatus.ACTIVE
                assert operation.status == "failed"
                assert operation.status is OperationStatus.FAILED
                assert operation.error_code == "stale_prepared"

            assert await store.prepare_cancellation(
                ticket_id=ticket_id,
                operation_id=uuid4(),
                request_fingerprint="second-attempt",
                now=datetime.now(UTC),
            )

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_cleanup_does_not_touch_in_flight() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)

            ticket_id, operation_id = await create_started_cancellation_for_errors(
                store,
                event_id,
            )

            now = datetime.now(UTC)

            async with factory.begin() as session:
                await session.execute(
                    update(TicketOperation)
                    .where(TicketOperation.id == operation_id)
                    .values(updated_at=now - timedelta(days=365))
                )

            await store.expire_stale_prepared_operations(
                stale_before=now - timedelta(minutes=30),
                now=now,
            )

            async with factory() as session:
                ticket = await session.get(Ticket, ticket_id)
                operation = await session.get(
                    TicketOperation,
                    operation_id,
                )

                assert ticket.status == "active"
                assert ticket.status is TicketStatus.ACTIVE
                assert operation.status == "in_flight"
                assert operation.status is OperationStatus.IN_FLIGHT

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_cleanup_does_not_touch_unknown() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)

            ticket_id, operation_id = await create_started_cancellation_for_errors(
                store,
                event_id,
            )

            now = datetime.now(UTC)

            assert await store.record_cancellation_error(
                ticket_id=ticket_id,
                operation_id=operation_id,
                outcome="unknown",
                provider_http_status=500,
                error_code="provider_error",
                now=now,
            )

            async with factory.begin() as session:
                await session.execute(
                    update(TicketOperation)
                    .where(TicketOperation.id == operation_id)
                    .values(updated_at=now - timedelta(days=365))
                )

            await store.expire_stale_prepared_operations(
                stale_before=now - timedelta(minutes=30),
                now=now,
            )

            async with factory() as session:
                ticket = await session.get(Ticket, ticket_id)
                operation = await session.get(
                    TicketOperation,
                    operation_id,
                )

                assert ticket.status == "active"
                assert ticket.status is TicketStatus.ACTIVE
                assert operation.status == "unknown"
                assert operation.status is OperationStatus.UNKNOWN

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_outbox_get_pending() -> None:
    async def scenario() -> None:
        engine, factory, _ = await prepare_database()

        try:
            now = datetime.now(UTC)

            first_id = uuid4()
            second_id = uuid4()
            sent_id = uuid4()

            async with factory.begin() as session:
                session.add_all(
                    [
                        Outbox(
                            id=first_id,
                            event_type="ticket_purchased",
                            payload={"message": "First notification"},
                            status="pending",
                            created_at=now - timedelta(minutes=2),
                        ),
                        Outbox(
                            id=second_id,
                            event_type="ticket_purchased",
                            payload={"message": "Second notification"},
                            status="pending",
                            created_at=now - timedelta(minutes=1),
                        ),
                        Outbox(
                            id=sent_id,
                            event_type="ticket_purchased",
                            payload={"message": "Sent notification"},
                            status="sent",
                            created_at=now,
                        ),
                    ]
                )

            store = PostgresOutboxStore(factory)

            pending = await store.get_pending(limit=10000)
            pending_ids = [item.id for item in pending]

            assert first_id in pending_ids
            assert second_id in pending_ids
            assert sent_id not in pending_ids

            assert pending_ids.index(first_id) < pending_ids.index(second_id)

            assert pending == sorted(
                pending,
                key=lambda item: (item.created_at, item.id),
            )

            limited = await store.get_pending(limit=2)

            assert len(limited) == 2
            assert [item.id for item in limited] == pending_ids[:2]

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_outbox_mark_sent() -> None:
    async def scenario() -> None:
        engine, factory, _ = await prepare_database()

        try:
            event_id = uuid4()
            other_id = uuid4()

            async with factory.begin() as session:
                session.add_all(
                    [
                        Outbox(
                            id=event_id,
                            event_type="ticket_purchased",
                            payload={"message": "Test notification"},
                            status="pending",
                            created_at=datetime.now(UTC),
                        ),
                        Outbox(
                            id=other_id,
                            event_type="ticket_purchased",
                            payload={"message": "Other notification"},
                            status="pending",
                            created_at=datetime.now(UTC),
                        ),
                    ]
                )

            store = PostgresOutboxStore(factory)
            await store.mark_sent(event_id)

            async with factory() as session:
                sent_event = await session.get(Outbox, event_id)
                other_event = await session.get(Outbox, other_id)

                assert sent_event is not None
                assert other_event is not None

                assert sent_event.status == "sent"
                assert other_event.status == "pending"

                assert sent_event.status is OutboxStatus.SENT
                assert other_event.status is OutboxStatus.PENDING

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_get_registration_by_key() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)

            reserved, registration_id, operation_id = await try_reserve(
                store,
                event_id=event_id,
            )

            assert reserved is True

            key = f"purchase-{uuid4()}"

            key = f"purchase-{uuid4()}"

            async with factory.begin() as session:
                await session.execute(
                    update(TicketOperation)
                    .where(TicketOperation.id == operation_id)
                    .values(idempotency_key=key)
                )

            found = await store.get_registration_by_key(key)

            assert found is not None
            assert found.registration_id == registration_id
            assert found.request_fingerprint == "integration-test"
            assert found.status == "prepared"

            missing = await store.get_registration_by_key("unknown-key")
            assert missing is None

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_reserve_registration_saves_idempotency_key() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)
            key = f"purchase-{uuid4()}"
            registration_id = uuid4()
            operation_id = uuid4()

            request = TicketCreate(
                event_id=event_id,
                first_name="Anna",
                last_name="Test",
                email="anna@example.com",
                seat="A16",
                idempotency_key=key,
            )

            reserved = await store.reserve_registration(
                registration_id=registration_id,
                operation_id=operation_id,
                request=request,
                request_fingerprint="new-purchase-fingerprint",
                now=datetime.now(UTC),
            )

            assert reserved is True

            found = await store.get_registration_by_key(key)

            assert found is not None
            assert found.registration_id == registration_id
            assert found.request_fingerprint == "new-purchase-fingerprint"
            assert found.status == "prepared"

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_concurrent_same_idempotency_key() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)
            key = f"concurrent-{uuid4()}"

            async def reserve(seat: str) -> bool:
                request = TicketCreate(
                    event_id=event_id,
                    first_name="Anna",
                    last_name="Test",
                    email="anna@example.com",
                    seat=seat,
                    idempotency_key=key,
                )

                return await store.reserve_registration(
                    registration_id=uuid4(),
                    operation_id=uuid4(),
                    request=request,
                    request_fingerprint="concurrent-test",
                    now=datetime.now(UTC),
                )

            results = await asyncio.wait_for(
                asyncio.gather(
                    reserve("A97"),
                    reserve("A98"),
                    return_exceptions=True,
                ),
                timeout=15.0,
            )

            assert sum(result is True for result in results) == 1
            assert sum(result is False for result in results) == 1, repr(results)

            async with factory() as session:
                count = await session.scalar(
                    select(func.count())
                    .select_from(TicketOperation)
                    .where(TicketOperation.idempotency_key == key)
                )

                assert count == 1

                ticket_count = await session.scalar(
                    select(func.count())
                    .select_from(Ticket)
                    .where(
                        Ticket.event_id == event_id,
                        Ticket.seat.in_(["A97", "A98"]),
                    )
                )

                assert ticket_count == 1

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_failed_registration_allows_retry_with_same_key() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)
            key = f"failed-retry-{uuid4()}"

            request = TicketCreate(
                event_id=event_id,
                first_name="Anna",
                last_name="Test",
                email="anna@example.com",
                seat="A17",
                idempotency_key=key,
            )

            registration_id = uuid4()
            operation_id = uuid4()

            assert await store.reserve_registration(
                registration_id=registration_id,
                operation_id=operation_id,
                request=request,
                request_fingerprint="failed-retry-test",
                now=datetime.now(UTC),
            )

            assert await store.mark_registration_in_flight(
                registration_id=registration_id,
                operation_id=operation_id,
                now=datetime.now(UTC),
            )

            assert await store.record_registration_error(
                registration_id=registration_id,
                operation_id=operation_id,
                outcome="failed",
                provider_http_status=400,
                error_code="provider_rejected",
                now=datetime.now(UTC),
            )

            found = await store.get_registration_by_key(key)
            assert found is None

            async with factory() as session:
                old_operation = await session.get(TicketOperation, operation_id)

                assert old_operation is not None
                assert old_operation.status == "failed"
                assert old_operation.idempotency_key is None

            new_registration_id = uuid4()

            assert await store.reserve_registration(
                registration_id=new_registration_id,
                operation_id=uuid4(),
                request=request,
                request_fingerprint="failed-retry-test",
                now=datetime.now(UTC),
            )

            repeated = await store.get_registration_by_key(key)

            assert repeated is not None
            assert repeated.registration_id == new_registration_id
            assert repeated.status == "prepared"

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_unknown_registration_keeps_idempotency_key() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)
            key = f"unknown-{uuid4()}"
            registration_id = uuid4()
            operation_id = uuid4()

            request = TicketCreate(
                event_id=event_id,
                first_name="Anna",
                last_name="Test",
                email="anna@example.com",
                seat="A18",
                idempotency_key=key,
            )

            assert await store.reserve_registration(
                registration_id=registration_id,
                operation_id=operation_id,
                request=request,
                request_fingerprint="unknown-first",
                now=datetime.now(UTC),
            )

            assert await store.mark_registration_in_flight(
                registration_id=registration_id,
                operation_id=operation_id,
                now=datetime.now(UTC),
            )

            assert await store.record_registration_error(
                registration_id=registration_id,
                operation_id=operation_id,
                outcome="unknown",
                provider_http_status=500,
                error_code="provider_error",
                now=datetime.now(UTC),
            )

            found = await store.get_registration_by_key(key)

            assert found is not None
            assert found.registration_id == registration_id
            assert found.status == "unknown"
            assert found.idempotency_key == key

            another_request = request.model_copy(update={"seat": "A19"})

            reserved_again = await store.reserve_registration(
                registration_id=uuid4(),
                operation_id=uuid4(),
                request=another_request,
                request_fingerprint="unknown-second",
                now=datetime.now(UTC),
            )

            assert reserved_again is False

            async with factory() as session:
                operation_count = await session.scalar(
                    select(func.count())
                    .select_from(TicketOperation)
                    .where(TicketOperation.idempotency_key == key)
                )

                ticket_count = await session.scalar(
                    select(func.count())
                    .select_from(Ticket)
                    .where(
                        Ticket.event_id == event_id,
                        Ticket.seat.in_(["A18", "A19"]),
                    )
                )

                assert operation_count == 1
                assert ticket_count == 1

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_repeated_success_creates_one_outbox_event() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            now = datetime.now(UTC)
            key = f"outbox-retry-{uuid4()}"

            events = SimpleNamespace(
                get_event=AsyncMock(
                    return_value=SimpleNamespace(
                        status="published",
                        registration_deadline=now + timedelta(days=6),
                        event_time=now + timedelta(days=7),
                    )
                )
            )

            seats = SimpleNamespace(
                get_available_seats=AsyncMock(return_value=["A25"]),
                invalidate=AsyncMock(),
            )

            provider = SimpleNamespace(register=AsyncMock(return_value=uuid4()))

            store = PostgresTicketStore(factory)

            service = TicketRegistrationService(
                events=events,
                seats=seats,
                provider=provider,
                store=store,
                clock=lambda: now,
            )

            request = TicketCreate(
                event_id=event_id,
                first_name="Anna",
                last_name="Test",
                email="anna@example.com",
                seat="A25",
                idempotency_key=key,
            )

            first_ticket_id = await service.register(request)
            second_ticket_id = await service.register(request)

            assert second_ticket_id == first_ticket_id

            provider.register.assert_awaited_once()
            events.get_event.assert_awaited_once_with(event_id)
            seats.get_available_seats.assert_awaited_once_with(event_id)
            seats.invalidate.assert_awaited_once_with(event_id)

            existing = await store.get_registration_by_key(key)

            assert existing is not None
            assert existing.registration_id == first_ticket_id
            assert existing.status == "success"

            async with factory() as session:
                outbox_events = (
                    await session.scalars(
                        select(Outbox).where(
                            Outbox.payload["ticket_id"].astext == str(first_ticket_id)
                        )
                    )
                ).all()

                assert len(outbox_events) == 1
                assert outbox_events[0].event_type == "ticket_purchased"
                assert outbox_events[0].status == "pending"

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_expired_success_releases_key_but_keeps_ticket_and_outbox() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(factory)
            now = datetime.now(UTC)
            key = f"expired-success-{uuid4()}"
            registration_id = uuid4()
            operation_id = uuid4()

            request = TicketCreate(
                event_id=event_id,
                first_name="Anna",
                last_name="Test",
                email="anna@example.com",
                seat="A36",
                idempotency_key=key,
            )

            assert await store.reserve_registration(
                registration_id=registration_id,
                operation_id=operation_id,
                request=request,
                request_fingerprint="expired-success-test",
                now=now,
            )

            assert await store.mark_registration_in_flight(
                registration_id=registration_id,
                operation_id=operation_id,
                now=now,
            )

            assert await store.finish_registration_success(
                registration_id=registration_id,
                operation_id=operation_id,
                provider_ticket_id=uuid4(),
                now=now,
            )

            async with factory.begin() as session:
                await session.execute(
                    update(TicketOperation)
                    .where(TicketOperation.id == operation_id)
                    .values(finished_at=now - timedelta(hours=25))
                )

            found = await store.get_registration_by_key(key)

            assert found is None

            async with factory() as session:
                operation = await session.get(TicketOperation, operation_id)
                ticket = await session.get(Ticket, registration_id)

                outbox_count = await session.scalar(
                    select(func.count())
                    .select_from(Outbox)
                    .where(Outbox.payload["ticket_id"].astext == str(registration_id))
                )

                assert operation is not None
                assert operation.status == "success"
                assert operation.status is OperationStatus.SUCCESS
                assert operation.idempotency_key is None

                assert ticket is not None
                assert ticket.status == "active"
                assert ticket.status is TicketStatus.ACTIVE
                assert outbox_count == 1

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_custom_idempotency_ttl_48_hours() -> None:
    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            store = PostgresTicketStore(
                factory,
                idempotency_ttl_hours=48,
            )

            now = datetime.now(UTC)
            key = f"ttl-48-{uuid4()}"
            registration_id = uuid4()
            operation_id = uuid4()

            request = TicketCreate(
                event_id=event_id,
                first_name="Anna",
                last_name="Test",
                email="anna@example.com",
                seat="A37",
                idempotency_key=key,
            )

            assert await store.reserve_registration(
                registration_id=registration_id,
                operation_id=operation_id,
                request=request,
                request_fingerprint="ttl-48-test",
                now=now,
            )

            assert await store.mark_registration_in_flight(
                registration_id=registration_id,
                operation_id=operation_id,
                now=now,
            )

            assert await store.finish_registration_success(
                registration_id=registration_id,
                operation_id=operation_id,
                provider_ticket_id=uuid4(),
                now=now,
            )

            async with factory.begin() as session:
                await session.execute(
                    update(TicketOperation)
                    .where(TicketOperation.id == operation_id)
                    .values(finished_at=now - timedelta(hours=25))
                )

            active = await store.get_registration_by_key(key)

            assert active is not None
            assert active.registration_id == registration_id
            assert active.status == "success"
            assert active.idempotency_key == key

            async with factory.begin() as session:
                await session.execute(
                    update(TicketOperation)
                    .where(TicketOperation.id == operation_id)
                    .values(finished_at=now - timedelta(hours=49))
                )

            expired = await store.get_registration_by_key(key)

            assert expired is None

            async with factory() as session:
                operation = await session.get(TicketOperation, operation_id)
                ticket = await session.get(Ticket, registration_id)

                outbox_count = await session.scalar(
                    select(func.count())
                    .select_from(Outbox)
                    .where(Outbox.payload["ticket_id"].astext == str(registration_id))
                )

                assert operation is not None
                assert operation.status == "success"
                assert operation.status is OperationStatus.SUCCESS
                assert operation.idempotency_key is None

                assert ticket is not None
                assert ticket.status == "active"
                assert ticket.status is TicketStatus.ACTIVE
                assert outbox_count == 1

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_sync_status_round_trip() -> None:

    async def scenario() -> None:
        engine, factory, _ = await prepare_database()

        try:
            now = datetime.now(UTC)

            async with factory() as session:
                repository = PostgresSyncStateRepository(session)

                statement = (
                    insert(SyncState)
                    .values(
                        id=1,
                        sync_status=SyncStatus.NEVER_RUN,
                        updated_at=now,
                    )
                    .on_conflict_do_update(
                        index_elements=[SyncState.id],
                        set_={
                            "sync_status": SyncStatus.NEVER_RUN,
                            "updated_at": now,
                        },
                    )
                )

                await session.execute(statement)

                query = select(SyncState.sync_status).where(SyncState.id == 1)

                assert await session.scalar(query) is SyncStatus.NEVER_RUN

                lease = await repository.try_start(
                    now=now,
                    stale_before=now - timedelta(minutes=5),
                )

                assert lease is not None
                assert await session.scalar(query) is SyncStatus.RUNNING

                assert await repository.finish_success(
                    lease=lease,
                    now=now,
                    newest_changed_at=None,
                )

                assert await session.scalar(query) is SyncStatus.SUCCESS

                lease = await repository.try_start(
                    now=now,
                    stale_before=now - timedelta(minutes=5),
                )

                assert lease is not None

                assert await repository.finish_failure(
                    lease=lease,
                    now=now,
                    error="Test synchronization error",
                )

                assert await session.scalar(query) is SyncStatus.FAILED

                await session.rollback()

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_outbox_unknown_event_type() -> None:
    async def scenario() -> None:
        engine, factory, _ = await prepare_database()

        try:
            event_id = uuid4()

            async with factory() as session:
                session.add(
                    Outbox(
                        id=event_id,
                        event_type="unknown_event",
                        payload={},
                        status=OutboxStatus.PENDING,
                        created_at=datetime.now(UTC),
                    )
                )

                await session.flush()
                session.expunge_all()

                outbox_event = await session.get(Outbox, event_id)

                assert outbox_event is not None
                assert outbox_event.event_type == "unknown_event"
                assert type(outbox_event.event_type) is str
                assert outbox_event.status is OutboxStatus.PENDING

                await session.rollback()

        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_event_status_round_trip() -> None:

    async def scenario() -> None:
        engine, factory, event_id = await prepare_database()

        try:
            async with factory() as session:
                for value, expected in (
                    ("new", EventStatus.NEW),
                    ("published", EventStatus.PUBLISHED),
                    ("finished", EventStatus("finished")),
                    ("registration_closed", EventStatus("registration_closed")),
                    ("future_status", "future_status"),
                ):
                    await session.execute(
                        update(Event).where(Event.id == event_id).values(status=value)
                    )

                    actual = await session.scalar(
                        select(Event.status).where(Event.id == event_id)
                    )

                    assert actual == expected
                    assert type(actual) is type(expected)

                await session.rollback()

        finally:
            await engine.dispose()

    asyncio.run(scenario())
