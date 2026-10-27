from datetime import UTC, date, datetime
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.events import get_event_read_repository
from app.application.events.schemas import (
    EventDetail,
    EventSummary,
    PlaceDetail,
    PlaceSummary,
)
from app.infrastructure.db.event_read_repository import (
    PostgresEventReadRepository,
)
from app.main import create_app

EVENT_ID = UUID("22222222-2222-4222-8222-222222222222")
PLACE_ID = UUID("11111111-1111-4111-8111-111111111111")


def make_event() -> EventSummary:
    return EventSummary(
        id=EVENT_ID,
        name="Autumn Concert",
        place=PlaceSummary(
            id=PLACE_ID,
            name="Concert Hall",
            city="Moscow",
            address="Main Street",
        ),
        event_time=datetime(2026, 11, 2, 19, tzinfo=UTC),
        registration_deadline=datetime(2026, 11, 1, 19, tzinfo=UTC),
        status="published",
        number_of_visitors=20,
    )


def make_app(repository: AsyncMock) -> FastAPI:
    application = create_app(enable_sync=False)

    application.dependency_overrides[get_event_read_repository] = lambda: repository

    return application


def test_list_events_returns_pagination_links() -> None:
    repository = AsyncMock(spec=PostgresEventReadRepository)
    repository.list_events.return_value = (3, [make_event()])

    application = make_app(repository)

    with TestClient(application) as client:
        response = client.get(
            "/api/events",
            params={
                "date_from": "2026-10-27",
                "page": 2,
                "page_size": 1,
            },
        )

    assert response.status_code == 200

    data = response.json()

    assert data["count"] == 3
    assert len(data["results"]) == 1
    assert data["results"][0]["name"] == "Autumn Concert"
    assert data["results"][0]["place"]["city"] == "Moscow"

    next_params = parse_qs(urlsplit(data["next"]).query)
    previous_params = parse_qs(urlsplit(data["previous"]).query)

    assert next_params["page"] == ["3"]
    assert previous_params["page"] == ["1"]
    assert next_params["date_from"] == ["2026-10-27"]

    repository.list_events.assert_awaited_once_with(
        date_from=date(2026, 10, 27),
        page=2,
        page_size=1,
    )


def test_list_events_uses_defaults() -> None:
    repository = AsyncMock(spec=PostgresEventReadRepository)
    repository.list_events.return_value = (1, [make_event()])

    with TestClient(make_app(repository)) as client:
        response = client.get("/api/events")

    assert response.status_code == 200

    data = response.json()

    assert data["count"] == 1
    assert data["next"] is None
    assert data["previous"] is None

    repository.list_events.assert_awaited_once_with(
        date_from=None,
        page=1,
        page_size=20,
    )


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page_size": 0},
        {"date_from": "invalid-date"},
    ],
)
def test_invalid_query_parameters(params: dict[str, object]) -> None:
    repository = AsyncMock(spec=PostgresEventReadRepository)

    with TestClient(make_app(repository)) as client:
        response = client.get("/api/events", params=params)

    assert response.status_code == 422
    repository.list_events.assert_not_awaited()


def test_get_event_returns_details() -> None:
    repository = AsyncMock(spec=PostgresEventReadRepository)

    summary = make_event()

    repository.get_event.return_value = EventDetail(
        **summary.model_dump(exclude={"place"}),
        place=PlaceDetail(
            **summary.place.model_dump(),
            seats_pattern="A1-100",
        ),
    )

    with TestClient(make_app(repository)) as client:
        response = client.get(f"/api/events/{EVENT_ID}")

    assert response.status_code == 200

    data = response.json()

    assert data["id"] == str(EVENT_ID)
    assert data["name"] == "Autumn Concert"
    assert data["place"]["seats_pattern"] == "A1-100"

    repository.get_event.assert_awaited_once_with(EVENT_ID)


def test_get_missing_event_returns_404() -> None:
    repository = AsyncMock(spec=PostgresEventReadRepository)
    repository.get_event.return_value = None

    with TestClient(make_app(repository)) as client:
        response = client.get(f"/api/events/{EVENT_ID}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Event not found"


def test_events_api_returns_503_without_database() -> None:
    application = create_app(enable_sync=False)

    with TestClient(application) as client:
        response = client.get("/api/events")

    assert response.status_code == 503
