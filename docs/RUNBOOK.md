# Runbook de operación para administrador único / Single-admin operations runbook

> **ES** — Cómo operar el stack de Wolf DevSecOps cuando eres la única persona a
> cargo: actualizar, respaldar, restaurar, rotar claves, recuperarte de una caída
> y rehacer tokens. Todos los comandos asumen el perfil `standard`
> (`deploy/docker-compose.install.yml`); para `lite`, cambia el archivo por
> `deploy/docker-compose.lite.yml` (no tiene DefectDojo: ignora esas partes).
>
> **EN** — How to operate the Wolf DevSecOps stack when you are the only person in
> charge: upgrade, back up, restore, rotate keys, recover from outages and redo
> tokens. Commands assume the `standard` profile
> (`deploy/docker-compose.install.yml`); for `lite`, use
> `deploy/docker-compose.lite.yml` (no DefectDojo: skip those parts).

Convención / Convention — corre desde la raíz del repo / run from the repo root:

```bash
export WOLF_COMPOSE=deploy/docker-compose.install.yml
alias wdc='docker compose -f $WOLF_COMPOSE'     # opcional / optional
```

**Qué hay en el stack / What is in the stack** (volúmenes reales / real volumes):

| Servicio / Service | Dato persistente / Persistent data | Respaldo / Backup |
|---|---|---|
| `db` (Postgres 16.4, BD del panel `wolf_devsecops`) | volumen `wolf_db` | `pg_dump` (backup.sh) |
| `sonar-db` (Postgres 16.4, BD `sonar`) | volumen `sonar_db` | `pg_dump` |
| `sonarqube` | `sonarqube_data`, `sonarqube_ext` | `--volumes` (opcional) |
| `defectdojo-db` (Postgres 17, BD `defectdojo`) | volumen `dd_db` | `pg_dump` |
| `defectdojo-redis` | `dd_redis` (cola de Celery) | no — regenerable |
| `trivy` / `trivy-scan` | `trivy_reports`, `trivy_cache` | no — regenerables |
| `deploy/.env`, `config/projects.yaml` | archivos en el host / host files | incluidos en el tarball / in the tarball |

---

## Español

### 0. Antes de tocar nada: la regla de oro

1. **Respaldo primero**, siempre: `bash deploy/backup.sh --volumes` (sección 2).
2. Cambia **una cosa a la vez** y verifica con `bash deploy/smoke.sh --compose $WOLF_COMPOSE`.
3. Anota qué versión tenías (tags de imagen en el compose) para poder volver atrás.

### 1. Actualizar SonarQube y DefectDojo

Los tags de imagen están fijados en el compose (`sonarqube:10.7-community`,
`defectdojo/defectdojo-django:2.40.3` y `defectdojo/defectdojo-nginx:2.40.3`). **No
uses `latest`**: sube el tag a propósito.

**Orden recomendado:** (1) respaldo → (2) SonarQube → (3) verificar → (4) DefectDojo →
(5) verificar → (6) el panel. Nunca los dos a la vez: si algo falla no sabrás cuál.

**SonarQube**

1. Lee las notas de la versión destino y su ruta de actualización oficial (SonarQube
   no permite saltar versiones arbitrariamente; a veces exige pasar por una LTA).
2. `bash deploy/backup.sh --volumes` (incluye `sonar-db` y los volúmenes de SonarQube).
3. Edita el tag en `deploy/docker-compose.install.yml` (servicio `sonarqube`).
4. ```bash
   docker compose -f $WOLF_COMPOSE pull sonarqube
   docker compose -f $WOLF_COMPOSE up -d sonarqube
   docker compose -f $WOLF_COMPOSE logs -f sonarqube
   ```
5. **Migración de esquema:** SonarQube no migra solo. Abre
   `http://localhost:9000/setup` y pulsa *Upgrade*; espera a que termine.
6. Verifica: `http://localhost:9000` abre, el panel muestra Sonar en verde
   (Admin → Integraciones → *Probar conexión*). Si el token fue rechazado, ver sección 6.

**DefectDojo**

1. Lee las release notes de la versión destino (DefectDojo documenta saltos y
   migraciones que requieren atención). Sube de a una versión menor si hay dudas.
