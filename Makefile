.PHONY: dev api web test lint build deploy reset

api:            ## run the API on :8100 (uses backend/.env)
	cd backend && PYTHONPATH=. uv run uvicorn app.main:app --reload --port 8100

web:            ## run the UI on :5173 (proxies /api to :8100)
	cd frontend && npm run dev

test:           ## offline test suite (fake LLM, temp SQLite)
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check app tests
	cd frontend && npx tsc -b

build:
	cd frontend && npm run build

deploy:         ## build + ship to the Oracle box
	./deploy/deploy.sh

reset:          ## wipe local demo data
	rm -rf backend/data
