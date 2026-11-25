import sentry_sdk
from pydantic import SecretStr


def init_monitoring(dsn: SecretStr | None) -> None:
    if dsn is None:
        return

    dsn_value = dsn.get_secret_value().strip()

    if not dsn_value:
        return

    sentry_sdk.init(dsn=dsn_value)