2. `bash deploy/backup.sh` (incluye `defectdojo-db`).
3. Cambia el **mismo** tag en los 5 servicios que lo usan (`defectdojo-initializer`,
   `-uwsgi`, `-celeryworker`, `-celerybeat` y `defectdojo-nginx`; los cuatro primeros
   usan `defectdojo-django`, nginx usa `defectdojo-nginx`). Todos deben quedar en la
   misma versión.
4. ```bash
   docker compose -f $WOLF_COMPOSE pull defectdojo-initializer defectdojo-uwsgi \
       defectdojo-celeryworker defectdojo-celerybeat defectdojo-nginx
   docker compose -f $WOLF_COMPOSE up -d
   docker compose -f $WOLF_COMPOSE logs -f defectdojo-initializer
   ```
5. **Migraciones:** las corre `defectdojo-initializer` al arrancar (`/entrypoint-initializer.sh`).
   Espera a que termine (`docker compose -f $WOLF_COMPOSE ps -a defectdojo-initializer`
   debe quedar `exited (0)`). Si sale con error, **no sigas**: restaura (sección 3).
6. Verifica en `http://localhost:8086` y con *Probar conexión* en el panel.

**Panel Wolf** (`backend`, `scheduler`, `bot`, `frontend`): `git pull` →
`docker compose -f $WOLF_COMPOSE up -d --build`. El backend migra su propia BD al
arrancar (el `scheduler` y el `bot` no migran, para evitar carreras).

**No cambies la versión mayor de Postgres** de `db`, `sonar-db` o `defectdojo-db` solo
editando el tag: el volumen existente deja de ser compatible. Una subida mayor es
*dump → volumen nuevo → restore*.

**Rollback:** vuelve el tag anterior, y si la migración ya corrió, restaura el
respaldo previo (sección 3). Una migración aplicada no se deshace sola.

### 2. Backup

```bash
bash deploy/backup.sh                       # BD de los 3 Postgres + deploy/.env + config/
bash deploy/backup.sh --volumes             # + sonarqube_data/sonarqube_ext (para SonarQube unos segundos)
bash deploy/backup.sh --out /mnt/respaldos  # destino (default ./backups)
bash deploy/backup.sh --compose deploy/docker-compose.lite.yml   # perfil lite
```

- Requiere el stack **levantado**. Hace `pg_dump` dentro de los contenedores
  `db`, `sonar-db` y `defectdojo-db`; las contraseñas se leen dentro del contenedor y
  **no se imprimen**.
- Produce `wolf-backup-YYYYmmdd-HHMM.tgz` (permisos 600). **Contiene `deploy/.env`
  (secretos, incluida `WOLF_MFA_FERNET_KEY`)**: guárdalo cifrado y fuera del servidor.
  Sin esa clave, los secretos MFA y los tokens guardados en la BD son irrecuperables.
- Si el backend usara SQLite (`WOLF_DATABASE_URL` sin definir; **no es el caso** del
  deploy de Compose, que usa el servicio `db`), el script avisa y debes respaldar ese
  archivo/volumen aparte.
- **Frecuencia sugerida:** diario (cron) y siempre antes de actualizar o rotar claves.
  Ejemplo: `0 3 * * * cd /ruta/wolf-devsecops && bash deploy/backup.sh --out /mnt/respaldos`.
- **Prueba la restauración** al menos una vez al trimestre en otra máquina. Un respaldo
  que nunca restauraste es una suposición.

### 3. Restore

```bash
docker compose -f $WOLF_COMPOSE up -d            # el stack debe estar levantado
bash deploy/restore.sh backups/wolf-backup-20261104-1530.tgz
```

- Pregunta **y/N** y **sobrescribe** `db`, `sonar-db`, `defectdojo-db` (y los volúmenes
  de SonarQube si el tarball los trae). Sin terminal interactiva se niega.
- Detiene `backend`, `scheduler`, `bot`, `sonarqube` y los servicios de DefectDojo
  mientras restaura, y los vuelve a arrancar.
- **No pisa** `deploy/.env` ni `config/`: si difieren, deja `*.from-backup` para que
  compares. Si `WOLF_MFA_FERNET_KEY` actual ≠ la del respaldo, los secretos cifrados
  restaurados **no se podrán descifrar** (ver sección 4).
- **Host nuevo:** clona el repo, copia `deploy/.env` desde el tarball (`tar xzf … wolf-backup-*/deploy.env`,
  renómbralo a `deploy/.env` y `chmod 600`), levanta el stack y luego restaura.
