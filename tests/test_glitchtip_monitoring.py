from unittest.mock import patch

from pydantic import SecretStr

from app.core.monitoring import init_monitoring


def test_init_monitoring_with_dsn() -> None:
    dsn = SecretStr("http://test-public-key@glitchtip.test/71")

    with patch("app.core.monitoring.sentry_sdk.init") as mock_init:
        init_monitoring(dsn)

    mock_init.assert_called_once_with(
        dsn=dsn.get_secret_value(),
    )


def test_init_monitoring_without_dsn() -> None:
    with patch("app.core.monitoring.sentry_sdk.init") as mock_init:
        init_monitoring(None)

    mock_init.assert_not_called()


def test_init_monitoring_with_empty_dsn() -> None:
    with patch("app.core.monitoring.sentry_sdk.init") as mock_init:
        init_monitoring(SecretStr(""))

    mock_init.assert_not_called()
