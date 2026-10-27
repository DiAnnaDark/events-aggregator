
# Events Aggregator

Сервис для работы с мероприятиями через Events Provider API.

Получает и сохраняет мероприятия в PostgreSQL, автоматически обновляет данные раз в сутки. Позволяет просматривать события и свободные места, регистрироваться на мероприятия и отменять регистрацию.

Используются Python 3.12, FastAPI, SQLAlchemy, PostgreSQL и Alembic.

## Запуск

Для локального запуска нужны Python 3.12, uv и Docker.

Создать `.env` на основе `.env.example` и указать настройки базы данных и ключ Events Provider API.

```bash
cp .env.example .env
uv sync
docker compose up -d db
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Документация API доступна по адресу:

http://127.0.0.1:8000/docs

## Проверка

```bash
uv run ruff check .
uv run ruff format --check .
uv run python -m pytest -q
```

Интеграционные тесты используют отдельную базу PostgreSQL. В GitHub Actions она запускается автоматически.

## Развёртывание

При отправке изменений в `main` GitHub Actions проверяет код, запускает тесты, собирает Docker-образ и отправляет его на платформу LMS.

## Особенности

Для регистрации и отмены используются транзакции PostgreSQL с защитой от параллельных операций.

Незавершённые подготовленные операции очищаются автоматически. Операции с неизвестным результатом требуют отдельной проверки.
