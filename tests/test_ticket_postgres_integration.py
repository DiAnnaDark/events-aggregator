import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

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
