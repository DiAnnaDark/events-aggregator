import pytest

from app.core.config import Settings


def test_settings_reads_sentry_dsn(
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
        postgres_password="test",
        postgres_host="localhost",
        postgres_port=5432,
    )

    assert settings.sentry_dsn is not None
    assert settings.sentry_dsn.get_secret_value() == (
        "http://test-public-key@glitchtip.test/71"
    )