- Verifica: `bash deploy/smoke.sh --compose $WOLF_COMPOSE`.

### 4. Rotar `WOLF_MFA_FERNET_KEY`

`WOLF_MFA_FERNET_KEY` cifra (Fernet) tres cosas en la BD del panel:

| Qué | Dónde |
|---|---|
| Secreto MFA (TOTP) de cada usuario | `users.mfa_secret` |
| Tokens de fuente guardados desde el panel | `source_config.token_cipher` |
| Contraseña de bind LDAP | `ldap_config.bind_password_cipher` (modelo `LDAPConfigRow`) |

> **Importante:** el producto **no re-cifra** lo ya guardado al cambiar la clave (no
> hay herramienta de rotación; la clave nueva reemplaza a la vieja). Tras rotar, lo
> cifrado con la clave anterior **queda ilegible** y **hay que volver a guardar cada
> secreto**. Rota solo si la clave se filtró o por política; no es una operación de rutina.
> Mientras no re-guardes, esas fuentes/LDAP/MFA fallan.

Procedimiento:

1. `bash deploy/backup.sh` (el tarball guarda la clave **vieja** en `deploy.env`: es tu rollback).
2. Antes de rotar, **ten un segundo acceso admin listo** o ventana de mantenimiento:
   los usuarios con MFA no podrán validar su TOTP con la clave nueva.
3. Genera la clave nueva (mismo método que el instalador) y escríbela en `deploy/.env`
   sin imprimirla:
   ```bash
   NEW="$(openssl rand -base64 32 | tr '+/' '-_')"
   cp deploy/.env deploy/.env.pre-rotacion && chmod 600 deploy/.env.pre-rotacion
   sed -i.bak "s|^WOLF_MFA_FERNET_KEY=.*|WOLF_MFA_FERNET_KEY=${NEW}|" deploy/.env && rm deploy/.env.bak
   unset NEW
   ```
   (`deploy/.env.pre-rotacion` contiene la clave vieja: bórralo cuando termines.)
4. Recrea los procesos que la leen: `docker compose -f $WOLF_COMPOSE up -d backend scheduler bot`.
5. **Re-guarda los secretos:**
   - **Tokens de fuente:** panel → Admin → Integraciones → pega de nuevo el token de
     cada fuente (GitHub, SonarQube, DefectDojo, Harbor, Trivy) y *Probar conexión*.
     Si prefieres el entorno (`deploy/.env`), ver sección 6.
   - **LDAP:** vuelve a escribir la contraseña de bind en la configuración LDAP del
     área Admin. (Si no usas LDAP, omite.)
   - **MFA:** cada usuario con MFA debe **re-enrolar** su TOTP. Si tu propio admin queda
     bloqueado por MFA ilegible, crea otro admin por consola
     (`docker compose -f $WOLF_COMPOSE exec backend wolf-devsecops create-admin <usuario> --allow-existing`,
     pide la contraseña por stdin oculto) y enrola su MFA en el primer login.
     `[PENDING VERIFICATION]` cómo un admin resetea el MFA *de otro usuario* (solo
     verifiqué `POST /me/mfa/disable`, que actúa sobre el usuario autenticado).
6. Verifica `smoke.sh` y que las fuentes salgan en verde.
7. **Rollback:** restaura `deploy/.env.pre-rotacion` → `deploy/.env` y repite el paso 4:
   todo lo cifrado con la clave vieja vuelve a ser legible. Lo que re-guardaste con la
   clave nueva habrá que re-guardarlo otra vez.
8. Borra `deploy/.env.pre-rotacion` y los tarballs viejos que guardan la clave vieja si
   la rotación fue por filtración.

La clave `WOLF_JWT_SECRET` es **distinta** y de propósito único: rotarla solo invalida
las sesiones activas (todos tendrán que iniciar sesión de nuevo).

### 5. Si cae un servicio

Primero siempre: `docker compose -f $WOLF_COMPOSE ps -a` y
`docker compose -f $WOLF_COMPOSE logs --tail=100 <servicio>`.

