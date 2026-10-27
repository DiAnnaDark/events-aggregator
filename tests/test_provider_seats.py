import asyncio
from uuid import UUID

import httpx
import pytest

from app.infrastructure.provider.client import EventsProviderClient

EVENT_ID = UUID("550e8400-e29b-41d4-a716-446655440000")


def test_provider_seats_returns_available_seats() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert request.url.path == f"/api/events/{EVENT_ID}/seats/"
            assert request.headers["x-api-key"] == "test-key"

            return httpx.Response(
                200,
                json={"seats": ["A1", "A3", "B2"]},
            )

        transport = httpx.MockTransport(handler)

        async with httpx.AsyncClient(transport=transport) as client:
            provider = EventsProviderClient(
                base_url="http://provider.test/",
                api_key="test-key",
                client=client,
            )

            seats = await provider.seats(event_id=EVENT_ID)

            assert seats == ["A1", "A3", "B2"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"seats": "A1"},
        {"seats": ["A1", 123]},
    ],
)
def test_provider_seats_rejects_invalid_response(
    payload: dict[str, object],
) -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        transport = httpx.MockTransport(handler)

        async with httpx.AsyncClient(transport=transport) as client:
            provider = EventsProviderClient(
                base_url="http://provider.test",
                api_key="test-key",
                client=client,
            )

            with pytest.raises(
                ValueError,
                match="Invalid Events Provider seats response",
            ):
                await provider.seats(event_id=EVENT_ID)

    asyncio.run(scenario())


def test_provider_seats_propagates_http_error() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                json={"detail": "Event not found"},
            )

        transport = httpx.MockTransport(handler)

        async with httpx.AsyncClient(transport=transport) as client:
            provider = EventsProviderClient(
                base_url="http://provider.test",
                api_key="test-key",
                client=client,
            )

            with pytest.raises(httpx.HTTPStatusError) as error:
                await provider.seats(event_id=EVENT_ID)

            assert error.value.response.status_code == 404

    asyncio.run(scenario())
