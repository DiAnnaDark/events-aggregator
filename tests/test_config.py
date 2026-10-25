import pytest

from app.core.config import Settings


def test_settings_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DATABASE_NAME", "test_events")
    monkeypatch.setenv("POSTGRES_USERNAME", "test_user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test_password")
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_PORT", "6543")

    settings = Settings(_env_file=None)

    assert settings.postgres_database_name == "test_events"
    assert settings.postgres_username == "test_user"
    assert settings.postgres_password.get_secret_value() == "test_password"
    assert settings.postgres_host == "localhost"
    assert settings.postgres_port == 6543
