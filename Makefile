.PHONY: install fmt lint test run migrate docker-up docker-down deploy-prep clean

install:
	poetry install

fmt:
	poetry run black src tests
	poetry run ruff check --fix src tests

lint:
	poetry run ruff check src tests
	poetry run black --check src tests
	poetry run mypy src

test:
	poetry run pytest

run:
	poetry run python -m pacer.main

migrate:
	poetry run alembic upgrade head

migration:
	poetry run alembic revision --autogenerate -m "$(m)"

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f pacer

deploy-prep:
	@echo "==> PACER :: 1COMMERCE LLC deploy-prep"
	@test -f .env || (echo "Missing .env — copy from .env.example" && exit 1)
	docker compose build
	docker compose run --rm pacer alembic upgrade head
	@echo "==> Ready. Run: make docker-up"

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml
	find . -type d -name __pycache__ -exec rm -rf {} +
