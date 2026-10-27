import asyncio
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.tickets import get_ticket_cancellation_service
from app.application.tickets.cancellation_service import TicketCancellationService
from app.application.tickets.schemas import TicketCreate
from app.infrastructure.db.base import Base
from app.infrastructure.db.models import (
    Event,
    Place,
    Ticket,
    TicketOperation,
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
                assert ticket.provider_ticket_id == provider_ticket_id
                assert operation.status == "success"

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
                assert operation.status == "unknown"

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
                assert operation.status == "failed"
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
                assert ticket.provider_ticket_id is not None

                assert operation.registration_id == ticket_id
                assert operation.operation_type == "cancel"
                assert operation.status == "prepared"

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
                assert ticket.cancelled_at is not None
                assert operation.status == "success"
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
                assert operation.status == "prepared"

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
                assert operation.status == "failed"

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
                assert operation.status == "unknown"

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
                assert operation.status == "failed"
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
                assert operation.status == "in_flight"

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
                assert ticket.cancelled_at is not None
                assert operation_count == 1

        finally:
            await engine.dispose()

    asyncio.run(scenario())
