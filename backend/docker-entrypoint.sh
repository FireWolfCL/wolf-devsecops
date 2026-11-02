#!/usr/bin/env sh
# =============================================================================
# Entrypoint del backend: espera BD -> migra -> bootstrap admin -> uvicorn.
# Idempotente: en reinicios las migraciones ya están al día y el admin ya existe.
# =============================================================================
set -e

# 1. Espera a Postgres (si DB_HOST está definido; con SQLite no aplica).
if [ -n "$DB_HOST" ]; then
  echo "[entrypoint] esperando a la BD en $DB_HOST:${DB_PORT:-5432}…"
  until nc -z "$DB_HOST" "${DB_PORT:-5432}"; do sleep 1; done
  echo "[entrypoint] BD disponible."
fi

# 2. Migraciones (ADR-012: el esquema lo gobierna Alembic). Se corre desde /app,
#    donde viven alembic.ini y migrations/; la URL sale de WOLF_DATABASE_URL.
echo "[entrypoint] aplicando migraciones (alembic upgrade head)…"
alembic upgrade head

# 3. Bootstrap del primer admin (fail-closed en el CLI: solo si no hay usuarios).
#    El '|| true' tolera el reinicio, donde el admin ya existe.
if [ -n "$WOLF_ADMIN_USERNAME" ] && [ -n "$WOLF_ADMIN_PASSWORD" ]; then
  echo "[entrypoint] bootstrap del admin '$WOLF_ADMIN_USERNAME' (si no existe)…"
  if [ -n "$WOLF_ADMIN_EMAIL" ]; then
    wolf-devsecops create-admin "$WOLF_ADMIN_USERNAME" --password "$WOLF_ADMIN_PASSWORD" --email "$WOLF_ADMIN_EMAIL" || echo "[entrypoint] admin ya existe; se omite."
  else
    wolf-devsecops create-admin "$WOLF_ADMIN_USERNAME" --password "$WOLF_ADMIN_PASSWORD" || echo "[entrypoint] admin ya existe; se omite."
  fi
fi

# 4. Arranca el servidor (CMD del Dockerfile).
echo "[entrypoint] arrancando servidor…"
exec "$@"
