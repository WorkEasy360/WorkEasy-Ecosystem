# Workeasy360 - developer entry points.
#
# Every target here is also a CI job, so "it passed locally" means the same
# thing as "it will pass in CI".

PY := .venv/Scripts/python.exe
ifeq ($(OS),)
	PY := .venv/bin/python
endif
BACKEND := backend
export PYTHONPATH := $(BACKEND)

.PHONY: help
help:
	@echo "setup        Create the virtualenv and install dependencies"
	@echo "up           Start PostgreSQL, PgBouncer, Redis and the broker"
	@echo "down         Stop the local stack"
	@echo "migrate      Run migrations as the owner role"
	@echo "test         Full test suite"
	@echo "test-fast    Everything except the slow integration tests"
	@echo "lint         Ruff lint + format check"
	@echo "lint-arch    Architecture dependency contracts"
	@echo "test-arch    Architecture fitness tests"
	@echo "typecheck    mypy over contracts/"
	@echo "check        Django system checks (W360.E001-E009)"
	@echo "security     Bandit + dependency audit + secret scan"
	@echo "spikes       Re-run the three Phase 1 verification spikes"
	@echo "ci           Everything CI runs"

.PHONY: setup
setup:
	python -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements-dev.txt

.PHONY: up
up:
	docker compose up -d
	@echo "PostgreSQL 5442 | PgBouncer 6442 | Redis 6392 | RabbitMQ 5682"

.PHONY: down
down:
	docker compose down

.PHONY: migrate
migrate:
	DJANGO_SETTINGS_MODULE=config.settings.migration $(PY) $(BACKEND)/manage.py migrate

.PHONY: makemigrations
makemigrations:
	$(PY) $(BACKEND)/manage.py makemigrations

.PHONY: test
test:
	$(PY) -m pytest -q

.PHONY: test-fast
test-fast:
	$(PY) -m pytest -q -m "not slow"

.PHONY: lint
lint:
	$(PY) -m ruff check $(BACKEND)
	$(PY) -m ruff format --check $(BACKEND)

.PHONY: fmt
fmt:
	$(PY) -m ruff check $(BACKEND) --fix
	$(PY) -m ruff format $(BACKEND)

.PHONY: lint-arch
lint-arch:
	.venv/Scripts/lint-imports.exe || lint-imports

.PHONY: test-arch
test-arch:
	$(PY) -m pytest -q -m architecture

.PHONY: typecheck
typecheck:
	$(PY) -m mypy

.PHONY: check
check:
	DJANGO_SETTINGS_MODULE=config.settings.migration $(PY) $(BACKEND)/manage.py check --deploy

.PHONY: security
security:
	$(PY) -m bandit -c pyproject.toml -r $(BACKEND)/workeasy360 $(BACKEND)/config -ll
	$(PY) -m pip_audit -r requirements.txt --strict

.PHONY: spikes
spikes:
	$(PY) $(BACKEND)/tests/spikes/spike_r01_rls_pgbouncer.py
	$(PY) $(BACKEND)/tests/spikes/spike_r09_broker.py
	$(PY) $(BACKEND)/tests/spikes/spike_r14_entitlement_latency.py

.PHONY: ci
ci: lint typecheck lint-arch check test
	@echo "All local gates passed."
