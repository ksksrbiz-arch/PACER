FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir poetry==1.8.3

# Copy dependency files first (cache layer)
COPY pyproject.toml poetry.lock* ./

# Install dependencies (no dev deps in prod)
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi

# Copy source
COPY src/ ./src/
COPY alembic.ini ./
COPY migrations/ ./migrations/

# Non-root user for security
RUN useradd -m pacer && chown -R pacer:pacer /app
USER pacer

# Default command
CMD ["python", "-m", "src.main"]