| Servicio | Qué se nota | Qué hacer |
|---|---|---|
| `db` | `/api/health` falla; el panel no carga datos; backend/scheduler/bot en reinicio | `up -d db`; mira logs (disco lleno, permisos del volumen). Si el volumen está corrupto → restore (sección 3). El backend espera a `db` sano (healthcheck `pg_isready`). |
| `backend` | El panel carga pero sin datos / `/api/health` caído | `logs backend`. Causa típica: falta una variable obligatoria en `deploy/.env` (el compose aborta con "falta WOLF_…"). `up -d backend`. |
| `frontend` | `http://localhost:8088` no responde (el backend sí) | `up -d --build frontend`. Puerto 8088 ocupado: cámbialo en el compose. |
| `scheduler` | Dejan de generarse snapshots de postura/tendencia y notificaciones; el panel sigue andando | `logs scheduler`; `up -d scheduler`. No tiene healthcheck HTTP (es un worker). |
| `bot` | El bot de Telegram no responde | Sale limpio si no hay `WOLF_TELEGRAM_BOT_TOKEN` (es normal). Si lo hay: `logs bot`, `up -d bot`. |
| `sonarqube` | Fuente Sonar no alcanzable | Suele ser RAM (necesita ~2–3 GB libres en Docker) o `vm.max_map_count` en Linux. `logs sonarqube`; `up -d sonarqube`. Tarda minutos en estar listo. |
| `sonar-db` | SonarQube cae junto con ella | `up -d sonar-db` y luego `restart sonarqube`. Volumen dañado → restore. |
| `defectdojo-db` / `defectdojo-redis` | DefectDojo no responde / no procesa imports | `up -d defectdojo-db defectdojo-redis` y luego `restart defectdojo-uwsgi defectdojo-celeryworker defectdojo-celerybeat`. Redis solo guarda la cola: perderlo no pierde hallazgos. |
| `defectdojo-uwsgi` / `-nginx` | `http://localhost:8086` da 502/no abre | `up -d defectdojo-uwsgi defectdojo-nginx`; `logs`. Emulación amd64 en Apple Silicon: arranque lento, es normal. |
| `defectdojo-celeryworker` / `-celerybeat` | Imports/dedup no avanzan | `restart` de ambos. |
| `trivy` (Nginx de reportes) | Contenedores sin datos de Trivy | `up -d trivy`. El reporte vuelve con `bash deploy/scan-target.sh --image <img>`. |
| Harbor (stack aparte) | Pilar Contenedores sin datos de registry | `cd deploy/harbor/harbor/harbor && docker compose up -d` (ver `deploy/harbor/README.md`). |

**Reinicio completo del host:** los servicios tienen `restart: unless-stopped`; tras
reiniciar Docker deberían volver solos. Si no: `docker compose -f $WOLF_COMPOSE up -d`.
**Nunca** uses `down -v` para "arreglar" algo: borra todos los volúmenes.

### 6. Rehacer tokens de fuente

Tienes dos lugares; **el valor guardado desde el panel (BD cifrada) tiene precedencia
sobre `deploy/.env`**. Si cambias el token en `.env` pero hay uno guardado en el panel,
seguirá usándose el del panel: vuelve a guardarlo ahí.

- **SonarQube y DefectDojo (generados por el instalador):**
  ```bash
  bash deploy/provision-sources.sh
  ```
  Es idempotente: espera a las fuentes, **revoca y vuelve a emitir** el token de
  SonarQube, emite el de DefectDojo, los escribe en `deploy/.env` y reinicia el
  backend. Después, *Probar conexión* en el panel.
- **GitHub (SaaS):** crea el token en GitHub con permisos **de solo lectura**, y
  guárdalo en Admin → Integraciones (o `GITHUB_TOKEN` en `deploy/.env`). **Revoca el
  anterior en GitHub.** Hay aviso de vencimiento en el panel cuando el proveedor lo informa.
- **Harbor:** `HARBOR_ADMIN_PASSWORD` debe estar definida en `deploy/.env` (el
  instalador no la genera; `setup-harbor.sh` la exige); `provision-sources.sh` la usa
  para cablear el token de Harbor.
- **Trivy:** `TRIVY_TOKEN` en `deploy/.env` (vacío por defecto) o en el panel.
- Tras editar `deploy/.env`: `docker compose -f $WOLF_COMPOSE up -d backend scheduler bot`
  (recrea con el entorno nuevo).
- Si un token se filtró: **primero revócalo en la fuente**, luego rehaz el token aquí.

---

## English

### 0. Before touching anything: the golden rule

