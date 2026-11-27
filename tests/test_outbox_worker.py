import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, call
from uuid import uuid4

from app.application.outbox.worker import OutboxWorker


def test_worker_run_once() -> None:
    async def scenario() -> None:
        store = SimpleNamespace(
            get_pending=AsyncMock(return_value=[object(), object()])
        )

        worker = OutboxWorker(store=store, batch_size=2)

        result = await worker.run_once()

        assert result == 2
        store.get_pending.assert_awaited_once_with(limit=2)

    asyncio.run(scenario())


def test_worker_start_stop_and_polling() -> None:
    async def scenario() -> None:
        polled_twice = asyncio.Event()
        calls = 0

        async def get_pending(*, limit: int) -> list[object]:
            nonlocal calls
            calls += 1

            if calls >= 2:
                polled_twice.set()

            return []

        store = SimpleNamespace(get_pending=get_pending)
        worker = OutboxWorker(
            store=store,
            poll_interval_seconds=0.01,
        )

        worker.start()

        try:
            await asyncio.wait_for(polled_twice.wait(), timeout=2)
        finally:
            await worker.stop()

        assert calls >= 2

    asyncio.run(scenario())


def test_worker_retries_after_error() -> None:
    async def scenario() -> None:
        retried = asyncio.Event()
        calls = 0

        async def get_pending(*, limit: int) -> list[object]:
            nonlocal calls
            calls += 1

            if calls == 1:
                raise RuntimeError("Temporary database error")

            retried.set()
            return []

        store = SimpleNamespace(get_pending=get_pending)
        worker = OutboxWorker(
            store=store,
            poll_interval_seconds=0.01,
        )

        worker.start()

        try:
            await asyncio.wait_for(retried.wait(), timeout=2)
        finally:
            await worker.stop()

        assert calls >= 2

    asyncio.run(scenario())


def test_worker_sends_notification_and_marks_sent() -> None:
    async def scenario() -> None:
        event_id = uuid4()
        ticket_id = str(uuid4())

        event = SimpleNamespace(
            id=event_id,
            event_type="ticket_purchased",
            payload={
                "message": "Билет успешно куплен",
                "ticket_id": ticket_id,
            },
        )

        store = SimpleNamespace(
            get_pending=AsyncMock(return_value=[event]),
            mark_sent=AsyncMock(),
        )
        sender = SimpleNamespace(send=AsyncMock())

        worker = OutboxWorker(
            store=store,
            sender=sender,
            batch_size=1,
        )

        result = await worker.run_once()

        assert result == 1

        sender.send.assert_awaited_once_with(
            message="Билет успешно куплен",
            reference_id=ticket_id,
            idempotency_key=str(event_id),
        )

        store.mark_sent.assert_awaited_once_with(event_id)

    asyncio.run(scenario())


def test_worker_retries_notification_with_same_key() -> None:
    async def scenario() -> None:
        event_id = uuid4()
        ticket_id = str(uuid4())

        event = SimpleNamespace(
            id=event_id,
            event_type="ticket_purchased",
            payload={
                "message": "Билет успешно куплен",
                "ticket_id": ticket_id,
            },
        )

        store = SimpleNamespace(
            get_pending=AsyncMock(return_value=[event]),
            mark_sent=AsyncMock(),
        )

        sender = SimpleNamespace(
            send=AsyncMock(
                side_effect=[
                    RuntimeError("Capashino unavailable"),
                    None,
                ]
            )
        )

        worker = OutboxWorker(store=store, sender=sender)

        await worker.run_once()

        store.mark_sent.assert_not_awaited()

        await worker.run_once()

        expected_call = call(
            message="Билет успешно куплен",
            reference_id=ticket_id,
            idempotency_key=str(event_id),
        )

        assert sender.send.await_args_list == [
            expected_call,
            expected_call,
        ]

        store.mark_sent.assert_awaited_once_with(event_id)

    asyncio.run(scenario())


def test_worker_skips_invalid_payload() -> None:
    async def scenario() -> None:
        event = SimpleNamespace(
            id=uuid4(),
            event_type="ticket_purchased",
            payload={
                "message": "Билет успешно куплен",
            },
        )

        store = SimpleNamespace(
            get_pending=AsyncMock(return_value=[event]),
            mark_sent=AsyncMock(),
        )
        sender = SimpleNamespace(send=AsyncMock())

        worker = OutboxWorker(store=store, sender=sender)

        result = await worker.run_once()

        assert result == 1
        sender.send.assert_not_awaited()
        store.mark_sent.assert_not_awaited()

    asyncio.run(scenario())


def test_worker_retries_when_mark_sent_fails() -> None:
    async def scenario() -> None:
        event_id = uuid4()
        ticket_id = str(uuid4())

        event = SimpleNamespace(
            id=event_id,
            event_type="ticket_purchased",
            payload={
                "message": "Билет успешно куплен",
                "ticket_id": ticket_id,
            },
        )

        store = SimpleNamespace(
            get_pending=AsyncMock(return_value=[event]),
            mark_sent=AsyncMock(
                side_effect=[
                    RuntimeError("Database unavailable"),
                    None,
                ]
            ),
        )

        sender = SimpleNamespace(send=AsyncMock())

        worker = OutboxWorker(store=store, sender=sender)

        await worker.run_once()

        assert store.mark_sent.await_count == 1

        await worker.run_once()

        expected_call = call(
            message="Билет успешно куплен",
            reference_id=ticket_id,
            idempotency_key=str(event_id),
        )

        assert sender.send.await_args_list == [
            expected_call,
            expected_call,
        ]

        assert store.mark_sent.await_args_list == [
            call(event_id),
            call(event_id),
        ]

    asyncio.run(scenario())


def test_worker_skips_unknown_event_and_processes_next() -> None:
    async def scenario() -> None:
        unknown_event = SimpleNamespace(
            id=uuid4(),
            event_type="unknown_event",
            payload={},
        )

        valid_event = SimpleNamespace(
            id=uuid4(),
            event_type="ticket_purchased",
            payload={
                "message": "Билет успешно куплен",
                "ticket_id": str(uuid4()),
            },
        )

        store = SimpleNamespace(
            get_pending=AsyncMock(return_value=[unknown_event, valid_event]),
            mark_sent=AsyncMock(),
        )

        sender = SimpleNamespace(send=AsyncMock())
        worker = OutboxWorker(store=store, sender=sender)

        result = await worker.run_once()

        assert result == 2

        sender.send.assert_awaited_once_with(
            message=valid_event.payload["message"],
            reference_id=valid_event.payload["ticket_id"],
            idempotency_key=str(valid_event.id),
        )

        store.mark_sent.assert_awaited_once_with(valid_event.id)

    asyncio.run(scenario())
