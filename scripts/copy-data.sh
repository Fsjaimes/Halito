#!/usr/bin/env sh
# Copia los datos de la base de desarrollo a la de producción, para no repetir en el servidor
# horas de descarga del portal y de procesamiento de Landsat.
#
#   scripts/copy-data.sh dump                    # en tu equipo: crea data/halito.dump
#   scripts/copy-data.sh restore [archivo]       # donde corre la producción: lo carga
#
# La restauración reemplaza los datos de la base de producción.
set -eu

DUMP=${2:-data/halito.dump}
PROD="docker compose -f docker-compose.prod.yml"

case "${1:-}" in
  dump)
    mkdir -p "$(dirname "$DUMP")"
    docker compose exec -T db pg_dump -U halito -d halito --format=custom --no-owner > "$DUMP"
    echo "Copia creada: $DUMP ($(du -h "$DUMP" | cut -f1))"
    ;;
  restore)
    [ -f "$DUMP" ] || { echo "No existe $DUMP" >&2; exit 1; }
    $PROD up -d --wait db
    # TimescaleDB necesita prepararse antes de restaurar hypertables y agregados continuos.
    $PROD exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q -c "
      DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;
      CREATE EXTENSION IF NOT EXISTS timescaledb; SELECT timescaledb_pre_restore();"'
    $PROD exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --exit-on-error' < "$DUMP"
    $PROD exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q -c "SELECT timescaledb_post_restore();"'
    echo "Datos restaurados."
    ;;
  *)
    echo "Uso: $0 dump|restore [archivo]" >&2
    exit 2
    ;;
esac
