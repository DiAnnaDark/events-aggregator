FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /uvx /bin/

WORKDIR /app

ENV UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=never
ENV PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./

RUN uv sync --locked --no-dev --no-install-project --no-cache

COPY app/ ./app/
COPY migrations/ ./migrations/
COPY alembic.ini ./
COPY run.sh ./

USER 10001:10001

EXPOSE 8000

CMD ["sh", "./run.sh"]