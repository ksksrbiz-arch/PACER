FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_VERSION=1.8.3

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential libpq-dev curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install "poetry==${POETRY_VERSION}"

WORKDIR /app

# Copy lock files + README (pyproject.toml declares readme = "README.md")
COPY pyproject.toml README.md ./
COPY poetry.lock* ./
RUN poetry install --only main --no-root --no-interaction

COPY . .
RUN poetry install --only main --no-interaction

RUN useradd -m -u 10001 pacer
USER pacer

ENTRYPOINT ["pacer"]
CMD ["schedule"]
