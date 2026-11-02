# Deployment — Wolf DevSecOps

[Español](DEPLOY.md) · **English**

> **The easiest path is the assisted installer** — see [`INSTALL.md`](../INSTALL.md)
> (`bash deploy/install.sh`). This document covers the manual and Kubernetes paths.

Two paths. Both bring up **the complete product from scratch**: Postgres + backend
(migrated, with the first admin) + frontend. Sources (GitHub, DefectDojo,
SonarQube, Harbor, Trivy, WSS) are connected **from the panel** at
`/admin/integrations`.

> Secrets and credentials live in `deploy/.env` (gitignored) or in a k8s `Secret`.
> They are never committed. The `.env`'s `WOLF_ADMIN_PASSWORD` is the first-login one.

---

## 0) FULL install — generates the sources from scratch ⭐

Brings up Wolf DevSecOps **and** its sources (SonarQube + DefectDojo + Trivy with
real scanning), wires them and leaves them **green** in the panel. Harbor runs in
its own stack (its official installer). GitHub is SaaS: you connect an existing one.

```bash
# 1. (fresh) start from the wiring template
cp config/projects.install.yaml config/projects.yaml

# 2. bring up Wolf + Sonar + DefectDojo + Trivy
docker compose -f deploy/docker-compose.install.yml up -d

# 3. Harbor (optional, heavy — its official installer, port 8085)
bash deploy/harbor/setup-harbor.sh

# 4. issue tokens, create the demo projects and restart the backend → sources green
bash deploy/provision-sources.sh

# 5. open the panel
open http://localhost:8088          # Integrations → "Test connection"
```

Ports: Wolf `8088`, SonarQube `9000`, DefectDojo `8086`, Harbor `8085`.

> **Apple Silicon (arm64):** DefectDojo only publishes amd64 images; the compose
> already marks them `platform: linux/amd64` (Rosetta emulation). SonarQube,
> Harbor, Trivy, Postgres and Redis run natively on arm64.
>
> **Already have your own Sonar/DefectDojo/Harbor?** Don't start the bundled one:
> in `config/projects.yaml` (or from the panel /admin/integrations) change that
> source's `base_url` to yours and put its token in `deploy/.env`. That's the
> "connect the existing one" option.

---

## A) Wolf DevSecOps only (no generated sources) ✅ verified

```bash
# 1. Secrets: a deploy/.env is already generated. To rotate them:
#    WOLF_JWT_SECRET     → python3 -c "import secrets;print(secrets.token_urlsafe(48))"
#    WOLF_MFA_FERNET_KEY → python3 -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"

# 2. Build and run
docker compose -f deploy/docker-compose.prototype.yml up -d --build

# 3. Open
open http://localhost:8088
```

On startup the backend: waits for Postgres → `alembic upgrade head` →
admin bootstrap (idempotent) → uvicorn. Health at `http://localhost:8088/api/health`.

**First login** (the real, hardened flow):
1. Sign in with `admin` / `WOLF_ADMIN_PASSWORD` (see `deploy/.env`).
2. The system forces a **password change** (`must_change_password`).
3. As an admin, it forces **MFA enrollment** (scan the QR with your TOTP app and
   confirm the code). Until MFA is enrolled, the admin area returns 403 (T-AUTH-04).
4. Once in: **Integrations** → connect GitHub/DefectDojo/Sonar/Harbor/Trivy and
   "Test connection".

Operations:
```bash
docker compose -f deploy/docker-compose.prototype.yml logs -f backend   # logs
docker compose -f deploy/docker-compose.prototype.yml down              # stop (keeps DB)
docker compose -f deploy/docker-compose.prototype.yml down -v           # stop and WIPE DB
```

### All-in-one (also brings up Sonar + DefectDojo as local targets)
```bash
docker compose -f deploy/docker-compose.full.yml up -d --build
```

---

## B) Kubernetes (manifests in `deploy/k8s/`)

YAML validated structurally offline (not runtime-verified: no cluster in this
environment). Requires a cluster and the images available (on a local cluster,
load them: `kind load docker-image …` / `minikube image load …`).

```bash
# 1. Images with the tag the manifests expect
docker tag wolf-devsecops-backend:latest  wolf-devsecops-backend:proto
docker tag wolf-devsecops-frontend:latest wolf-devsecops-frontend:proto
# (kind) kind load docker-image wolf-devsecops-backend:proto wolf-devsecops-frontend:proto

# 2. Namespace + real Secret (see the header of 10-secret.example.yaml to generate it)
kubectl apply -f deploy/k8s/00-namespace.yaml
kubectl -n wolf-devsecops create secret generic wolf-secrets \
  --from-literal=POSTGRES_PASSWORD="$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')" \
  --from-literal=WOLF_JWT_SECRET="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')" \
  --from-literal=WOLF_MFA_FERNET_KEY="$(python3 -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())')" \
  --from-literal=WOLF_ADMIN_PASSWORD="$(python3 -c 'import secrets;print(secrets.token_urlsafe(15))')"
#   ↑ save the WOLF_ADMIN_PASSWORD that is printed: it's the first-login one.

# 3. Rest of the stack
kubectl apply -f deploy/k8s/20-postgres.yaml -f deploy/k8s/30-backend.yaml \
              -f deploy/k8s/40-frontend.yaml -f deploy/k8s/50-ingress.yaml

# 4. Access (no ingress): port-forward
kubectl -n wolf-devsecops port-forward svc/frontend 8088:80
```

Notes:
- Backend at **1 replica** on purpose: the entrypoint applies migrations on startup
  (avoids a race between two simultaneous `alembic upgrade`s). To scale, move the
  migration to a dedicated `Job`/`initContainer` and leave the app Pods without it.
- The example `Secret` (`10-secret.example.yaml`) is a **template**: do not apply
  its `CAMBIAME` values.
