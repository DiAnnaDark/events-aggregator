import asyncio
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI

from app.core.config import Settings


def test_lifespan_reads_ttl_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_DATABASE_NAME", "test")
    monkeypatch.setenv("POSTGRES_USERNAME", "test")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-only")
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_PORT", "55433")
    monkeypatch.setenv("TICKET_IDEMPOTENCY_TTL_HOURS", "48")

    settings = Settings(
        _env_file=None,
        events_provider_api_key=None,
        capashino_api_key=None,
    )

    main_module = import_module("app.main")

    engine = SimpleNamespace(dispose=AsyncMock())
    session_factory = object()

    worker = SimpleNamespace(
        start=Mock(),
        stop=AsyncMock(),
    )

    monkeypatch.setattr(main_module, "Settings", Mock(return_value=settings))
    monkeypatch.setattr(main_module, "create_db_engine", Mock(return_value=engine))
    monkeypatch.setattr(
        main_module,
        "create_session_factory",
        Mock(return_value=session_factory),
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
            assert settings.ticket_idempotency_ttl_hours == 48
            assert application.state.ticket_idempotency_ttl_hours == 48
            assert application.state.db_session_factory is session_factory

        worker.start.assert_called_once_with()
        worker.stop.assert_awaited_once_with()
        engine.dispose.assert_awaited_once_with()

    asyncio.run(scenario())
