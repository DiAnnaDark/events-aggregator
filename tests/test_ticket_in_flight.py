import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.infrastructure.db.ticket_repository import PostgresTicketRepository
from app.infrastructure.db.ticket_store import PostgresTicketStore


def test_repository_marks_registration_in_flight() -> None:
    async def scenario() -> None:
        operation_id = uuid4()
        registration_id = uuid4()

        result = MagicMock()
        result.scalar_one_or_none.return_value = operation_id

        session = MagicMock()
        session.execute = AsyncMock(return_value=result)

        repository = PostgresTicketRepository(session)

        updated = await repository.mark_registration_in_flight(
            registration_id=registration_id,
            operation_id=operation_id,
            now=datetime.now(UTC),
        )

        assert updated is True
        session.execute.assert_awaited_once()

        statement = session.execute.await_args.args[0]

        compiled = statement.compile(
            dialect=postgresql.dialect(),
        )

        sql = str(compiled)

        assert "UPDATE ticket_operations" in sql
        assert "RETURNING ticket_operations.id" in sql

        values = list(compiled.params.values())

        assert "prepared" in values
        assert "in_flight" in values
        assert "pending" in values
        assert "register" in values

    asyncio.run(scenario())


def test_repository_rejects_invalid_transition() -> None:
    async def scenario() -> None:
        result = MagicMock()
        result.scalar_one_or_none.return_value = None

        session = MagicMock()
        session.execute = AsyncMock(return_value=result)

        repository = PostgresTicketRepository(session)

        updated = await repository.mark_registration_in_flight(
            registration_id=uuid4(),
            operation_id=uuid4(),
            now=datetime.now(UTC),
        )

        assert updated is False
        session.execute.assert_awaited_once()

    asyncio.run(scenario())


def test_store_marks_registration_in_transaction() -> None:
    async def scenario() -> None:
        session = MagicMock()

        transaction = MagicMock()
        transaction.__aenter__ = AsyncMock(return_value=session)
        transaction.__aexit__ = AsyncMock(return_value=False)

        session_factory = MagicMock()
        session_factory.begin.return_value = transaction

        with patch(
            "app.infrastructure.db.ticket_store.PostgresTicketRepository"
        ) as repository_class:
            repository = repository_class.return_value
            repository.mark_registration_in_flight = AsyncMock(return_value=True)

            store = PostgresTicketStore(session_factory)

            updated = await store.mark_registration_in_flight(
                registration_id=uuid4(),
                operation_id=uuid4(),
                now=datetime.now(UTC),
            )

            assert updated is True

            session_factory.begin.assert_called_once()
            repository_class.assert_called_once_with(session)

            repository.mark_registration_in_flight.assert_awaited_once()

            transaction.__aenter__.assert_awaited_once()
            transaction.__aexit__.assert_awaited_once_with(
                None,
                None,
                None,
            )

    asyncio.run(scenario())