1. **Back up first**, always: `bash deploy/backup.sh --volumes` (section 2).
2. Change **one thing at a time** and verify with `bash deploy/smoke.sh --compose $WOLF_COMPOSE`.
3. Note the version you were on (image tags in the compose file) so you can go back.

### 1. Upgrading SonarQube and DefectDojo

Image tags are pinned in the compose file (`sonarqube:10.7-community`,
`defectdojo/defectdojo-django:2.40.3` and `defectdojo/defectdojo-nginx:2.40.3`).
**Never use `latest`**: bump the tag deliberately.

**Recommended order:** (1) backup → (2) SonarQube → (3) verify → (4) DefectDojo →
(5) verify → (6) the panel. Never both at once: if something breaks you won't know which.

**SonarQube**

1. Read the target version's release notes and its official upgrade path (SonarQube
   does not allow arbitrary version jumps; sometimes you must pass through an LTA).
2. `bash deploy/backup.sh --volumes` (includes `sonar-db` and the SonarQube volumes).
3. Edit the tag in `deploy/docker-compose.install.yml` (service `sonarqube`).
4. ```bash
   docker compose -f $WOLF_COMPOSE pull sonarqube
   docker compose -f $WOLF_COMPOSE up -d sonarqube
   docker compose -f $WOLF_COMPOSE logs -f sonarqube
   ```
5. **Schema migration:** SonarQube does not migrate on its own. Open
   `http://localhost:9000/setup`, press *Upgrade*, and wait for it to finish.
6. Verify: `http://localhost:9000` opens, the panel shows Sonar green
   (Admin → Integrations → *Test connection*). If the token is rejected, see section 6.

**DefectDojo**

1. Read the target version's release notes (DefectDojo documents jumps and
   migrations that need attention). Go one minor version at a time if unsure.
2. `bash deploy/backup.sh` (includes `defectdojo-db`).
3. Change the **same** tag in all 5 services that use it (`defectdojo-initializer`,
   `-uwsgi`, `-celeryworker`, `-celerybeat` use `defectdojo-django`; `defectdojo-nginx`
   uses `defectdojo-nginx`). All must end up on the same version.
4. ```bash
   docker compose -f $WOLF_COMPOSE pull defectdojo-initializer defectdojo-uwsgi \
       defectdojo-celeryworker defectdojo-celerybeat defectdojo-nginx
   docker compose -f $WOLF_COMPOSE up -d
   docker compose -f $WOLF_COMPOSE logs -f defectdojo-initializer
   ```
5. **Migrations:** run by `defectdojo-initializer` on start (`/entrypoint-initializer.sh`).
   Wait until it ends (`docker compose -f $WOLF_COMPOSE ps -a defectdojo-initializer`
   should show `exited (0)`). If it fails, **stop**: restore (section 3).
6. Verify at `http://localhost:8086` and with *Test connection* in the panel.

**Wolf panel** (`backend`, `scheduler`, `bot`, `frontend`): `git pull` →
`docker compose -f $WOLF_COMPOSE up -d --build`. The backend migrates its own DB on
start (`scheduler` and `bot` do not, to avoid races).

**Do not change the Postgres major version** of `db`, `sonar-db` or `defectdojo-db` by
editing the tag alone: the existing volume stops being compatible. A major bump is
*dump → fresh volume → restore*.

**Rollback:** put the previous tag back and, if the migration already ran, restore the
previous backup (section 3). An applied migration does not undo itself.

### 2. Backup

```bash
bash deploy/backup.sh                       # DBs of the 3 Postgres + deploy/.env + config/
bash deploy/backup.sh --volumes             # + sonarqube_data/sonarqube_ext (stops SonarQube a few seconds)
bash deploy/backup.sh --out /mnt/backups    # destination (default ./backups)
bash deploy/backup.sh --compose deploy/docker-compose.lite.yml   # lite profile
```

- Requires the stack **up**. Runs `pg_dump` inside `db`, `sonar-db` and
  `defectdojo-db`; passwords are read inside the container and **never printed**.
- Produces `wolf-backup-YYYYmmdd-HHMM.tgz` (mode 600). **It contains `deploy/.env`
  (secrets, including `WOLF_MFA_FERNET_KEY`)**: store it encrypted and off-host.
  Without that key, the MFA secrets and tokens stored in the DB are unrecoverable.
- If the backend used SQLite (`WOLF_DATABASE_URL` unset; **not** the Compose deploy,
  which uses the `db` service), the script warns and you must back up that file/volume
  separately.
