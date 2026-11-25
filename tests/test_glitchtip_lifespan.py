import asyncio
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI

from app.core.config import Settings


def test_lifespan_initializes_monitoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "SENTRY_DSN",
        "http://test-public-key@glitchtip.test/71",
    )

    settings = Settings(
        _env_file=None,
        postgres_database_name="test",
        postgres_username="test",
        postgres_password="test-only",
        postgres_host="localhost",
        postgres_port=55433,
        events_provider_api_key=None,
        capashino_api_key=None,
    )

    main_module = import_module("app.main")

    engine = SimpleNamespace(dispose=AsyncMock())
    worker = SimpleNamespace(
        start=Mock(),
        stop=AsyncMock(),
    )

    monitoring = Mock()

    monkeypatch.setattr(
        main_module,
        "init_monitoring",
        monitoring,
        raising=False,
    )
    monkeypatch.setattr(main_module, "Settings", Mock(return_value=settings))
    monkeypatch.setattr(main_module, "create_db_engine", Mock(return_value=engine))
    monkeypatch.setattr(
        main_module,
        "create_session_factory",
        Mock(return_value=object()),
    )
    monkeypatch.setattr(
        main_module,
        "PostgresOutboxStore",
        Mock(return_value=object()),
    )
    monkeypatch.setattr(
        main_module,
        "OutboxWorker",
        Mock(return_value=worker),
    )

    async def scenario() -> None:
        application = FastAPI()

        async with main_module.sync_lifespan(application):
            monitoring.assert_called_once_with(settings.sentry_dsn)

    asyncio.run(scenario())
