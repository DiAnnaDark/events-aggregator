from enum import StrEnum
from typing import TypeVar

from sqlalchemy import Text, TypeDecorator
from sqlalchemy.engine import Dialect

from app.core.enums import EventStatus, OutboxEventType, parse_event_status

EnumT = TypeVar("EnumT", bound=StrEnum)


class EnumText(TypeDecorator[EnumT]):
    impl = Text
    cache_ok = True

    def __init__(self, enum_class: type[EnumT]) -> None:
        super().__init__()
        self.enum_class = enum_class

    def process_bind_param(
        self,
        value: str | None,
        dialect: Dialect,
    ) -> str | None:
        if isinstance(value, StrEnum):
            return value.value

        return value

    def process_result_value(
        self,
        value: str | None,
        dialect: Dialect,
    ) -> EnumT | None:
        if value is None:
            return None

        return self.enum_class(value)


class OutboxEventTypeText(TypeDecorator[OutboxEventType | str]):
    impl = Text
    cache_ok = True

    def process_bind_param(
        self,
        value: str | None,
        dialect: Dialect,
    ) -> str | None:
        if isinstance(value, OutboxEventType):
            return value.value

        return value

    def process_result_value(
        self,
        value: str | None,
        dialect: Dialect,
    ) -> OutboxEventType | str | None:
        if value is None:
            return None

        try:
            return OutboxEventType(value)
        except ValueError:
            return value


class EventStatusText(TypeDecorator[EventStatus | str]):
    impl = Text
    cache_ok = True

    def process_bind_param(
        self,
        value: str | None,
        dialect: Dialect,
    ) -> str | None:
        if isinstance(value, EventStatus):
            return value.value

        return value

    def process_result_value(
        self,
        value: str | None,
        dialect: Dialect,
    ) -> EventStatus | str | None:
        if value is None:
            return None

        return parse_event_status(value)
