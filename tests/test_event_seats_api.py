from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.events import (
    get_event_read_repository,
    get_seats_service,
)
from app.application.events.seats_service import SeatsService
from app.infrastructure.provider.client import EventsProviderClient
from app.main import create_app

EVENT_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


def make_app(
    *,
    status: str | None = "published",
    provider_error: Exception | None = None,
) -> tuple[FastAPI, AsyncMock, AsyncMock]:
    application = create_app(enable_sync=False)

    event = None if status is None else SimpleNamespace(status=status)

    get_event = AsyncMock(return_value=event)

    get_available_seats = AsyncMock(
        return_value=["A1", "A3"],
        side_effect=provider_error,
    )

    repository = SimpleNamespace(get_event=get_event)

    service = SimpleNamespace(
        get_available_seats=get_available_seats,
    )

    application.dependency_overrides[get_event_read_repository] = lambda: repository

    application.dependency_overrides[get_seats_service] = lambda: service

    return application, get_event, get_available_seats


def make_http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request(
        "GET",
        "http://provider.test/api/events/seats/",
    )

    response = httpx.Response(
        status_code,
        request=request,
    )

    return httpx.HTTPStatusError(
        "Provider HTTP error",
        request=request,
        response=response,
    )


def test_get_available_seats_returns_200() -> None:
    application, get_event, get_available_seats = make_app()

    with TestClient(application) as client:
        response = client.get(f"/api/events/{EVENT_ID}/seats")

    assert response.status_code == 200
    assert response.json() == {
        "event_id": str(EVENT_ID),
        "available_seats": ["A1", "A3"],
    }

    get_event.assert_awaited_once_with(EVENT_ID)
    get_available_seats.assert_awaited_once_with(EVENT_ID)


def test_unknown_event_returns_404() -> None:
    application, _, get_available_seats = make_app(status=None)

    with TestClient(application) as client:
        response = client.get(f"/api/events/{EVENT_ID}/seats")

    assert response.status_code == 404
    get_available_seats.assert_not_awaited()


def test_unpublished_event_returns_409() -> None:
    application, _, get_available_seats = make_app(status="new")

    with TestClient(application) as client:
        response = client.get(f"/api/events/{EVENT_ID}/seats")

    assert response.status_code == 409
    get_available_seats.assert_not_awaited()


def test_missing_provider_returns_503() -> None:
    application = create_app(enable_sync=False)

    repository = SimpleNamespace(
        get_event=AsyncMock(return_value=SimpleNamespace(status="published"))
    )

    application.dependency_overrides[get_event_read_repository] = lambda: repository

    with TestClient(application) as client:
        response = client.get(f"/api/events/{EVENT_ID}/seats")

    assert response.status_code == 503


@pytest.mark.parametrize(
    ("provider_error", "expected_status"),
    [
        (httpx.ReadTimeout("Provider timeout"), 504),
        (httpx.ConnectError("Connection failed"), 502),
        (make_http_error(404), 404),
        (make_http_error(500), 502),
        (ValueError("Invalid provider response"), 502),
    ],
)
def test_provider_errors_are_handled(
    provider_error: Exception,
    expected_status: int,
) -> None:
    application, _, _ = make_app(provider_error=provider_error)

    with TestClient(application) as client:
        response = client.get(f"/api/events/{EVENT_ID}/seats")

    assert response.status_code == expected_status


def test_invalid_event_uuid_returns_422() -> None:
    application, _, _ = make_app()

    with TestClient(application) as client:
        response = client.get("/api/events/not-a-uuid/seats")

    assert response.status_code == 422


def test_seats_api_uses_provider_and_cache() -> None:
    provider_response = httpx.Response(
        200,
        json={"seats": ["A1", "A3"]},
        request=httpx.Request(
            "GET",
            f"http://provider.test/api/events/{EVENT_ID}/seats/",
        ),
    )

    http_client = SimpleNamespace(get=AsyncMock(return_value=provider_response))

    provider = EventsProviderClient(
        base_url="http://provider.test",
        api_key="test-key",
        client=http_client,
    )

    seats_service = SeatsService(provider)

    repository = SimpleNamespace(
        get_event=AsyncMock(return_value=SimpleNamespace(status="published"))
    )

    application = create_app(enable_sync=False)

    application.dependency_overrides[get_event_read_repository] = lambda: repository

    application.dependency_overrides[get_seats_service] = lambda: seats_service

    with TestClient(application) as client:
        first = client.get(f"/api/events/{EVENT_ID}/seats")
        second = client.get(f"/api/events/{EVENT_ID}/seats")

    assert first.status_code == 200
    assert second.status_code == 200

    assert first.json() == {
        "event_id": str(EVENT_ID),
        "available_seats": ["A1", "A3"],
    }

    assert second.json() == first.json()

    http_client.get.assert_awaited_once_with(
        f"http://provider.test/api/events/{EVENT_ID}/seats/",
        headers={"x-api-key": "test-key"},
        timeout=10.0,
    )
