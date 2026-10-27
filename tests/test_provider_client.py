import asyncio
from datetime import date
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.infrastructure.provider.client import EventsProviderClient


def test_events_requests_first_page() -> None:
    http_client = Mock(spec=httpx.AsyncClient)

    response = Mock(spec=httpx.Response)
    response.json.return_value = {
        "next": None,
        "previous": None,
        "results": [],
    }

    http_client.get = AsyncMock(return_value=response)

    provider = EventsProviderClient(
        base_url="http://provider.test/",
        api_key="test-key",
        client=http_client,
    )

    result = asyncio.run(provider.events(changed_at=date(2000, 1, 1)))

    assert result["results"] == []

    http_client.get.assert_awaited_once_with(
        "http://provider.test/api/events/",
        params={"changed_at": "2000-01-01"},
        headers={"x-api-key": "test-key"},
        timeout=10.0,
    )

    response.raise_for_status.assert_called_once_with()


def test_events_rejects_http_error() -> None:
    http_client = Mock(spec=httpx.AsyncClient)

    request = httpx.Request(
        "GET",
        "http://provider.test/api/events/",
    )
    response = httpx.Response(503, request=request)

    http_client.get = AsyncMock(return_value=response)

    provider = EventsProviderClient(
        base_url="http://provider.test",
        api_key="test-key",
        client=http_client,
    )

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(provider.events(changed_at=date(2000, 1, 1)))
