# Convenience targets. Everything here is also runnable by hand; nothing is hidden.

SHELL := /bin/bash
BACKEND := backend
TEST_DB_URL := postgresql+psycopg://workos:workos@127.0.0.1:5432/workos_test
TEST_APP_URL := postgresql+psycopg://workos_app:workos_app@127.0.0.1:5432/workos_test

.PHONY: help up down logs install migrate downgrade test test-unit test-fast lint types imports check test-db

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

test-db:  ## Create the test database (run once after `make up`)
	docker compose exec -T postgres psql -U workos -d postgres -c "CREATE DATABASE workos_test OWNER workos;" || true
	docker compose exec -T postgres psql -U workos -d workos_test -f /docker-entrypoint-initdb.d/10-dev-roles.sql

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

lint:  ## ruff
	cd $(BACKEND) && ruff check .

types:  ## mypy --strict over contexts and platform
	cd $(BACKEND) && PYTHONPATH=. mypy

imports:  ## Architecture boundary contracts
	cd $(BACKEND) && lint-imports --config .importlinter

check: lint types imports test  ## Everything CI runs
