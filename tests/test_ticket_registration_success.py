import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.infrastructure.db.ticket_repository import (
    PostgresTicketRepository,
    TicketStateConflict,
)
from app.infrastructure.db.ticket_store import PostgresTicketStore


def make_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def test_repository_confirms_registration() -> None:
    async def scenario() -> None:
        registration_id = uuid4()
        operation_id = uuid4()
        provider_ticket_id = uuid4()
        now = datetime.now(UTC)

        session = MagicMock()
        session.execute = AsyncMock(
            side_effect=[
                make_result(operation_id),
                make_result(registration_id),
            ]
        )

        repository = PostgresTicketRepository(session)

        confirmed = await repository.finish_registration_success(
            registration_id=registration_id,
            operation_id=operation_id,
            provider_ticket_id=provider_ticket_id,
            now=now,
        )

        assert confirmed is True
        assert session.execute.await_count == 2

        statements = [call.args[0] for call in session.execute.await_args_list]

        operation_values = (
            statements[0]
            .compile(
                dialect=postgresql.dialect(),
            )
            .params
        )

        ticket_values = (
            statements[1]
            .compile(
                dialect=postgresql.dialect(),
            )
            .params
        )

        assert operation_values["status"] == "success"
        assert operation_values["provider_http_status"] == 201
        assert operation_values["finished_at"] == now

        assert ticket_values["status"] == "active"
        assert ticket_values["provider_ticket_id"] == provider_ticket_id
        assert ticket_values["registered_at"] == now

    asyncio.run(scenario())


def test_repository_rejects_repeated_confirmation() -> None:
    async def scenario() -> None:
        session = MagicMock()
        session.execute = AsyncMock(return_value=make_result(None))

        repository = PostgresTicketRepository(session)

        confirmed = await repository.finish_registration_success(
            registration_id=uuid4(),
            operation_id=uuid4(),
            provider_ticket_id=uuid4(),
            now=datetime.now(UTC),
        )

        assert confirmed is False
        session.execute.assert_awaited_once()

    asyncio.run(scenario())


def test_repository_detects_ticket_state_conflict() -> None:
    async def scenario() -> None:
        operation_id = uuid4()

        session = MagicMock()
        session.execute = AsyncMock(
            side_effect=[
                make_result(operation_id),
                make_result(None),
            ]
        )

        repository = PostgresTicketRepository(session)

        with pytest.raises(TicketStateConflict):
            await repository.finish_registration_success(
                registration_id=uuid4(),
                operation_id=operation_id,
                provider_ticket_id=uuid4(),
                now=datetime.now(UTC),
            )

        assert session.execute.await_count == 2

    asyncio.run(scenario())


def test_store_confirms_registration_in_transaction() -> None:
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
            repository.finish_registration_success = AsyncMock(return_value=True)

            store = PostgresTicketStore(session_factory)

            confirmed = await store.finish_registration_success(
                registration_id=uuid4(),
                operation_id=uuid4(),
                provider_ticket_id=uuid4(),
                now=datetime.now(UTC),
            )

            assert confirmed is True

            session_factory.begin.assert_called_once()
            repository_class.assert_called_once_with(session)

            repository.finish_registration_success.assert_awaited_once()

            transaction.__aenter__.assert_awaited_once()
            transaction.__aexit__.assert_awaited_once_with(
                None,
                None,
                None,
            )

    asyncio.run(scenario())
