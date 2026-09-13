# Convenience targets. Everything here is also runnable by hand; nothing is hidden.

SHELL := /bin/bash
BACKEND := backend
FRONTEND := frontend
# The image superuser. It bootstraps roles and hands the schema to workos_owner; nothing in the
# application or the migrations ever connects as it.
SUPERUSER := workos
DEV_DB := workos
TEST_DB_URL := postgresql+psycopg://workos_owner:workos_owner@127.0.0.1:5432/workos_test
TEST_APP_URL := postgresql+psycopg://workos_app:workos_app@127.0.0.1:5432/workos_test

.PHONY: help up down logs install openapi migrate downgrade test test-unit test-fast connector-test connector-types connector-smoke store-smoke mail lint types imports check dev-db test-db seed web-install web-check web-test e2e

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

up:  ## Start PostgreSQL, Redis, MinIO, Keycloak
	docker compose up -d postgres redis minio

down:  ## Stop everything
	docker compose down

logs:  ## Follow container logs
	docker compose logs -f

install:  ## Install backend dependencies into the active virtualenv
	cd $(BACKEND) && pip install -e ".[dev]"

dev-db:  ## Provision roles and schema ownership on the development database
	# Idempotent. Only needed on a volume created before ops/db/dev-roles.sql was last changed;
	# a fresh volume runs the same script from docker-entrypoint-initdb.d.
	docker compose exec -T postgres psql -U $(SUPERUSER) -d postgres -f /docker-entrypoint-initdb.d/10-dev-roles.sql
	docker compose exec -T postgres psql -U $(SUPERUSER) -d $(DEV_DB) -f /docker-entrypoint-initdb.d/10-dev-roles.sql

test-db:  ## Create the test database (run once after `make up`)
	docker compose exec -T postgres psql -U $(SUPERUSER) -d postgres -f /docker-entrypoint-initdb.d/10-dev-roles.sql
	docker compose exec -T postgres psql -U $(SUPERUSER) -d postgres -c "CREATE DATABASE workos_test OWNER workos_owner;" || true
	docker compose exec -T postgres psql -U $(SUPERUSER) -d workos_test -f /docker-entrypoint-initdb.d/10-dev-roles.sql

openapi:  ## Regenerate the committed OpenAPI snapshot
	# The snapshot is reviewed in the diff, so regenerating it is a deliberate act rather than
	# something a test does behind the author's back.
	cd $(BACKEND) && PYTHONPATH=. python -c "import json, pathlib; \
from app.main import create_app; \
pathlib.Path('openapi.json').write_text(json.dumps(create_app().openapi(), indent=2, sort_keys=True) + chr(10))"

migrate:  ## Apply all migrations to the development database
	cd $(BACKEND) && alembic upgrade head

downgrade:  ## Roll every migration back to base
	cd $(BACKEND) && alembic downgrade base

test:  ## Full suite (needs PostgreSQL)
	cd $(BACKEND) && WORKOS_DATABASE_URL=$(TEST_DB_URL) WORKOS_APP_DATABASE_URL=$(TEST_APP_URL) \
		PYTHONPATH=. pytest -q

test-unit:  ## Domain and authorization tests only, no database
	cd $(BACKEND) && PYTHONPATH=. pytest tests/unit -q

test-fast: test-unit  ## Alias

connector-test:  ## The connectors' own tests. No database, no mail server, no network.
	.venv/bin/python -m pytest connectors/tests -q

mail:  ## Start the development mail server (GreenMail) for the IMAP connector
	docker compose --profile dev up -d mail

connector-smoke:  ## Drive the IMAP connector against a real mail server. Needs `make mail`.
	RUN_IMAP_TESTS=1 .venv/bin/python -m pytest connectors/tests -q -m imap

store-smoke:  ## Exercise the S3 adapter and the attachment flow against MinIO. Needs `make up`.
	cd $(BACKEND) && WORKOS_DATABASE_URL=$(TEST_DB_URL) WORKOS_APP_DATABASE_URL=$(TEST_APP_URL) \
		RUN_OBJECT_STORE_TESTS=1 PYTHONPATH=. pytest tests/integration/test_object_store.py -q

connector-types:  ## mypy --strict over the connectors
	.venv/bin/mypy --strict connectors/

lint:  ## ruff, over the backend and the connectors
	cd $(BACKEND) && ruff check .
	ruff check connectors/

types:  ## mypy --strict over contexts and platform
	cd $(BACKEND) && PYTHONPATH=. mypy

imports:  ## Architecture boundary contracts
	cd $(BACKEND) && lint-imports --config .importlinter

seed:  ## Create the development organization, people and team (idempotent)
	# Runs as the superuser: "which organization has this slug?" is a question from outside every
	# tenant, and RLS means no tenant-scoped role can answer it. See ops/dev/seed.py.
	PYTHONPATH=$(BACKEND) .venv/bin/python ops/dev/seed.py

web-install:  ## Install frontend dependencies
	cd $(FRONTEND) && npm ci

web-test:  ## Frontend component tests (L5)
	cd $(FRONTEND) && npm run test

web-check:  ## Frontend: generated-client drift, types, lint, component tests, production build
	# The drift check first, because a client that has fallen behind the spec makes every other
	# frontend failure below it a red herring.
	cd $(FRONTEND) && npm run generate:api:check && npm run typecheck && npm run lint \
		&& npm run test && npm run build

e2e:  ## The curated journeys (L6). Needs PostgreSQL; starts the API and the dev server itself.
	cd $(FRONTEND) && npm run e2e

check: lint types imports connector-test connector-types test web-check  ## Everything CI runs
