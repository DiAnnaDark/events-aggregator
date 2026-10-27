import asyncio
import json
from uuid import UUID

import httpx
import pytest

from app.infrastructure.provider.client import EventsProviderClient

EVENT_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
TICKET_ID = UUID("650e8400-e29b-41d4-a716-446655440001")


def test_register_returns_provider_ticket_id() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert request.url.path == f"/api/events/{EVENT_ID}/register/"
            assert request.headers["x-api-key"] == "test-key"

            assert json.loads(request.content) == {
                "first_name": "Анна",
                "last_name": "Иванова",
                "email": "anna@example.com",
                "seat": "A15",
            }

            return httpx.Response(
                201,
                json={"ticket_id": str(TICKET_ID)},
            )

        transport = httpx.MockTransport(handler)

        async with httpx.AsyncClient(transport=transport) as client:
            provider = EventsProviderClient(
                base_url="http://provider.test",
                api_key="test-key",
                client=client,
            )

            result = await provider.register(
                event_id=EVENT_ID,
                first_name="Анна",
                last_name="Иванова",
                email="anna@example.com",
                seat="A15",
            )

            assert result == TICKET_ID

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("payload", "error_type"),
    [
        ({}, TypeError),
        ({"ticket_id": 123}, TypeError),
        ({"ticket_id": "invalid-uuid"}, ValueError),
    ],
)
def test_register_rejects_invalid_response(
    payload: object,
    error_type: type[Exception],
) -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(201, json=payload)

        transport = httpx.MockTransport(handler)

        async with httpx.AsyncClient(transport=transport) as client:
            provider = EventsProviderClient(
                base_url="http://provider.test",
                api_key="test-key",
                client=client,
            )

            with pytest.raises(error_type):
                await provider.register(
                    event_id=EVENT_ID,
                    first_name="Анна",
                    last_name="Иванова",
                    email="anna@example.com",
                    seat="A15",
                )

    asyncio.run(scenario())


def test_register_propagates_provider_error() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json=["This ticket is not available (already sold)."],
            )

        transport = httpx.MockTransport(handler)

        async with httpx.AsyncClient(transport=transport) as client:
            provider = EventsProviderClient(
                base_url="http://provider.test",
                api_key="test-key",
                client=client,
            )

            with pytest.raises(httpx.HTTPStatusError) as error:
                await provider.register(
                    event_id=EVENT_ID,
                    first_name="Анна",
                    last_name="Иванова",
                    email="anna@example.com",
                    seat="A15",
                )

            assert error.value.response.status_code == 400

    asyncio.run(scenario())


def test_unregister_sends_delete_with_json() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "DELETE"
            assert request.url.path == f"/api/events/{EVENT_ID}/unregister/"
            assert request.headers["x-api-key"] == "test-key"

            assert json.loads(request.content) == {
                "ticket_id": str(TICKET_ID),
            }

            return httpx.Response(
                200,
                json={"success": True},
            )

        transport = httpx.MockTransport(handler)

        async with httpx.AsyncClient(transport=transport) as client:
            provider = EventsProviderClient(
                base_url="http://provider.test",
                api_key="test-key",
                client=client,
            )

            result = await provider.unregister(
                event_id=EVENT_ID,
                ticket_id=TICKET_ID,
            )

            assert result is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"success": False},
        {"success": "true"},
    ],
)
def test_unregister_rejects_invalid_response(
    payload: object,
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
                match="Invalid Events Provider cancellation response",
            ):
                await provider.unregister(
                    event_id=EVENT_ID,
                    ticket_id=TICKET_ID,
                )

    asyncio.run(scenario())


def test_unregister_propagates_provider_error() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                json={"detail": "Ticket not found"},
            )

        transport = httpx.MockTransport(handler)

        async with httpx.AsyncClient(transport=transport) as client:
            provider = EventsProviderClient(
                base_url="http://provider.test",
                api_key="test-key",
                client=client,
            )

            with pytest.raises(httpx.HTTPStatusError) as error:
                await provider.unregister(
                    event_id=EVENT_ID,
                    ticket_id=TICKET_ID,
                )

            assert error.value.response.status_code == 404

    asyncio.run(scenario())
