# =============================================================================
# Wolf DevSecOps — backend (FastAPI + Alembic). Imagen de producción.
# -----------------------------------------------------------------------------
# - Instala el paquete en modo editable con el extra `postgres` (asyncpg) para
#   que `alembic.ini` y `migrations/` se resuelvan contra /app (build_alembic_config
#   usa la ruta del paquete importado — ver update_service._BACKEND_DIR).
# - Corre como usuario sin privilegios (uid 10001).
# - El entrypoint espera a la BD, aplica migraciones y hace bootstrap del primer
#   admin antes de arrancar uvicorn.
# =============================================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# netcat: espera activa a que Postgres acepte conexiones (entrypoint).
RUN apt-get update \
    && apt-get install -y --no-install-recommends netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

COPY . /app
RUN pip install -e ".[postgres]"

# Usuario sin privilegios; dueño de /app (editable install escribe .egg-info).
RUN useradd --system --uid 10001 --create-home wolf \
    && chmod +x /app/docker-entrypoint.sh \
    && chown -R wolf:wolf /app
USER wolf

EXPOSE 8000
ENV WOLF_CONFIG_PATH=/config/projects.yaml

# Salud real: /api/health responde aunque falte config (ver routes_health).
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=10 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "wolf_devsecops.main:app", "--host", "0.0.0.0", "--port", "8000"]
