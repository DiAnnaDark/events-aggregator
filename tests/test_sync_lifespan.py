from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.main import create_app


def test_missing_provider_key_keeps_health_available() -> None:
    settings = MagicMock()
    settings.events_provider_api_key = None

    engine = MagicMock()
    engine.dispose = AsyncMock()

    with (
        patch("app.main.Settings", return_value=settings),
        patch("app.main.create_db_engine", return_value=engine),
        patch("app.main.create_session_factory"),
        TestClient(create_app()) as client,
    ):
        health = client.get("/api/health")
        trigger = client.post("/api/sync/trigger")

    assert health.status_code == 200
    assert trigger.status_code == 503

    engine.dispose.assert_awaited_once()


def test_lifespan_creates_and_closes_resources() -> None:
    settings = MagicMock()
    settings.events_provider_api_key = SecretStr("test-key")
    settings.events_provider_base_url = "http://provider.invalid"

    engine = MagicMock()
    engine.dispose = AsyncMock()

    http_context = MagicMock()
    http_context.__aenter__ = AsyncMock(return_value=MagicMock())
    http_context.__aexit__ = AsyncMock(return_value=False)

    runner = MagicMock()
    runner.trigger.return_value = True
    runner.stop = AsyncMock()

    with (
        patch("app.main.Settings", return_value=settings),
        patch("app.main.create_db_engine", return_value=engine),
        patch("app.main.create_session_factory"),
        patch("app.main.httpx.AsyncClient", return_value=http_context),
        patch("app.main.EventsProviderClient"),
        patch("app.main.ProviderEventsSource"),
        patch("app.main.PostgresSyncStore"),
        patch("app.main.SyncService"),
        patch("app.main.SyncRunner", return_value=runner),
    ):
        application = create_app()

        with TestClient(application) as client:
            response = client.post("/api/sync/trigger")

            assert response.status_code == 200
            assert response.json() == {"status": "started"}

        assert not hasattr(application.state, "sync_runner")

    runner.trigger.assert_called_once()
    runner.stop.assert_awaited_once()
    engine.dispose.assert_awaited_once()
