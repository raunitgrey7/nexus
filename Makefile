# NEXUS developer entry points. Works with GNU make on macOS/Linux/WSL/Git-Bash.
.DEFAULT_GOAL := help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

setup: ## Install backend (uv) and frontend (npm) dependencies
	cd backend && uv sync --group dev --extra bench
	cd frontend && npm install

api: ## Run the backend API with auto-reload on :8000
	cd backend && uv run uvicorn nexus.api.app:create_app --factory --reload --port 8000

ui: ## Run the Next.js twin UI on :3000
	cd frontend && npm run dev

dev: ## Run API + UI together (requires two terminals normally; this uses background jobs)
	$(MAKE) -j2 api ui

test: ## Run the backend test-suite
	cd backend && uv run pytest -q

test-all: ## Run all tests including slow ones and coverage
	cd backend && uv run pytest -q --cov=nexus --cov-report=term-missing

lint: ## Ruff + mypy + eslint + tsc
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy nexus
	cd frontend && npm run lint && npm run typecheck

format: ## Auto-format backend and frontend
	cd backend && uv run ruff format . && uv run ruff check --fix .
	cd frontend && npm run format

bench: ## Run the full benchmark suite (small/medium/large × strategies × seeds)
	cd backend && uv run python -m benchmarks.run_benchmark --scale small medium large --seeds 3

bench-quick: ## Quick benchmark (small, 1 seed)
	cd backend && uv run python -m benchmarks.run_benchmark --scale small --seeds 1 --minutes 60

deck: ## Build the pitch deck (pitch/NEXUS_Pitch_Deck.pptx)
	cd backend && uv run --with python-pptx --with matplotlib python ../pitch/build_deck.py

up: ## docker compose up (postgres, redis, backend, frontend, prometheus, grafana)
	docker compose up --build -d

down: ## docker compose down
	docker compose down

logs: ## tail backend logs
	docker compose logs -f backend

demo: ## Run the scripted demo storyline in the terminal (R07 failure → 46 strategies → best plan)
	cd backend && uv run python -m nexus demo

.PHONY: help setup api ui dev test test-all lint format bench bench-quick deck up down logs demo
