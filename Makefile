# NetHız Telekom + AI Support Assistant
SHELL := /bin/bash
COMPOSE := docker compose
OBS := docker compose -f docker-compose.yml -f docker-compose.observability.yml
SCENARIO ?= stuck_provisioning
CHAOS_ARGS ?=
PYTEST_ARGS ?=

.DEFAULT_GOAL := help
.PHONY: help env up up-observability down down-hard build logs ps smoke test test-unit \
        test-integration test-arch chaos chaos-reset chaos-status eval shell-core shell-assistant \
        seed open

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

env: ## Create .env from .env.example if missing
	@test -f .env || (cp .env.example .env && echo "created .env")

build: env ## Build all images
	$(COMPOSE) build

up: env ## Start company + assistant (single command demo)
	$(COMPOSE) up -d --build
	@$(MAKE) --no-print-directory wait

up-observability: env ## Start everything plus the self-hosted Langfuse stack
	$(OBS) up -d --build
	@echo "Langfuse UI: http://localhost:$${LANGFUSE_WEB_PORT_HOST:-3000}"

wait: ## Wait until the stack is healthy
	@echo "waiting for services..."
	@for i in $$(seq 1 60); do \
	  if curl -fsS localhost:8001/health >/dev/null 2>&1 && \
	     curl -fsS localhost:8003/health >/dev/null 2>&1 && \
	     curl -fsS localhost:8080/health >/dev/null 2>&1; then echo "stack up"; exit 0; fi; \
	  sleep 2; done; echo "timeout waiting for stack"; $(COMPOSE) ps; exit 1

down: ## Stop everything (keep data)
	$(OBS) down

down-hard: ## Stop everything and delete volumes
	$(OBS) down -v

ps: ## Show container status
	$(COMPOSE) ps

logs: ## Tail logs (SERVICE=core-api to narrow)
	$(COMPOSE) logs -f --tail=100 $(SERVICE)

smoke: ## Health + seed sanity checks against the running stack
	@bash scripts/smoke.sh

test: env ## Run the whole test suite inside the test-runner container
	$(COMPOSE) run --rm test-runner bash scripts/run_tests.sh $(PYTEST_ARGS)

test-unit: ## Unit tests only (no running stack required)
	$(COMPOSE) run --rm test-runner bash scripts/run_tests.sh -m "not integration" $(PYTEST_ARGS)

test-integration: ## Integration tests (requires: make up)
	$(COMPOSE) run --rm test-runner python -m pytest tests/integration -m integration $(PYTEST_ARGS)

test-arch: ## Architecture boundary tests only
	$(COMPOSE) run --rm test-runner python -m pytest tests/architecture $(PYTEST_ARGS)

chaos: ## Inject a failure: make chaos SCENARIO=stuck_provisioning [CHAOS_ARGS="--customer NH-100042"]
	$(COMPOSE) run --rm chaos python -m chaos.cli $(SCENARIO) $(CHAOS_ARGS)

chaos-reset: ## Undo every injected failure
	$(COMPOSE) run --rm chaos python -m chaos.cli reset

chaos-status: ## Show which scenarios are currently active
	$(COMPOSE) run --rm chaos python -m chaos.cli status

eval: env ## Run the evaluation suite and write a report
	$(COMPOSE) run --rm test-runner python -m evals.run --report evals/reports

seed: ## Re-run the company seed (idempotent)
	$(COMPOSE) exec core-api python -m app.seed

shell-core: ## Shell inside core-api
	$(COMPOSE) exec core-api bash

shell-assistant: ## Shell inside the assistant
	$(COMPOSE) exec assistant bash

open: ## Print the demo URLs
	@echo "Chat widget (customer)     http://localhost:8080"
	@echo "Ticket panel (departments) http://localhost:8003/agent"
	@echo "Department channels        http://localhost:8004"
	@echo "Core API docs              http://localhost:8001/docs"
	@echo "Prometheus / Alertmanager  http://localhost:9091 / http://localhost:9093"
