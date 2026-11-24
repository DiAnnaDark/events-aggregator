import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_idempotency_ttl_reads_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_DATABASE_NAME", "test")
    monkeypatch.setenv("POSTGRES_USERNAME", "test")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-only")
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_PORT", "55433")

    monkeypatch.setenv("TICKET_IDEMPOTENCY_TTL_HOURS", "48")

    settings = Settings(_env_file=None)

    assert settings.ticket_idempotency_ttl_hours == 48


@pytest.mark.parametrize("value", ["0", "-1", "invalid"])
def test_idempotency_ttl_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("POSTGRES_DATABASE_NAME", "test")
    monkeypatch.setenv("POSTGRES_USERNAME", "test")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-only")
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_PORT", "55433")

    monkeypatch.setenv("TICKET_IDEMPOTENCY_TTL_HOURS", value)

    with pytest.raises(
        ValidationError,
        match="ticket_idempotency_ttl_hours",
    ):
        Settings(_env_file=None)
