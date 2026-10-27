import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.application.tickets.schemas import TicketCreate
from app.infrastructure.db.ticket_store import PostgresTicketStore


def make_request() -> TicketCreate:
    return TicketCreate(
        event_id=uuid4(),
        first_name="Anna",
        last_name="Test",
        email="anna@example.com",
        seat="A12",
    )


def make_transaction():
    session = MagicMock()

    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=session)
    transaction.__aexit__ = AsyncMock(return_value=False)

    session_factory = MagicMock()
    session_factory.begin.return_value = transaction

    return session_factory, session, transaction


def test_store_reserves_registration_in_transaction() -> None:
    async def scenario() -> None:
        session_factory, session, transaction = make_transaction()

        with patch(
            "app.infrastructure.db.ticket_store.PostgresTicketRepository"
        ) as repository_class:
            repository = repository_class.return_value
            repository.reserve_registration = AsyncMock(return_value=True)

            store = PostgresTicketStore(session_factory)

            reserved = await store.reserve_registration(
                registration_id=uuid4(),
                operation_id=uuid4(),
                request=make_request(),
                request_fingerprint="test-fingerprint",
                now=datetime.now(UTC),
            )

            assert reserved is True

            session_factory.begin.assert_called_once()
            repository_class.assert_called_once_with(session)

            repository.reserve_registration.assert_awaited_once()
            transaction.__aenter__.assert_awaited_once()
            transaction.__aexit__.assert_awaited_once_with(
                None,
                None,
                None,
            )

    asyncio.run(scenario())


def test_store_propagates_database_error() -> None:
    async def scenario() -> None:
        session_factory, _, transaction = make_transaction()

        with patch(
            "app.infrastructure.db.ticket_store.PostgresTicketRepository"
        ) as repository_class:
            repository = repository_class.return_value
            repository.reserve_registration = AsyncMock(
                side_effect=RuntimeError("Database failure")
            )

            store = PostgresTicketStore(session_factory)

            with pytest.raises(RuntimeError, match="Database failure"):
                await store.reserve_registration(
                    registration_id=uuid4(),
                    operation_id=uuid4(),
                    request=make_request(),
                    request_fingerprint="test-fingerprint",
                    now=datetime.now(UTC),
                )

            transaction.__aexit__.assert_awaited_once()

            exit_arguments = transaction.__aexit__.await_args.args

            assert exit_arguments[0] is RuntimeError
            assert isinstance(exit_arguments[1], RuntimeError)

    asyncio.run(scenario())
