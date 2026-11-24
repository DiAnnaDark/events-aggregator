from pydantic import PositiveInt, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    postgres_database_name: str
    postgres_username: str
    postgres_password: SecretStr
    postgres_host: str
    postgres_port: int

    events_provider_base_url: str = "http://events-provider.dev-2.python-labs.ru"
    events_provider_api_key: SecretStr | None = None

    ticket_idempotency_ttl_hours: PositiveInt = 24

    outbox_poll_interval_seconds: float = 30.0
    outbox_batch_size: int = 100

    capashino_base_url: str = (
        "http://student-system-capashino-web.student-system-capashino.svc:8000"
    )
    capashino_api_key: SecretStr | None = None
