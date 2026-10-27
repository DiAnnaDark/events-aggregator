import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.infrastructure.db.ticket_event_reader import (
    PostgresTicketEventReader,
)


def make_session_factory():
    session = MagicMock()

    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)

    session_factory = MagicMock()
    session_factory.return_value = context

    return session_factory, session, context


def test_event_reader_uses_short_database_session() -> None:
    async def scenario() -> None:
        session_factory, session, context = make_session_factory()

        event_id = uuid4()
        expected_event = object()

        with patch(
            "app.infrastructure.db.ticket_event_reader.PostgresEventReadRepository"
        ) as repository_class:
            repository = repository_class.return_value
            repository.get_event = AsyncMock(return_value=expected_event)

            reader = PostgresTicketEventReader(session_factory)

            event = await reader.get_event(event_id)

            assert event is expected_event

            session_factory.assert_called_once()
            repository_class.assert_called_once_with(session)
            repository.get_event.assert_awaited_once_with(event_id)

            context.__aenter__.assert_awaited_once()
            context.__aexit__.assert_awaited_once_with(
                None,
                None,
                None,
            )

    asyncio.run(scenario())


def test_event_reader_closes_session_after_failure() -> None:
    async def scenario() -> None:
        session_factory, session, context = make_session_factory()

        with patch(
            "app.infrastructure.db.ticket_event_reader.PostgresEventReadRepository"
        ) as repository_class:
            repository = repository_class.return_value
            repository.get_event = AsyncMock(
                side_effect=RuntimeError("Database failure")
            )

            reader = PostgresTicketEventReader(session_factory)

            with pytest.raises(
                RuntimeError,
                match="Database failure",
            ):
                await reader.get_event(uuid4())

            repository_class.assert_called_once_with(session)

            context.__aenter__.assert_awaited_once()
            context.__aexit__.assert_awaited_once()

    asyncio.run(scenario())
