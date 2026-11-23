from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.main import create_app


def test_missing_provider_key_keeps_health_available() -> None:
    settings = MagicMock()
    settings.capashino_api_key = None
    settings.events_provider_api_key = None
    settings.outbox_poll_interval_seconds = 30.0
    settings.outbox_batch_size = 100

    engine = MagicMock()
    engine.dispose = AsyncMock()

    outbox_worker = MagicMock()
    outbox_worker.stop = AsyncMock()

    with (
        patch("app.main.OutboxWorker", return_value=outbox_worker),
        patch("app.main.Settings", return_value=settings),
        patch("app.main.create_db_engine", return_value=engine),
        patch("app.main.create_session_factory"),
        patch("app.main.DailySyncScheduler") as scheduler_class,
        patch("app.main.TicketRecoveryScheduler") as recovery_class,
        TestClient(create_app()) as client,
    ):
        health = client.get("/api/health")
        trigger = client.post("/api/sync/trigger")

        outbox_worker.start.assert_called_once_with()

    outbox_worker.stop.assert_awaited_once_with()

    assert health.status_code == 200
    assert trigger.status_code == 503

    scheduler_class.assert_not_called()
    recovery_class.assert_not_called()
    engine.dispose.assert_awaited_once()


def test_lifespan_creates_and_closes_resources() -> None:
    settings = MagicMock()
    settings.capashino_api_key = None
    settings.events_provider_api_key = SecretStr("test-key")
    settings.events_provider_base_url = "http://provider.invalid"
    settings.outbox_poll_interval_seconds = 30.0
    settings.outbox_batch_size = 100

    engine = MagicMock()
    engine.dispose = AsyncMock()

    http_context = MagicMock()
    http_context.__aenter__ = AsyncMock(return_value=MagicMock())
    http_context.__aexit__ = AsyncMock(return_value=False)

    shutdown_order = []

    async def stop_recovery() -> None:
        shutdown_order.append("recovery")

    async def stop_scheduler() -> None:
        shutdown_order.append("scheduler")

    async def stop_runner() -> None:
        shutdown_order.append("runner")

    runner = MagicMock()
    runner.trigger.return_value = True
    runner.stop = AsyncMock(side_effect=stop_runner)

    scheduler = MagicMock()
    scheduler.stop = AsyncMock(side_effect=stop_scheduler)

    recovery_scheduler = MagicMock()
    recovery_scheduler.stop = AsyncMock(side_effect=stop_recovery)

    outbox_worker = MagicMock()
    outbox_worker.stop = AsyncMock()

    with (
        patch("app.main.OutboxWorker", return_value=outbox_worker),
        patch("app.main.Settings", return_value=settings),
        patch("app.main.create_db_engine", return_value=engine),
        patch("app.main.create_session_factory") as session_factory_class,
        patch("app.main.httpx.AsyncClient", return_value=http_context),
        patch("app.main.EventsProviderClient"),
        patch("app.main.ProviderEventsSource"),
        patch("app.main.PostgresSyncStore") as store_class,
        patch("app.main.PostgresTicketStore") as ticket_store_class,
        patch("app.main.SyncService"),
        patch("app.main.SyncRunner", return_value=runner),
        patch(
            "app.main.DailySyncScheduler",
            return_value=scheduler,
        ) as scheduler_class,
        patch(
            "app.main.TicketRecoveryScheduler",
            return_value=recovery_scheduler,
        ) as recovery_class,
    ):
        application = create_app()

        with TestClient(application) as client:
            response = client.post("/api/sync/trigger")

            assert response.status_code == 200
            assert response.json() == {"status": "started"}

            assert hasattr(application.state, "sync_runner")
            assert hasattr(application.state, "seats_service")
            assert hasattr(application.state, "registration_provider")
            assert hasattr(application.state, "db_session_factory")

            outbox_worker.start.assert_called_once_with()

        outbox_worker.stop.assert_awaited_once_with()

        assert not hasattr(application.state, "sync_runner")
        assert not hasattr(application.state, "seats_service")
        assert not hasattr(application.state, "registration_provider")
        assert not hasattr(application.state, "db_session_factory")

    scheduler_class.assert_called_once_with(
        runner=runner,
        history=store_class.return_value,
    )

    ticket_store_class.assert_called_once_with(
        session_factory_class.return_value,
    )

    recovery_class.assert_called_once_with(
        store=ticket_store_class.return_value,
    )

    scheduler.start.assert_called_once()
    scheduler.stop.assert_awaited_once()

    recovery_scheduler.start.assert_called_once()
    recovery_scheduler.stop.assert_awaited_once()

    runner.trigger.assert_called_once()
    runner.stop.assert_awaited_once()

    assert shutdown_order == [
        "recovery",
        "scheduler",
        "runner",
    ]

    engine.dispose.assert_awaited_once()


def test_lifespan_configures_capashino_and_closes_client() -> None:
    settings = MagicMock()
    settings.events_provider_api_key = None
    settings.capashino_api_key = SecretStr("test-capashino-key")
    settings.capashino_base_url = "http://capashino.test"
    settings.outbox_poll_interval_seconds = 30.0
    settings.outbox_batch_size = 100

    engine = MagicMock()
    http_client = MagicMock()
    outbox_worker = MagicMock()

    shutdown_order = []

    async def stop_worker() -> None:
        shutdown_order.append("worker")

    async def close_http() -> None:
        shutdown_order.append("http")

    async def dispose_engine() -> None:
        shutdown_order.append("database")

    outbox_worker.stop = AsyncMock(side_effect=stop_worker)
    http_client.aclose = AsyncMock(side_effect=close_http)
    engine.dispose = AsyncMock(side_effect=dispose_engine)

    with (
        patch("app.main.Settings", return_value=settings),
        patch("app.main.create_db_engine", return_value=engine),
        patch("app.main.create_session_factory"),
        patch(
            "app.main.httpx.AsyncClient",
            return_value=http_client,
        ) as http_client_class,
        patch("app.main.CapashinoClient") as capashino_class,
        patch(
            "app.main.OutboxWorker",
            return_value=outbox_worker,
        ) as worker_class,
        TestClient(create_app()) as client,
    ):
        assert client.get("/api/health").status_code == 200

        http_client_class.assert_called_once_with()

        capashino_class.assert_called_once_with(
            base_url="http://capashino.test",
            api_key="test-capashino-key",
            client=http_client,
        )

        assert worker_class.call_args.kwargs["sender"] is capashino_class.return_value

        outbox_worker.start.assert_called_once_with()

    outbox_worker.stop.assert_awaited_once_with()
    http_client.aclose.assert_awaited_once_with()
    engine.dispose.assert_awaited_once_with()

    assert shutdown_order == [
        "worker",
        "http",
        "database",
    ]
