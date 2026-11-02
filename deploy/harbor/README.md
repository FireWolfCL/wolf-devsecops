# Harbor — registry + escaneo de imágenes (generado desde 0)

Harbor **no** se despliega con un `docker-compose.yml` escrito a mano: su
herramienta `prepare` genera el compose y la configuración a partir de
`harbor.yml`. Por eso aquí hay un **script** que automatiza su instalador oficial
en vez de un compose propio.

## Levantarlo
```bash
bash deploy/harbor/setup-harbor.sh
```
Esto:
1. descarga el *online installer* de Harbor (versión fijada en el script),
2. escribe `harbor.yml` (HTTP, puerto `${HARBOR_HTTP_PORT:-8085}`, pass admin
   desde `deploy/.env`, Trivy-adapter activado),
3. corre `./prepare --with-trivy` y `docker compose up -d`.

Queda en `http://localhost:8085` — usuario `admin`, contraseña
`HARBOR_ADMIN_PASSWORD` (`deploy/.env`). Wolf DevSecOps lo alcanza por
`host.docker.internal:8085` (ya configurado en `config/projects.yaml`).

Después, cabléalo a Wolf (crea el proyecto y lo pone en verde):
```bash
bash deploy/provision-sources.sh
```

## Notas
- **Pesado:** Harbor son ~10 contenedores (core, portal, registry, jobservice,
  db, redis, proxy, trivy-adapter…). Dale RAM.
- El directorio de trabajo `deploy/harbor/harbor/` (installer, imágenes, data,
  logs) está en `.gitignore`: no es fuente.
- **Apple Silicon:** Harbor publica imágenes multi-arch (arm64), corre nativo.
- Parar Harbor: `cd deploy/harbor/harbor/harbor && docker compose down`.
- El conector de Wolf prueba Harbor por `/api/v2.0/systeminfo` (público): el probe
  queda **verde** (alcanzable + versión) con Harbor arriba. Para leer CVEs por
  imagen, sube una imagen al proyecto y deja que el Trivy-adapter la escanee.
