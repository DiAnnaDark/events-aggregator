from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.main import create_app


def test_trigger_starts_synchronization() -> None:
    application = create_app(enable_sync=False)

    runner = Mock()
    runner.trigger.return_value = True

    application.state.sync_runner = runner

    with TestClient(application) as client:
        response = client.post("/api/sync/trigger")

    assert response.status_code == 200
    assert response.json() == {"status": "started"}

    runner.trigger.assert_called_once_with()


def test_trigger_rejects_parallel_synchronization() -> None:
    application = create_app(enable_sync=False)

    runner = Mock()
    runner.trigger.return_value = False

    application.state.sync_runner = runner

    with TestClient(application) as client:
        response = client.post("/api/sync/trigger")

    assert response.status_code == 409

    runner.trigger.assert_called_once_with()


def test_trigger_without_runner_returns_503() -> None:
    application = create_app(enable_sync=False)

    with TestClient(application) as client:
        response = client.post("/api/sync/trigger")

    assert response.status_code == 503
