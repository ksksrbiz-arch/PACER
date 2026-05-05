.PHONY: install fmt lint test run migrate docker-up docker-down deploy-prep clean secrets deploy

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

# ── VPS deploy ────────────────────────────────────────────────────────
# Usage:
#   make secrets               # generate / rotate operator secrets locally
#   make deploy IP=1.2.3.4    # one-shot bootstrap of a fresh Ubuntu VPS
#   make deploy IP=1.2.3.4 SSH_USER=root SSH_KEY=~/.ssh/pacer_ed25519
secrets:
	python3 deploy/gen_secrets.py

deploy:
	@test -n "$(IP)" || (echo "Usage: make deploy IP=<vps-ip> [SSH_USER=root] [SSH_KEY=~/.ssh/pacer_ed25519]" && exit 1)
	bash deploy/remote_bootstrap.sh $(IP) $(or $(SSH_USER),root) $(or $(SSH_KEY),$$HOME/.ssh/pacer_ed25519)

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml
	find . -type d -name __pycache__ -exec rm -rf {} +
