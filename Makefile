.DEFAULT_GOAL := help

.PHONY: help install lint fmt test run migrate docker-up docker-down

help:
	@echo "PACER — 1COMMERCE LLC DomainFi / RWA Platform"
	@echo ""
	@echo "Usage:"
	@echo "  make install      Install dependencies (poetry)"
	@echo "  make lint         Run Ruff + Black checks"
	@echo "  make fmt          Auto-format with Black"
	@echo "  make test         Run pytest"
	@echo "  make run          Run the daily pipeline once (manual)"
	@echo "  make migrate      Apply Alembic migrations"
	@echo "  make docker-up    Start local dev stack (Postgres + app)"
	@echo "  make docker-down  Stop local dev stack"

install:
	poetry install

lint:
	poetry run ruff check src/ tests/
	poetry run black --check src/ tests/

fmt:
	poetry run black src/ tests/

test:
	poetry run pytest tests/ -v --tb=short

run:
	poetry run python -c "\
import asyncio; \
from src.pacer.pacer_client import PACERClient; \
asyncio.run(PACERClient().fetch_yesterday_bankruptcies())"

migrate:
	poetry run alembic upgrade head

docker-up:
	docker-compose up -d

docker-down:
	docker-compose down
