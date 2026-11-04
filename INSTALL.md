<div align="center">

# 🐺 Wolf DevSecOps — Instalación / Installation

**Español** · [English](#english)

</div>

---

## Español

### Requisitos

- **Docker Desktop** (o Docker Engine) con **Docker Compose v2** (`docker compose`).
- **RAM libre en Docker según el perfil**: `lite` ~2 GB · `standard` ~4–5 GB ·
  `full` ~6 GB+ (SonarQube solo ya pide 2–3 GB). Súbela en *Docker Desktop →
  Settings → Resources* si hace falta. Ver [Perfiles](#perfiles-de-instalación).
- macOS, Linux o Windows (con WSL2). En Apple Silicon todo corre nativo salvo
  DefectDojo, que va emulado (amd64) — ya está configurado.

### Instalación asistida (recomendada) ⭐

Un solo comando levanta **toda la suite** (panel Wolf + SonarQube + DefectDojo +
Trivy), genera los secretos, migra la base, crea el primer admin y te deja el
panel abierto:

```bash
git clone https://github.com/FireWolfCL/wolf-devsecops.git
cd wolf-devsecops
bash deploy/install.sh
```

El instalador es **interactivo y bilingüe**: elige idioma, confirma los pasos y,
al final, te imprime la URL y la contraseña del primer login.

**En Windows** (PowerShell), si prefieres no usar WSL:

```powershell
git clone https://github.com/FireWolfCL/wolf-devsecops.git
cd wolf-devsecops
pwsh deploy/install.ps1
```

Modo no interactivo (CI / scripts):

```bash
bash deploy/install.sh --yes --lang es --profile standard
```

### Perfiles de instalación

El instalador te pregunta el **perfil** (o pásalo con `--profile`):

| Perfil | Qué levanta | RAM aprox. | Para quién |
|---|---|---|---|
| `lite` | Panel + **SonarQube** + **Trivy** (sin DefectDojo) | **~2 GB** | Arrancar el día uno, máquinas modestas, demo rápida |
| `standard` (default) | lite **+ DefectDojo** (gestión de hallazgos) | ~4–5 GB | Uso normal con triage de vulnerabilidades |
| `full` | standard **+ Harbor** (registry; su instalador aparte) | ~6 GB+ | Escaneo de las imágenes que realmente despliegas |

En `lite`, DefectDojo y Harbor aparecen como **“no configurados”** en el panel; se
suman reinstalando en `standard`/`full` o conectando instancias propias desde
**Admin → Integraciones**. Ejemplos:

```bash
bash deploy/install.sh --yes --profile lite       # liviano, ~2 GB
bash deploy/install.sh --yes --profile full       # standard + Harbor
```

En Windows: `pwsh deploy/install.ps1 -Profile lite`.

### Pilar Contenedores: Harbor (productivo) vs Trivy suelto (fallback)

El pilar **Contenedores** tiene dos caminos. Elige según si tienes un registry:

| | **Harbor + trivy-adapter** (camino principal) | **Trivy suelto** (`deploy/scan-target.sh --image …`) |
|---|---|---|
| Qué escanea | Las imágenes que **realmente subes al registry y despliegas** | Una imagen que le indicas a mano, en tu máquina |
| Cuándo usarlo | Tienes (o vas a tener) un registry y despliegas desde ahí | **No** tienes registry, o quieres un primer puntaje el día uno |
| Perfil | `full` | `lite` y `standard` (ya viene incluido) |
| Costo | **~10 contenedores** (core, portal, registry, jobservice, db, redis, proxy, trivy-adapter…), ~6 GB+ de RAM en total, y **su propio instalador**: `deploy/harbor/setup-harbor.sh` (no es un servicio del compose del panel) | Casi nulo: un contenedor `aquasec/trivy` efímero |
| Re-escaneo | El trivy-adapter de Harbor re-escanea al subir una imagen | Lo corres tú cada vez |

**Recomendación:** empieza con `lite` + Trivy suelto (semana 1, ver
[docs/PRIMEROS-30-DIAS.md](docs/PRIMEROS-30-DIAS.md)) y pasa a **Harbor a partir del
mes 2**, cuando ya operas el panel con soltura y tu flujo de despliegue pasa por un
registry. Harbor es el flujo productivo; el Trivy suelto es el fallback.

```bash
bash deploy/install.sh --yes --profile full      # standard + ofrece instalar Harbor
bash deploy/harbor/setup-harbor.sh               # o instalar/reintentar Harbor aparte
bash deploy/provision-sources.sh                 # cablea Harbor al panel
```

Antes de instalar Harbor, define `HARBOR_ADMIN_PASSWORD` en `deploy/.env` (el instalador
no la genera y `setup-harbor.sh` la exige). Detalles de Harbor (puerto `8085`, versión fijada, `docker compose down` propio):
[`deploy/harbor/README.md`](deploy/harbor/README.md).

### Qué hace el instalador

1. Verifica Docker + Compose.
2. Genera `deploy/.env` con secretos aleatorios (**no pisa uno existente**).
3. Siembra `config/projects.yaml` desde la plantilla.
4. `docker compose up -d --build` de toda la suite.
5. Espera a que el backend esté sano (migra + crea admin automáticamente).
6. (Opcional) Emite tokens y cablea las fuentes demo (`provision-sources.sh`).
7. Imprime accesos y próximos pasos.

### Primer login (flujo endurecido)

1. Entra a **http://localhost:8088** con `admin` y la contraseña que imprimió el
   instalador (está en `deploy/.env`, `WOLF_ADMIN_PASSWORD`).
2. El sistema **exige cambiar la contraseña**.
3. Por ser admin, **exige enrolar MFA**: escanea el QR con tu app TOTP (Google
   Authenticator, Aegis…) y confirma el código. Hasta enrolar MFA, el área admin
   responde 403 (por diseño).
4. Ya dentro: **Admin → Integraciones** para conectar GitHub/SonarQube/DefectDojo/
   Harbor/Trivy y pulsar *“Probar conexión”*.

### Puertos

| Servicio | URL |
|---|---|
| Panel Wolf | http://localhost:8088 |
| SonarQube | http://localhost:9000 |
| DefectDojo | http://localhost:8086 |
| Harbor (opcional) | http://localhost:8085 |

### Operación

```bash
# Ver logs del backend
docker compose -f deploy/docker-compose.install.yml logs -f backend
# Parar (conserva los datos)
docker compose -f deploy/docker-compose.install.yml down
# Borrar TODO, incluidos los datos
docker compose -f deploy/docker-compose.install.yml down -v
```

**Backup, restore, actualizaciones y rotación de claves:** ver
[docs/RUNBOOK.md](docs/RUNBOOK.md) (`deploy/backup.sh`, `deploy/restore.sh`).
**¿Primera vez y estás solo?** Sigue [docs/PRIMEROS-30-DIAS.md](docs/PRIMEROS-30-DIAS.md).

### ¿Ya tienes tu propio Sonar / DefectDojo / Harbor?

No levantes el del stack: en **Admin → Integraciones** (o en `config/projects.yaml`)
cambia el `base_url` de esa fuente por el tuyo y pon su token en `deploy/.env`.

> Para Kubernetes, ver [`deploy/DEPLOY.md`](deploy/DEPLOY.md).

---

## English

### Requirements

- **Docker Desktop** (or Docker Engine) with **Docker Compose v2** (`docker compose`).
- **Free RAM in Docker depends on the profile**: `lite` ~2 GB · `standard`
  ~4–5 GB · `full` ~6 GB+ (SonarQube alone needs 2–3 GB). Raise it under *Docker
  Desktop → Settings → Resources* if needed. See [Profiles](#install-profiles).
- macOS, Linux or Windows (with WSL2). On Apple Silicon everything runs natively
  except DefectDojo, which runs emulated (amd64) — already configured.

### Assisted install (recommended) ⭐

One command brings up the **whole suite** (Wolf panel + SonarQube + DefectDojo +
Trivy), generates the secrets, migrates the database, creates the first admin and
leaves the panel open:

```bash
git clone https://github.com/FireWolfCL/wolf-devsecops.git
cd wolf-devsecops
bash deploy/install.sh --lang en
```

The installer is **interactive and bilingual**: pick a language, confirm the
steps, and at the end it prints the URL and the first-login password.

**On Windows** (PowerShell), if you'd rather not use WSL:

```powershell
git clone https://github.com/FireWolfCL/wolf-devsecops.git
cd wolf-devsecops
pwsh deploy/install.ps1 -Lang en
```

Non-interactive (CI / scripts):

```bash
bash deploy/install.sh --yes --lang en --profile standard
```

### Install profiles

The installer asks for a **profile** (or pass it with `--profile`):

| Profile | What it brings up | Approx. RAM | For whom |
|---|---|---|---|
| `lite` | Panel + **SonarQube** + **Trivy** (no DefectDojo) | **~2 GB** | Day-one start, modest machines, quick demo |
| `standard` (default) | lite **+ DefectDojo** (findings management) | ~4–5 GB | Normal use with vulnerability triage |
| `full` | standard **+ Harbor** (registry; its own installer) | ~6 GB+ | Scanning the images you actually deploy |

In `lite`, DefectDojo and Harbor show as **“not configured”** in the panel; add
them by reinstalling as `standard`/`full` or by connecting your own instances from
**Admin → Integrations**. Examples:

```bash
bash deploy/install.sh --yes --profile lite       # lightweight, ~2 GB
bash deploy/install.sh --yes --profile full       # standard + Harbor
```

On Windows: `pwsh deploy/install.ps1 -Profile lite`.

### Containers pillar: Harbor (production) vs standalone Trivy (fallback)

The **Containers** pillar has two paths. Pick based on whether you have a registry:

| | **Harbor + trivy-adapter** (main path) | **Standalone Trivy** (`deploy/scan-target.sh --image …`) |
|---|---|---|
| What it scans | The images you **actually push to the registry and deploy** | An image you name by hand, on your machine |
| When to use it | You have (or will have) a registry and deploy from it | You have **no** registry, or want a day-one score |
| Profile | `full` | `lite` and `standard` (already included) |
| Cost | **~10 containers** (core, portal, registry, jobservice, db, redis, proxy, trivy-adapter…), ~6 GB+ RAM overall, and **its own installer**: `deploy/harbor/setup-harbor.sh` (not a service of the panel's compose file) | Almost none: one ephemeral `aquasec/trivy` container |
| Re-scanning | Harbor's trivy-adapter rescans on image push | You run it each time |

**Recommendation:** start with `lite` + standalone Trivy (week 1, see
[docs/PRIMEROS-30-DIAS.md](docs/PRIMEROS-30-DIAS.md)) and move to **Harbor from month
2 on**, once you run the panel comfortably and your deploy flow goes through a
registry. Harbor is the production flow; standalone Trivy is the fallback.

```bash
bash deploy/install.sh --yes --profile full      # standard + offers to install Harbor
bash deploy/harbor/setup-harbor.sh               # or install/retry Harbor separately
bash deploy/provision-sources.sh                 # wires Harbor into the panel
```

Before installing Harbor, set `HARBOR_ADMIN_PASSWORD` in `deploy/.env` (the installer
does not generate it and `setup-harbor.sh` requires it). Harbor details (port `8085`, pinned version, its own `docker compose down`):
[`deploy/harbor/README.md`](deploy/harbor/README.md).

### What the installer does

1. Checks Docker + Compose.
2. Generates `deploy/.env` with random secrets (**never overwrites an existing one**).
3. Seeds `config/projects.yaml` from the template.
4. `docker compose up -d --build` for the whole suite.
5. Waits for the backend to be healthy (auto-migrates + creates admin).
6. (Optional) Issues tokens and wires the demo sources (`provision-sources.sh`).
7. Prints access details and next steps.

### First login (hardened flow)

1. Open **http://localhost:8088** and sign in as `admin` with the password the
   installer printed (it's in `deploy/.env`, `WOLF_ADMIN_PASSWORD`).
2. The system **forces a password change**.
3. As an admin, it **requires MFA enrollment**: scan the QR with your TOTP app
   (Google Authenticator, Aegis…) and confirm the code. Until MFA is enrolled, the
   admin area returns 403 (by design).
4. Then: **Admin → Integrations** to connect GitHub/SonarQube/DefectDojo/Harbor/
   Trivy and hit *“Test connection”*.

### Ports

| Service | URL |
|---|---|
| Wolf panel | http://localhost:8088 |
| SonarQube | http://localhost:9000 |
| DefectDojo | http://localhost:8086 |
| Harbor (optional) | http://localhost:8085 |

### Operations

```bash
# Tail backend logs
docker compose -f deploy/docker-compose.install.yml logs -f backend
# Stop (keeps data)
docker compose -f deploy/docker-compose.install.yml down
# Wipe EVERYTHING, data included
docker compose -f deploy/docker-compose.install.yml down -v
```

**Backup, restore, upgrades and key rotation:** see
[docs/RUNBOOK.md](docs/RUNBOOK.md) (`deploy/backup.sh`, `deploy/restore.sh`).
**First time, and on your own?** Follow [docs/PRIMEROS-30-DIAS.md](docs/PRIMEROS-30-DIAS.md).

### Already have your own Sonar / DefectDojo / Harbor?

Don't start the bundled one: in **Admin → Integrations** (or in
`config/projects.yaml`) point that source's `base_url` at yours and put its token
in `deploy/.env`.

> For Kubernetes, see [`deploy/DEPLOY.md`](deploy/DEPLOY.md).
