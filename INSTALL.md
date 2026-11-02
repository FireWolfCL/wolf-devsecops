<div align="center">

# 🐺 Wolf DevSecOps — Instalación / Installation

**Español** · [English](#english)

</div>

---

## Español

### Requisitos

- **Docker Desktop** (o Docker Engine) con **Docker Compose v2** (`docker compose`).
- **~4 GB de RAM libres** en Docker (SonarQube solo ya pide 2–3 GB). Súbelo en
  *Docker Desktop → Settings → Resources* si hace falta.
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
bash deploy/install.sh --yes --lang es
```

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

### ¿Ya tienes tu propio Sonar / DefectDojo / Harbor?

No levantes el del stack: en **Admin → Integraciones** (o en `config/projects.yaml`)
cambia el `base_url` de esa fuente por el tuyo y pon su token en `deploy/.env`.

> Para Kubernetes, ver [`deploy/DEPLOY.md`](deploy/DEPLOY.md).

---

## English

### Requirements

- **Docker Desktop** (or Docker Engine) with **Docker Compose v2** (`docker compose`).
- **~4 GB free RAM** in Docker (SonarQube alone needs 2–3 GB). Raise it under
  *Docker Desktop → Settings → Resources* if needed.
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
bash deploy/install.sh --yes --lang en
```

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

### Already have your own Sonar / DefectDojo / Harbor?

Don't start the bundled one: in **Admin → Integrations** (or in
`config/projects.yaml`) point that source's `base_url` at yours and put its token
in `deploy/.env`.

> For Kubernetes, see [`deploy/DEPLOY.md`](deploy/DEPLOY.md).
