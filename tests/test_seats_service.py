import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.application.events.seats_service import SeatsService

EVENT_ID = UUID("550e8400-e29b-41d4-a716-446655440000")
OTHER_EVENT_ID = UUID("650e8400-e29b-41d4-a716-446655440001")


def test_seats_cache_expires_after_30_seconds() -> None:
    async def scenario() -> None:
        current_time = 100.0

        provider = SimpleNamespace(
            seats=AsyncMock(
                side_effect=[
                    ["A1", "A2"],
                    ["B1"],
                ]
            )
        )

        service = SeatsService(
            provider,
            clock=lambda: current_time,
        )

        first = await service.get_available_seats(EVENT_ID)
        assert first == ["A1", "A2"]

        first.append("B9")

        cached = await service.get_available_seats(EVENT_ID)
        assert cached == ["A1", "A2"]

        current_time = 129.9

        cached = await service.get_available_seats(EVENT_ID)
        assert cached == ["A1", "A2"]

        current_time = 130.0

        refreshed = await service.get_available_seats(EVENT_ID)
        assert refreshed == ["B1"]

        assert provider.seats.await_count == 2

    asyncio.run(scenario())


def test_cache_is_independent_for_each_event() -> None:
    async def scenario() -> None:
        provider = SimpleNamespace(
            seats=AsyncMock(
                side_effect=[
                    ["A1"],
                    ["B2"],
                ]
            )
        )

        service = SeatsService(provider)

        first = await service.get_available_seats(EVENT_ID)
        second = await service.get_available_seats(OTHER_EVENT_ID)
        repeated = await service.get_available_seats(EVENT_ID)

        assert first == ["A1"]
        assert second == ["B2"]
        assert repeated == ["A1"]

        assert provider.seats.await_count == 2

    asyncio.run(scenario())


def test_failed_request_is_not_cached() -> None:
    async def scenario() -> None:
        provider = SimpleNamespace(
            seats=AsyncMock(
                side_effect=[
                    RuntimeError("Temporary failure"),
                    ["A1"],
                ]
            )
        )

        service = SeatsService(provider)

        with pytest.raises(RuntimeError, match="Temporary failure"):
            await service.get_available_seats(EVENT_ID)

        seats = await service.get_available_seats(EVENT_ID)

        assert seats == ["A1"]
        assert provider.seats.await_count == 2

    asyncio.run(scenario())


def test_cache_can_be_invalidated() -> None:
    async def scenario() -> None:
        provider = SimpleNamespace(
            seats=AsyncMock(
                side_effect=[
                    ["A1"],
                    ["A2"],
                ]
            )
        )

        service = SeatsService(provider)

        first = await service.get_available_seats(EVENT_ID)

        await service.invalidate(EVENT_ID)

        second = await service.get_available_seats(EVENT_ID)

        assert first == ["A1"]
        assert second == ["A2"]

        assert provider.seats.await_count == 2

    asyncio.run(scenario())


def test_parallel_requests_share_one_provider_request() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        finish = asyncio.Event()

        async def fetch_seats(
            *,
            event_id: UUID,
        ) -> list[str]:
            assert event_id == EVENT_ID

            started.set()
            await finish.wait()

            return ["A1", "A2"]

        provider = SimpleNamespace(seats=AsyncMock(side_effect=fetch_seats))

        service = SeatsService(provider)

        first_task = asyncio.create_task(service.get_available_seats(EVENT_ID))

        await asyncio.wait_for(started.wait(), timeout=3)

        second_task = asyncio.create_task(service.get_available_seats(EVENT_ID))

        await asyncio.sleep(0)

        assert provider.seats.await_count == 1

        finish.set()

        first, second = await asyncio.gather(
            first_task,
            second_task,
        )

        assert first == ["A1", "A2"]
        assert second == ["A1", "A2"]

        provider.seats.assert_awaited_once_with(
            event_id=EVENT_ID,
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("ttl", [0, -1])
def test_cache_rejects_invalid_ttl(ttl: float) -> None:
    provider = SimpleNamespace(seats=AsyncMock())

    with pytest.raises(ValueError, match="TTL"):
        SeatsService(
            provider,
            ttl_seconds=ttl,
        )
