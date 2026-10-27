import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.application.tickets.state import TicketCancellationSnapshot
from app.infrastructure.db.ticket_repository import PostgresTicketRepository
from app.infrastructure.db.ticket_store import PostgresTicketStore


def test_repository_reads_ticket_for_cancellation() -> None:
    async def scenario() -> None:
        ticket_id = uuid4()
        event_id = uuid4()
        provider_ticket_id = uuid4()
        event_time = datetime.now(UTC)

        row = {
            "ticket_id": ticket_id,
            "event_id": event_id,
            "provider_ticket_id": provider_ticket_id,
            "status": "active",
            "event_time": event_time,
        }

        result = MagicMock()
        result.mappings.return_value.one_or_none.return_value = row

        session = MagicMock()
        session.execute = AsyncMock(return_value=result)

        repository = PostgresTicketRepository(session)

        ticket = await repository.get_ticket_for_cancellation(
            ticket_id=ticket_id,
        )

        assert isinstance(ticket, TicketCancellationSnapshot)
        assert ticket.ticket_id == ticket_id
        assert ticket.event_id == event_id
        assert ticket.provider_ticket_id == provider_ticket_id
        assert ticket.status == "active"

        session.execute.assert_awaited_once()

        statement = session.execute.await_args.args[0]

        sql = str(
            statement.compile(
                dialect=postgresql.dialect(),
            )
        )

        assert "tickets" in sql
        assert "events" in sql

    asyncio.run(scenario())


def test_repository_returns_none_for_missing_ticket() -> None:
    async def scenario() -> None:
        result = MagicMock()
        result.mappings.return_value.one_or_none.return_value = None

        session = MagicMock()
        session.execute = AsyncMock(return_value=result)

        repository = PostgresTicketRepository(session)

        ticket = await repository.get_ticket_for_cancellation(
            ticket_id=uuid4(),
        )

        assert ticket is None

    asyncio.run(scenario())


def test_store_reads_ticket_in_short_session() -> None:
    async def scenario() -> None:
        session = MagicMock()

        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=session)
        context.__aexit__ = AsyncMock(return_value=False)

        session_factory = MagicMock()
        session_factory.return_value = context

        expected_ticket = MagicMock()
        ticket_id = uuid4()

        with patch(
            "app.infrastructure.db.ticket_store.PostgresTicketRepository"
        ) as repository_class:
            repository = repository_class.return_value
            repository.get_ticket_for_cancellation = AsyncMock(
                return_value=expected_ticket
            )

            store = PostgresTicketStore(session_factory)

            ticket = await store.get_ticket_for_cancellation(
                ticket_id=ticket_id,
            )

            assert ticket is expected_ticket

            repository_class.assert_called_once_with(session)

            repository.get_ticket_for_cancellation.assert_awaited_once_with(
                ticket_id=ticket_id,
            )

            context.__aenter__.assert_awaited_once()
            context.__aexit__.assert_awaited_once_with(
                None,
                None,
                None,
            )

    asyncio.run(scenario())
