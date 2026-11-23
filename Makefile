.PHONY: up down migrate ingest heat report api web test lint format prod dump

up:        ## Levanta la base de datos y espera a que esté lista
	docker compose up -d --wait

down:      ## Detiene los contenedores (los datos se conservan)
	docker compose down

migrate:   ## Aplica las migraciones pendientes
	cd backend && uv run alembic upgrade head

ingest:    ## Descarga y carga las lecturas nuevas de la CDMB y del IDEAM
	cd backend && uv run halito sync-stations && uv run halito ingest-cdmb && uv run halito ingest-ideam && uv run halito detect-episodes

heat:      ## Calcula el calor del suelo con imágenes Landsat (la primera vez tarda ~30 min)
	cd backend && uv run halito ingest-landsat

report:    ## Horas cargadas y cobertura por estación y variable
	cd backend && uv run halito quality-report

api:       ## Arranca la API en http://localhost:8000
	cd backend && uv run uvicorn halito.api.main:app --reload

web:       ## Arranca la interfaz en http://localhost:5173 (necesita la API encendida)
	cd web && npm install && npm run dev

test:      ## Corre los tests (necesita la base de datos migrada)
	cd backend && uv run pytest

lint:      ## Revisa estilo y tipos
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy
	cd web && npm run typecheck

format:    ## Aplica el formato
	cd backend && uv run ruff check --fix . && uv run ruff format .

prod:      ## Levanta el despliegue completo (base, API y web detrás de Caddy)
	docker compose -f docker-compose.prod.yml up -d --build --wait

dump:      ## Copia la base de desarrollo a data/halito.dump para llevarla al servidor
	scripts/copy-data.sh dump
