from pydantic import SecretStr
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
