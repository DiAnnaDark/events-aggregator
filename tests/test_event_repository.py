import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.sync.schemas import EventSnapshot
from app.infrastructure.db.event_repository import PostgresEventRepository


def make_event_data() -> dict:
    return {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "name": "Python Conference",
        "place": {
            "id": "650e8400-e29b-41d4-a716-446655440001",
            "name": "Conference Hall",
            "city": "Moscow",
            "address": "Lenina 1",
            "seats_pattern": "A1-1000",
            "created_at": "2026-01-01T10:00:00+03:00",
            "changed_at": "2026-01-01T10:00:00+03:00",
        },
        "event_time": "2026-02-01T17:00:00+03:00",
        "registration_deadline": "2026-01-31T17:00:00+03:00",
        "status": "published",
        "number_of_visitors": 5,
        "created_at": "2026-01-01T10:00:00+03:00",
        "changed_at": "2026-01-02T10:00:00+03:00",
        "status_changed_at": "2026-01-02T10:00:00+03:00",
    }


def test_repository_builds_guarded_upserts() -> None:
    event = EventSnapshot.model_validate(make_event_data())
    session = AsyncMock(spec=AsyncSession)

    repository = PostgresEventRepository(session)

    asyncio.run(repository.upsert(event))

    assert session.execute.await_count == 2
    session.commit.assert_not_awaited()

    place_statement = session.execute.await_args_list[0].args[0]
    event_statement = session.execute.await_args_list[1].args[0]

    dialect = postgresql.dialect()

    place_sql = str(place_statement.compile(dialect=dialect))
    event_sql = str(event_statement.compile(dialect=dialect))

    assert "ON CONFLICT (id) DO UPDATE" in place_sql
    assert "ON CONFLICT (id) DO UPDATE" in event_sql

    assert "WHERE places.changed_at <= excluded.changed_at" in place_sql

    assert "WHERE events.changed_at <= excluded.changed_at" in event_sql

    assert "created_at =" not in place_sql.partition("DO UPDATE SET")[2]

    assert "created_at =" not in event_sql.partition("DO UPDATE SET")[2]


def test_event_rejects_negative_visitor_count() -> None:
    data = make_event_data()
    data["number_of_visitors"] = -1

    with pytest.raises(ValidationError):
        EventSnapshot.model_validate(data)
