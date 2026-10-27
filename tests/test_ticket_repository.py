import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.application.tickets.schemas import TicketCreate
from app.infrastructure.db.ticket_repository import PostgresTicketRepository


def make_ticket_request() -> TicketCreate:
    return TicketCreate(
        event_id=uuid4(),
        first_name="Anna",
        last_name="Test",
        email="anna@example.com",
        seat="A12",
    )


def test_reservation_creates_ticket_and_operation() -> None:
    async def scenario() -> None:
        registration_id = uuid4()
        operation_id = uuid4()
        request = make_ticket_request()
        now = datetime.now(UTC)

        insert_result = MagicMock()
        insert_result.scalar_one_or_none.return_value = registration_id

        session = MagicMock()
        session.execute = AsyncMock(
            side_effect=[
                insert_result,
                MagicMock(),
            ]
        )
        session.commit = AsyncMock()

        repository = PostgresTicketRepository(session)

        reserved = await repository.reserve_registration(
            registration_id=registration_id,
            operation_id=operation_id,
            request=request,
            request_fingerprint="test-fingerprint",
            now=now,
        )

        assert reserved is True
        assert session.execute.await_count == 2

        statements = [call.args[0] for call in session.execute.await_args_list]

        ticket_statement = statements[0]
        operation_statement = statements[1]

        ticket_sql = str(
            ticket_statement.compile(
                dialect=postgresql.dialect(),
            )
        )

        assert "INSERT INTO tickets" in ticket_sql
        assert "ON CONFLICT (event_id, seat)" in ticket_sql
        assert "DO NOTHING" in ticket_sql

        operation_sql = str(
            operation_statement.compile(
                dialect=postgresql.dialect(),
            )
        )

        assert "INSERT INTO ticket_operations" in operation_sql

        operation_values = operation_statement.compile(
            dialect=postgresql.dialect(),
        ).params

        assert operation_values["registration_id"] == registration_id
        assert operation_values["status"] == "prepared"
        assert operation_values["operation_type"] == "register"

        session.commit.assert_not_awaited()

    asyncio.run(scenario())


def test_reservation_rejects_occupied_seat() -> None:
    async def scenario() -> None:
        insert_result = MagicMock()
        insert_result.scalar_one_or_none.return_value = None

        session = MagicMock()
        session.execute = AsyncMock(return_value=insert_result)

        repository = PostgresTicketRepository(session)

        reserved = await repository.reserve_registration(
            registration_id=uuid4(),
            operation_id=uuid4(),
            request=make_ticket_request(),
            request_fingerprint="test-fingerprint",
            now=datetime.now(UTC),
        )

        assert reserved is False

        # Only the ticket INSERT is attempted.
        # No registration operation should be created.
        session.execute.assert_awaited_once()

    asyncio.run(scenario())
