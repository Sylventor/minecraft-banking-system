.DEFAULT_GOAL := help

.PHONY: help up down logs test test-backend test-core test-web test-integration proto fmt lint migrate-auth migrate-finance
help:
	@echo "Targets: up down logs test test-backend test-core test-web test-integration proto fmt lint migrate-auth migrate-finance"
up:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
down:
	docker compose down
logs:
	docker compose logs -f
test: test-backend test-core test-web
test-backend:
	python -m pytest backend/tests sdk/tests bot/tests -q
test-core:
	cmake -S . -B build && cmake --build build && ctest --test-dir build --output-on-failure
test-web:
	cd frontend && corepack pnpm test
test-integration:
	python tests/run_integration.py
proto:
	mkdir -p backend/app/grpc/generated
	python -m grpc_tools.protoc -I core/proto --python_out=backend/app/grpc/generated --pyi_out=backend/app/grpc/generated --grpc_python_out=backend/app/grpc/generated core/proto/horizon/banking/v1/banking.proto
fmt:
	cd backend && ruff format app tests
	cd frontend && corepack pnpm run format
lint:
	cd backend && ruff check app tests && mypy app --exclude generated
	cd frontend && corepack pnpm run lint && corepack pnpm run typecheck
migrate-auth:
	cd backend && alembic -c alembic-auth.ini upgrade head
migrate-finance:
	cd database/finance && alembic -c alembic.ini upgrade head
