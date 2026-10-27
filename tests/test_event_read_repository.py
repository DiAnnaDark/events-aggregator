import asyncio
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.events.schemas import EventDetail
from app.infrastructure.db.event_read_repository import (
    PostgresEventReadRepository,
)
from app.infrastructure.db.models import Event, Place


def make_models() -> tuple[Event, Place]:
    now = datetime(2026, 10, 27, 12, tzinfo=UTC)

    place = Place(
        id=UUID("11111111-1111-4111-8111-111111111111"),
        name="Concert Hall",
        city="Moscow",
        address="Main Street",
        seats_pattern="A1-100",
        created_at=now,
        changed_at=now,
    )

    event = Event(
        id=UUID("22222222-2222-4222-8222-222222222222"),
        place_id=place.id,
        name="Music Festival",
        event_time=now,
        registration_deadline=now,
        status="published",
        number_of_visitors=50,
        changed_at=now,
        created_at=now,
        status_changed_at=now,
    )

    return event, place


def test_list_events_filters_and_paginates() -> None:
    event, place = make_models()

    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = 271

    result = MagicMock()
    result.all.return_value = [(event, place)]
    session.execute.return_value = result

    repository = PostgresEventReadRepository(session)

    count, events = asyncio.run(
        repository.list_events(
            date_from=date(2026, 10, 27),
            page=3,
            page_size=2,
        )
    )

    assert count == 271
    assert len(events) == 1
    assert events[0].id == event.id
    assert events[0].place.city == "Moscow"

    count_query = session.scalar.await_args.args[0]
    events_query = session.execute.await_args.args[0]

    count_sql = str(count_query.compile(dialect=postgresql.dialect()))

    compiled = events_query.compile(dialect=postgresql.dialect())

    events_sql = str(compiled)

    assert "WHERE events.event_time >=" in count_sql
    assert "WHERE events.event_time >=" in events_sql
    assert "JOIN places" in events_sql
    assert "ORDER BY events.event_time, events.id" in events_sql
    assert "LIMIT" in events_sql
    assert "OFFSET" in events_sql

    parameters = compiled.params.values()

    assert datetime(2026, 10, 27, tzinfo=UTC) in parameters
    assert 2 in parameters
    assert 4 in parameters


def test_get_event_returns_details() -> None:
    event, place = make_models()

    session = AsyncMock(spec=AsyncSession)

    result = MagicMock()
    result.one_or_none.return_value = (event, place)
    session.execute.return_value = result

    repository = PostgresEventReadRepository(session)

    details = asyncio.run(repository.get_event(event.id))

    assert isinstance(details, EventDetail)
    assert details.id == event.id
    assert details.name == "Music Festival"
    assert details.place.seats_pattern == "A1-100"


def test_get_event_returns_none_when_missing() -> None:
    event, _ = make_models()

    session = AsyncMock(spec=AsyncSession)

    result = MagicMock()
    result.one_or_none.return_value = None
    session.execute.return_value = result

    repository = PostgresEventReadRepository(session)

    details = asyncio.run(repository.get_event(event.id))

    assert details is None


def test_list_events_rejects_invalid_pagination() -> None:
    session = AsyncMock(spec=AsyncSession)
    repository = PostgresEventReadRepository(session)

    for page, page_size in ((0, 20), (1, 0)):
        with pytest.raises(ValueError, match="Invalid pagination"):
            asyncio.run(
                repository.list_events(
                    date_from=None,
                    page=page,
                    page_size=page_size,
                )
            )

    session.scalar.assert_not_awaited()
    session.execute.assert_not_awaited()
