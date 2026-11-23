import asyncio
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.infrastructure.notifications.client import CapashinoClient


def test_send_notification_success() -> None:
    http_client = Mock(spec=httpx.AsyncClient)
    request = httpx.Request(
        "POST",
        "http://capashino.test/api/notifications",
    )
    response = httpx.Response(201, request=request)

    http_client.post = AsyncMock(return_value=response)

    capashino = CapashinoClient(
        base_url="http://capashino.test/",
        api_key="test-key",
        client=http_client,
    )

    asyncio.run(
        capashino.send(
            message="Билет успешно куплен",
            reference_id="ticket-123",
            idempotency_key="outbox-456",
        )
    )

    http_client.post.assert_awaited_once_with(
        "http://capashino.test/api/notifications",
        json={
            "message": "Билет успешно куплен",
            "reference_id": "ticket-123",
            "idempotency_key": "outbox-456",
        },
        headers={"X-API-Key": "test-key"},
        timeout=10.0,
    )


def test_send_notification_server_error() -> None:
    request = httpx.Request(
        "POST",
        "http://capashino.test/api/notifications",
    )
    response = httpx.Response(500, request=request)

    http_client = Mock(spec=httpx.AsyncClient)
    http_client.post = AsyncMock(return_value=response)

    capashino = CapashinoClient(
        base_url="http://capashino.test",
        api_key="test-key",
        client=http_client,
    )

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(
            capashino.send(
                message="Билет успешно куплен",
                reference_id="ticket-123",
                idempotency_key="outbox-456",
            )
        )


def test_send_notification_rejects_unexpected_status() -> None:
    http_client = Mock(spec=httpx.AsyncClient)
    request = httpx.Request(
        "POST",
        "http://capashino.test/api/notifications",
    )
    response = httpx.Response(200, request=request)

    http_client.post = AsyncMock(return_value=response)

    capashino = CapashinoClient(
        base_url="http://capashino.test",
        api_key="test-key",
        client=http_client,
    )

    with pytest.raises(
        ValueError,
        match="Unexpected Capashino response status",
    ):
        asyncio.run(
            capashino.send(
                message="Билет успешно куплен",
                reference_id="ticket-123",
                idempotency_key="outbox-456",
            )
        )