- **Suggested frequency:** daily (cron) and always before upgrading or rotating keys.
  Example: `0 3 * * * cd /path/wolf-devsecops && bash deploy/backup.sh --out /mnt/backups`.
- **Test the restore** at least quarterly on another machine. A backup you never
  restored is an assumption.

### 3. Restore

```bash
docker compose -f $WOLF_COMPOSE up -d            # the stack must be up
bash deploy/restore.sh backups/wolf-backup-20261104-1530.tgz
```

- Asks **y/N** and **overwrites** `db`, `sonar-db`, `defectdojo-db` (and the SonarQube
  volumes if the tarball has them). Refuses without an interactive terminal.
- Stops `backend`, `scheduler`, `bot`, `sonarqube` and the DefectDojo services while
  restoring, then starts them again.
- It does **not** overwrite `deploy/.env` or `config/`: if they differ it leaves
  `*.from-backup` for you to compare. If the current `WOLF_MFA_FERNET_KEY` ≠ the backed-up
  one, restored encrypted secrets **cannot be decrypted** (see section 4).
- **New host:** clone the repo, copy `deploy/.env` out of the tarball
  (`tar xzf … wolf-backup-*/deploy.env`, rename to `deploy/.env`, `chmod 600`), bring the
  stack up, then restore.
- Verify: `bash deploy/smoke.sh --compose $WOLF_COMPOSE`.

### 4. Rotating `WOLF_MFA_FERNET_KEY`

`WOLF_MFA_FERNET_KEY` encrypts (Fernet) three things in the panel DB:

| What | Where |
|---|---|
| Each user's MFA (TOTP) secret | `users.mfa_secret` |
| Source tokens saved from the panel | `source_config.token_cipher` |
| LDAP bind password | `ldap_config.bind_password_cipher` (model `LDAPConfigRow`) |

> **Important:** the product does **not re-encrypt** stored data when the key changes
> (there is no rotation tool; the new key replaces the old one). After rotating,
> anything encrypted with the old key becomes **unreadable** and **you must re-save every
> secret**. Rotate only if the key leaked or by policy; it is not a routine operation.
> Until you re-save them, those sources/LDAP/MFA fail.

Procedure:

1. `bash deploy/backup.sh` (the tarball keeps the **old** key in `deploy.env`: your rollback).
2. Before rotating, **have a second admin access ready** or a maintenance window:
   users with MFA won't be able to validate their TOTP with the new key.
3. Generate the new key (same method as the installer) and write it to `deploy/.env`
   without printing it:
   ```bash
   NEW="$(openssl rand -base64 32 | tr '+/' '-_')"
   cp deploy/.env deploy/.env.pre-rotation && chmod 600 deploy/.env.pre-rotation
   sed -i.bak "s|^WOLF_MFA_FERNET_KEY=.*|WOLF_MFA_FERNET_KEY=${NEW}|" deploy/.env && rm deploy/.env.bak
   unset NEW
   ```
   (`deploy/.env.pre-rotation` holds the old key: delete it when done.)
4. Recreate the processes that read it: `docker compose -f $WOLF_COMPOSE up -d backend scheduler bot`.
5. **Re-save the secrets:**
   - **Source tokens:** panel → Admin → Integrations → paste each source's token again
     (GitHub, SonarQube, DefectDojo, Harbor, Trivy) and *Test connection*. If you prefer
     the environment (`deploy/.env`), see section 6.
   - **LDAP:** re-enter the bind password in the Admin LDAP settings. (Skip if unused.)
   - **MFA:** every MFA user must **re-enroll** their TOTP. If your own admin is locked
     out by unreadable MFA, create another admin from the console
     (`docker compose -f $WOLF_COMPOSE exec backend wolf-devsecops create-admin <username> --allow-existing`,
     prompts for the password via hidden stdin) and enroll its MFA on first login.
     `[PENDING VERIFICATION]` how an admin resets *another user's* MFA (I only verified
     `POST /me/mfa/disable`, which acts on the authenticated user).
6. Verify with `smoke.sh` and check the sources are green.
7. **Rollback:** restore `deploy/.env.pre-rotation` → `deploy/.env` and repeat step 4:
   everything encrypted with the old key is readable again. Whatever you re-saved with the
   new key will need re-saving once more.
8. Delete `deploy/.env.pre-rotation` and old tarballs holding the old key if the
   rotation was due to a leak.

