# Despliegue — Wolf DevSecOps

**Español** · [English](DEPLOY.en.md)

Dos caminos. Ambos levantan **el producto completo de 0**: Postgres + backend
(migrado y con el primer admin) + frontend. Las fuentes (GitHub, DefectDojo,
SonarQube, Harbor, Trivy, WSS) se conectan **desde el panel** `/admin/integraciones`.

> Secretos y credenciales viven en `deploy/.env` (gitignored) o en un `Secret` de
> k8s. Nunca se comitean. El `WOLF_ADMIN_PASSWORD` del `.env` es el del primer login.

---

## 0) Instalación COMPLETA — genera las fuentes desde 0 ⭐

Levanta Wolf DevSecOps **y** sus fuentes (SonarQube + DefectDojo + Trivy con
escaneo real), las cablea y las deja **en verde** en el panel. Harbor va en su
propio stack (su instalador oficial). GitHub es SaaS: se conecta a uno existente.

```bash
# 1. (fresco) parte de la plantilla de wiring
cp config/projects.install.yaml config/projects.yaml

# 2. levanta Wolf + Sonar + DefectDojo + Trivy
docker compose -f deploy/docker-compose.install.yml up -d

# 3. Harbor (opcional, pesado — su instalador oficial, puerto 8085)
bash deploy/harbor/setup-harbor.sh

# 4. emite tokens, crea los proyectos demo y reinicia el backend → fuentes en verde
bash deploy/provision-sources.sh

# 5. abre el panel
open http://localhost:8088          # Integraciones → "Probar conexión"
```

Puertos: Wolf `8088`, SonarQube `9000`, DefectDojo `8086`, Harbor `8085`.

> **Apple Silicon (arm64):** DefectDojo solo publica imágenes amd64; el compose ya
> las marca `platform: linux/amd64` (emulación Rosetta). SonarQube, Harbor, Trivy,
> Postgres y Redis corren nativos arm64.
>
> **¿Ya tienes un Sonar/DefectDojo/Harbor propio?** No levantes el suyo: en
> `config/projects.yaml` (o desde el panel /admin/integraciones) cambia el
> `base_url` de esa fuente por el tuyo y pon su token en `deploy/.env`. Esa es la
> opción de "conectar lo existente".

---

## A) Solo Wolf DevSecOps (sin generar fuentes) ✅ verificado

```bash
# 1. Secretos: ya hay un deploy/.env generado. Para rotarlos:
#    WOLF_JWT_SECRET     → python3 -c "import secrets;print(secrets.token_urlsafe(48))"
#    WOLF_MFA_FERNET_KEY → python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"

# 2. Construir y levantar
docker compose -f deploy/docker-compose.prototype.yml up -d --build

# 3. Abrir
open http://localhost:8088
```

El backend, al arrancar: espera a Postgres → `alembic upgrade head` →
bootstrap del admin (idempotente) → uvicorn. Salud en `http://localhost:8088/api/health`.

**Primer login** (es el flujo real endurecido):
1. Entra con `admin` / `WOLF_ADMIN_PASSWORD` (ver `deploy/.env`).
2. El sistema exige **cambiar la contraseña** (`must_change_password`).
3. Por ser admin, exige **enrolar MFA** (escanea el QR con tu app TOTP y confirma
   el código). Hasta enrolar MFA, el panel admin responde 403 (T-AUTH-04).
4. Ya dentro: **Integraciones** → conectar GitHub/DefectDojo/Sonar/Harbor/Trivy y
   "Probar conexión".

Operación:
```bash
docker compose -f deploy/docker-compose.prototype.yml logs -f backend   # logs
docker compose -f deploy/docker-compose.prototype.yml down              # parar (conserva BD)
docker compose -f deploy/docker-compose.prototype.yml down -v           # parar y BORRAR BD
```

### All-in-one (además levanta Sonar + DefectDojo como objetivos locales)
```bash
docker compose -f deploy/docker-compose.full.yml up -d --build
```

---

## B) Kubernetes (manifiestos en `deploy/k8s/`)

YAML validado estructuralmente offline (no verificado en runtime: no hay cluster
en este entorno). Requiere un cluster y las imágenes disponibles (en cluster
local, cárgalas: `kind load docker-image …` / `minikube image load …`).

```bash
# 1. Imágenes con el tag que esperan los manifiestos
docker tag wolf-devsecops-backend:latest  wolf-devsecops-backend:proto
docker tag wolf-devsecops-frontend:latest wolf-devsecops-frontend:proto
# (kind) kind load docker-image wolf-devsecops-backend:proto wolf-devsecops-frontend:proto

# 2. Namespace + Secret real (ver cabecera de 10-secret.example.yaml para generarlo)
kubectl apply -f deploy/k8s/00-namespace.yaml
kubectl -n wolf-devsecops create secret generic wolf-secrets \
  --from-literal=POSTGRES_PASSWORD="$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')" \
  --from-literal=WOLF_JWT_SECRET="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')" \
  --from-literal=WOLF_MFA_FERNET_KEY="$(python3 -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')" \
  --from-literal=WOLF_ADMIN_PASSWORD="$(python3 -c 'import secrets;print(secrets.token_urlsafe(15))')"
#   ↑ guarda el WOLF_ADMIN_PASSWORD que se imprima: es el del primer login.

# 3. Resto del stack
kubectl apply -f deploy/k8s/20-postgres.yaml -f deploy/k8s/30-backend.yaml \
              -f deploy/k8s/40-frontend.yaml -f deploy/k8s/50-ingress.yaml

# 4. Acceso (sin ingress): port-forward
kubectl -n wolf-devsecops port-forward svc/frontend 8088:80
```

Notas:
- Backend a **1 réplica** a propósito: el entrypoint aplica migraciones al arrancar
  (evita carrera de dos `alembic upgrade` simultáneos). Para escalar, mueve la
  migración a un `Job`/`initContainer` dedicado y deja los Pods de app sin migrar.
- El `Secret` de ejemplo (`10-secret.example.yaml`) es **plantilla**: no apliques
  sus valores `CAMBIAME`.
