import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.events import router as events_router
from app.api.health import router as health_router
from app.api.sync import router as sync_router
from app.application.sync.runner import SyncRunner
from app.application.sync.service import SyncService
from app.core.config import Settings
from app.infrastructure.db.session import (
    create_db_engine,
    create_session_factory,
)
from app.infrastructure.db.sync_store import PostgresSyncStore
from app.infrastructure.provider.client import EventsProviderClient
from app.infrastructure.provider.source import ProviderEventsSource

logger = logging.getLogger(__name__)


@asynccontextmanager
async def sync_lifespan(
    application: FastAPI,
) -> AsyncIterator[None]:
    settings = Settings()
    engine = create_db_engine(settings)

    try:
        session_factory = create_session_factory(engine)
        application.state.db_session_factory = session_factory

        secret = settings.events_provider_api_key
        api_key = secret.get_secret_value() if secret is not None else ""

        if not api_key:
            logger.warning(
                "Events Provider API key is not configured; synchronization is unavailable"
            )
            yield
            return

        async with httpx.AsyncClient() as http_client:
            provider = EventsProviderClient(
                base_url=settings.events_provider_base_url,
                api_key=api_key,
                client=http_client,
            )

            source = ProviderEventsSource(provider)
            store = PostgresSyncStore(session_factory)

            service = SyncService(
                source=source,
                store=store,
            )

            runner = SyncRunner(service)
            application.state.sync_runner = runner

            try:
                yield
            finally:
                await runner.stop()
                del application.state.sync_runner

    finally:
        if hasattr(application.state, "db_session_factory"):
            del application.state.db_session_factory

        await engine.dispose()


def create_app(
    *,
    enable_sync: bool = True,
) -> FastAPI:
    application = FastAPI(
        title="Events Aggregator",
        lifespan=sync_lifespan if enable_sync else None,
    )

    application.include_router(health_router)
    application.include_router(sync_router)
    application.include_router(events_router)

    return application


app = create_app()