`WOLF_JWT_SECRET` is a **different**, single-purpose key: rotating it only invalidates
active sessions (everyone logs in again).

### 5. When a service goes down

Always start with: `docker compose -f $WOLF_COMPOSE ps -a` and
`docker compose -f $WOLF_COMPOSE logs --tail=100 <service>`.

| Service | What you notice | What to do |
|---|---|---|
| `db` | `/api/health` fails; panel loads no data; backend/scheduler/bot restart-looping | `up -d db`; check logs (disk full, volume permissions). Corrupt volume → restore (section 3). Backend waits for a healthy `db` (`pg_isready` healthcheck). |
| `backend` | Panel loads but empty / `/api/health` down | `logs backend`. Typical cause: a mandatory variable missing in `deploy/.env` (compose aborts with "falta WOLF_…"). `up -d backend`. |
| `frontend` | `http://localhost:8088` unreachable (backend OK) | `up -d --build frontend`. Port 8088 taken: change it in the compose file. |
| `scheduler` | Posture snapshots/trend and notifications stop; panel keeps working | `logs scheduler`; `up -d scheduler`. No HTTP healthcheck (it is a worker). |
| `bot` | Telegram bot silent | Exits cleanly without `WOLF_TELEGRAM_BOT_TOKEN` (normal). If set: `logs bot`, `up -d bot`. |
| `sonarqube` | Sonar source unreachable | Usually RAM (needs ~2–3 GB free in Docker) or `vm.max_map_count` on Linux. `logs sonarqube`; `up -d sonarqube`. Takes minutes to be ready. |
| `sonar-db` | SonarQube goes down with it | `up -d sonar-db`, then `restart sonarqube`. Damaged volume → restore. |
| `defectdojo-db` / `defectdojo-redis` | DefectDojo down / imports not processed | `up -d defectdojo-db defectdojo-redis`, then `restart defectdojo-uwsgi defectdojo-celeryworker defectdojo-celerybeat`. Redis only holds the queue: losing it loses no findings. |
| `defectdojo-uwsgi` / `-nginx` | `http://localhost:8086` gives 502/won't open | `up -d defectdojo-uwsgi defectdojo-nginx`; `logs`. amd64 emulation on Apple Silicon: slow start is normal. |
| `defectdojo-celeryworker` / `-celerybeat` | Imports/dedup stall | `restart` both. |
| `trivy` (reports Nginx) | Containers pillar has no Trivy data | `up -d trivy`. Rebuild the report with `bash deploy/scan-target.sh --image <img>`. |
| Harbor (separate stack) | Containers pillar has no registry data | `cd deploy/harbor/harbor/harbor && docker compose up -d` (see `deploy/harbor/README.md`). |

**Host reboot:** services use `restart: unless-stopped`; they should come back after
Docker restarts. If not: `docker compose -f $WOLF_COMPOSE up -d`.
**Never** use `down -v` to "fix" something: it deletes every volume.

### 6. Redoing source tokens

There are two places; **a value saved from the panel (encrypted in the DB) takes
precedence over `deploy/.env`**. If you change the token in `.env` but one is saved in
the panel, the panel's keeps being used: re-save it there.

- **SonarQube and DefectDojo (generated by the installer):**
  ```bash
  bash deploy/provision-sources.sh
  ```
  Idempotent: waits for the sources, **revokes and re-issues** the SonarQube token,
  issues the DefectDojo one, writes them to `deploy/.env` and restarts the backend.
  Then *Test connection* in the panel.
- **GitHub (SaaS):** create the token on GitHub with **read-only** permissions and save
  it in Admin → Integrations (or `GITHUB_TOKEN` in `deploy/.env`). **Revoke the old one
  on GitHub.** The panel shows an expiry reminder when the provider reports one.
- **Harbor:** `HARBOR_ADMIN_PASSWORD` must be set in `deploy/.env` (the installer does
  not generate it; `setup-harbor.sh` requires it); `provision-sources.sh` uses it to
  wire the Harbor token.
- **Trivy:** `TRIVY_TOKEN` in `deploy/.env` (empty by default) or in the panel.
- After editing `deploy/.env`: `docker compose -f $WOLF_COMPOSE up -d backend scheduler bot`
  (recreates with the new environment).
- If a token leaked: **revoke it at the source first**, then redo it here.
