import asyncio

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.infrastructure.db.session import create_db_engine, create_session_factory


def test_engine_and_session_factory() -> None:
    settings = Settings(
        _env_file=None,
        postgres_database_name="test_events",
        postgres_username="test_user",
        postgres_password=SecretStr("test@password"),
        postgres_host="127.0.0.1",
        postgres_port=6543,
    )

    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    session = session_factory()

    assert engine.url.drivername == "postgresql+asyncpg"
    assert engine.url.database == "test_events"
    assert engine.url.username == "test_user"
    assert engine.url.password == "test@password"
    assert engine.url.port == 6543

    assert isinstance(session, AsyncSession)
    assert session.sync_session.expire_on_commit is False

    asyncio.run(session.close())
    asyncio.run(engine.dispose())
