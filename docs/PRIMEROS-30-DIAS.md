# Primeros 30 días para el DevSecOps que está solo / First 30 days for the lone DevSecOps

> **ES** — Plan de cuatro semanas para quien es la única persona a cargo de la
> seguridad del código. Una cosa por semana, cada una con algo concreto que mostrar
> a gerencia. Sin marketing: lo que haces, el comando, y cuándo lo das por terminado.
>
> **EN** — A four-week plan for the one person in charge of code security. One thing
> per week, each with something concrete to show management. No marketing: what you
> do, the command, and when it counts as done.

Recuerda / Remember: Wolf DevSecOps **lee** GitHub, SonarQube, DefectDojo, Trivy y
Harbor; no es un escáner (ADR-001). Los escáneres corren en tu CI o, para arrancar,
en el bundle (`deploy/scan-target.sh`). / It **reads** those tools; it is not a scanner.
Scanners run in your CI or, to get started, in the bundle (`deploy/scan-target.sh`).

---

## Español

### Semana 1 — Instalar en modo `lite` y conectar el repo principal

**Objetivo:** tener un puntaje real de **un** repo, no una demo.

1. Instala (≈2 GB de RAM; sin DefectDojo todavía):
   ```bash
   git clone https://github.com/FireWolfCL/wolf-devsecops.git
   cd wolf-devsecops
   bash deploy/install.sh --profile lite
   ```
2. En el prompt *"¿Qué quieres analizar?"* pon la ruta de tu repo principal. Si lo
   saltaste (o usaste `--yes`), hazlo después:
   ```bash
   bash deploy/scan-target.sh --repo <ruta-a-tu-repo> --org <tu-equipo>
   ```
   Corre Trivy (dependencias/config) y SonarQube sobre tu código, montado solo-lectura.
   Para buscar secretos en **todo el historial git** agrega `--gitleaks`.
3. Primer login: abre `http://localhost:8088`, cambia la contraseña y enrola MFA
   (ver [INSTALL.md](../INSTALL.md)).

**Hecho cuando:** tu repo aparece en el panel con un score y las fuentes SonarQube y
Trivy están en verde.

**Qué mostrarle a gerencia:** una captura del panel con el puntaje de **su** repo, y la
frase "ya sabemos dónde estamos parados; no es una demo".

### Semana 2 — Leer el score y atacar las 3 acciones que más suben el puntaje

**Objetivo:** entender el número y mejorarlo con poco esfuerzo.

1. Abre el detalle de tu proyecto. La tarjeta **"Las 3 acciones que más suben tu
   puntaje"** ordena qué checks, al corregirse, aportan más.
2. Para cómo se calcula el puntaje, qué pesa cada check y qué significa cada color:
   [POSTURE-RUBRIC.md](POSTURE-RUBRIC.md).
3. Haz esas 3 acciones (o descarta con motivo las que no apliquen). Re-escanea para ver
   el cambio:
   ```bash
   bash deploy/scan-target.sh --repo <ruta-a-tu-repo>
   ```
4. Anota el score antes y después. La tendencia queda en el panel.

**Hecho cuando:** completaste (o justificaste) las 3 acciones y el score subió, o sabes
exactamente por qué no.

**Qué mostrarle a gerencia:** "antes → después" del puntaje, con las 3 acciones hechas
y su costo en horas.

### Semana 3 — Meter Trivy en el CI del equipo

**Objetivo:** que el escaneo deje de depender de tu laptop. Cada push/PR se escanea solo.

1. Lee [PIPELINES.md](PIPELINES.md) (el patrón: escáner en el CI → `reimport-scan` a
   DefectDojo → el panel lo lee).
2. Copia el workflow que corresponda desde [`deploy/ci-examples/github/`](../deploy/ci-examples/github/)
   (hay uno por categoría: Gitleaks, Semgrep, Grype, Checkov, ZAP; ver también
   [`deploy/ci-examples/README.md`](../deploy/ci-examples/README.md)). Prioridad sugerida:
   **Gitleaks → Grype/Syft → Semgrep → Checkov → ZAP**.
3. Para el pilar Contenedores: Trivy sobre la imagen en el CI. Sube el reporte a
   DefectDojo con `reimport-scan` (`scan_type=Trivy Scan`, ver tabla en PIPELINES.md).
   Tu CI necesita DefectDojo accesible: esto conecta con la semana 4; si aún no lo
   tienes arriba, deja el job escaneando y subiendo el reporte como artefacto del CI y
   conéctalo al activar DefectDojo.
4. Los jobs **no deben romper el build** por hallazgos al principio: primero medir, luego
   poner una política de release.

**Hecho cuando:** un PR de prueba dispara el escaneo en el CI y el reporte queda
disponible (como artefacto o en DefectDojo).

**Qué mostrarle a gerencia:** "el 100 % de los PRs ahora se escanea sin intervención
humana", con el enlace al run del CI.

### Semana 4 — Activar DefectDojo (perfil `standard`) y hacer el primer triage

**Objetivo:** pasar de "una lista de hallazgos" a "decisiones registradas".

1. Sube de perfil (conserva tu `deploy/.env` y `config/projects.yaml`: el instalador no los pisa):
   ```bash
   bash deploy/install.sh --profile standard
   bash deploy/provision-sources.sh      # emite tokens y cablea DefectDojo
   ```
   Necesitas ≈4–5 GB de RAM libres en Docker.
2. Ahora tu CI de la semana 3 puede subir a DefectDojo (`DEFECTDOJO_URL`,
   `DEFECTDOJO_TOKEN` como secrets; `product_name` = el producto del panel).
3. **Primer triage**, en DefectDojo, por severidad descendente. Para cada hallazgo
   decide **uno** de estos estados (no inventes un sistema propio):
   - **Mitigated** — lo corregiste.
   - **False Positive** — de verdad no es un problema (ver el caso de abajo antes de usarlo).
   - **Risk Accepted** — es real, pero decides convivir con él: con justificación y
     **fecha de vencimiento**.
   - **Out of Scope** — no pertenece a este producto.
   El panel cuenta solo los hallazgos **activos**: lo que cierres o aceptes deja de
   restar en el score.
4. Para escáneres sin estados (Trivy suelto en el CI, Gitleaks) usa el archivo de
   excepciones versionable: copia `config/wolf-ignore.yaml.example` a
   `config/wolf-ignore.yaml`; cada entrada lleva `motivo`, `aprobado_por` y `vence`
   (una excepción vencida **vuelve a contar** en el score). `wolf-devsecops gen-ignore
   --project <key> --out <dir>` genera `.trivyignore` / `.gitleaksignore` consistentes
   para el CI.

**Hecho cuando:** no queda ningún hallazgo Critical/High sin estado, y cada excepción
tiene motivo y vencimiento.

**Qué mostrarle a gerencia:** el número de hallazgos por estado (corregidos / aceptados
con vencimiento / pendientes) y el score en tendencia.

### Caso de ejemplo: un token de GitHub hallado en el código

Gitleaks (o el escaneo con `--gitleaks`) encuentra un token de GitHub en el código; en
este caso se usaba para **leer releases**. Reacción habitual: "es solo lectura, lo marco
como falso positivo". **No.** Es una credencial real: no es un falso positivo.

Tratamiento correcto, en este orden:

1. **Sácalo del código.** Muévelo a una variable de entorno / *secret* del CI, o
   reemplázalo por un **deploy key de solo lectura por repo** (no es una credencial de
   usuario).
2. **Revócalo ya.** Aunque lo borres del archivo, **sigue en el historial de git** (y en
   cualquier clon o fork). Mientras no lo revoques en GitHub, sigue siendo válido.
3. Si aun así decides mantenerlo (por ejemplo, no puedes rotarlo todavía), márcalo
   **Risk Accepted** — **nunca False Positive** — con justificación (qué permite, por
   qué lo aceptas, qué lo mitiga) y **fecha de vencimiento**. Al vencer, vuelve a contar.
   Para Gitleaks/Trivy sueltos, esa aceptación se escribe en `config/wolf-ignore.yaml`:
   ```yaml
   projects:
     mi-proyecto:
       - { id: <identificador-del-hallazgo-gitleaks>, motivo: "Riesgo aceptado: token de solo lectura para releases; rotación pendiente hasta el cambio a deploy key", aprobado_por: <tu-nombre>, fecha: 2026-11-04, vence: 2026-12-04, tool: gitleaks }
   ```
   (`vence` corto: 30 días. Un riesgo aceptado "para siempre" es deuda escondida.)

Por qué importa: marcarlo como falso positivo dice "esto nunca fue un problema" y borra
la evidencia; Risk Accepted dice "es un problema y alguien decidió convivir con él hasta
tal fecha", que es lo que una auditoría necesita ver.

---

## English

### Week 1 — Install in `lite` mode and connect the main repo

**Goal:** a real score for **one** repo, not a demo.

1. Install (≈2 GB RAM; no DefectDojo yet):
   ```bash
   git clone https://github.com/FireWolfCL/wolf-devsecops.git
   cd wolf-devsecops
   bash deploy/install.sh --profile lite --lang en
   ```
2. At the *"What do you want to analyze?"* prompt, enter your main repo's path. If you
   skipped it (or used `--yes`), do it later:
   ```bash
   bash deploy/scan-target.sh --repo <path-to-your-repo> --org <your-team>
   ```
   It runs Trivy (dependencies/config) and SonarQube on your code, mounted read-only.
   To hunt for secrets across the **whole git history** add `--gitleaks`.
3. First login: open `http://localhost:8088`, change the password and enroll MFA
   (see [INSTALL.md](../INSTALL.md)).

**Done when:** your repo shows in the panel with a score and the SonarQube and Trivy
sources are green.

**Show management:** a screenshot of the panel with **their** repo's score, and the
sentence "we now know where we stand; this is not a demo".

### Week 2 — Read the score and tackle the 3 actions that raise it most

**Goal:** understand the number and improve it with little effort.

1. Open your project's detail view. The **"The 3 actions that raise your score the
   most"** card ranks which checks, once fixed, add the most.
2. For how the score is computed, what each check weighs and what each colour means:
   [POSTURE-RUBRIC.md](POSTURE-RUBRIC.md).
3. Do those 3 actions (or discard with a reason the ones that don't apply). Rescan to see
   the change:
   ```bash
   bash deploy/scan-target.sh --repo <path-to-your-repo>
   ```
4. Write down the score before and after. The trend stays in the panel.

**Done when:** you completed (or justified) the 3 actions and the score went up, or you
know exactly why it didn't.

**Show management:** the "before → after" score, with the 3 actions done and their cost
in hours.

### Week 3 — Put Trivy in the team's CI

**Goal:** scanning stops depending on your laptop. Every push/PR is scanned on its own.

1. Read [PIPELINES.md](PIPELINES.md) (the pattern: scanner in CI → `reimport-scan` to
   DefectDojo → the panel reads it).
2. Copy the matching workflow from [`deploy/ci-examples/github/`](../deploy/ci-examples/github/)
   (one per category: Gitleaks, Semgrep, Grype, Checkov, ZAP; see also
   [`deploy/ci-examples/README.md`](../deploy/ci-examples/README.md)). Suggested priority:
   **Gitleaks → Grype/Syft → Semgrep → Checkov → ZAP**.
3. For the Containers pillar: Trivy on the image in CI. Upload the report to DefectDojo
   with `reimport-scan` (`scan_type=Trivy Scan`, see the table in PIPELINES.md). Your CI
   needs DefectDojo reachable: that ties into week 4; if you don't have it up yet, keep
   the job scanning and publishing the report as a CI artifact, and connect it when you
   enable DefectDojo.
4. Jobs **must not break the build** on findings at first: measure first, set a release
   policy later.

**Done when:** a test PR triggers the scan in CI and the report is available (as an
artifact or in DefectDojo).

**Show management:** "100 % of PRs are now scanned with no human involved", with the CI
run link.

### Week 4 — Enable DefectDojo (`standard` profile) and do the first triage

**Goal:** go from "a list of findings" to "recorded decisions".

1. Move up a profile (your `deploy/.env` and `config/projects.yaml` are kept: the
   installer does not overwrite them):
   ```bash
   bash deploy/install.sh --profile standard --lang en
   bash deploy/provision-sources.sh      # issues tokens and wires DefectDojo
   ```
   You need ≈4–5 GB of free RAM in Docker.
2. Your week-3 CI can now upload to DefectDojo (`DEFECTDOJO_URL`, `DEFECTDOJO_TOKEN` as
   secrets; `product_name` = the product configured in the panel).
3. **First triage**, in DefectDojo, by descending severity. For each finding pick
   **one** state (don't invent your own system):
   - **Mitigated** — you fixed it.
   - **False Positive** — it truly is not a problem (read the case below before using it).
   - **Risk Accepted** — it's real, but you decide to live with it: with a justification
     and an **expiry date**.
   - **Out of Scope** — doesn't belong to this product.
   The panel counts only **active** findings: what you close or accept stops counting
   against the score.
4. For scanners without states (standalone Trivy in CI, Gitleaks) use the versionable
   exceptions file: copy `config/wolf-ignore.yaml.example` to `config/wolf-ignore.yaml`;
   each entry carries `motivo` (reason), `aprobado_por` (approved by) and `vence`
   (expiry; an expired exception **counts again** in the score). `wolf-devsecops
   gen-ignore --project <key> --out <dir>` generates consistent `.trivyignore` /
   `.gitleaksignore` files for the CI.

**Done when:** no Critical/High finding is left without a state, and every exception has
a reason and an expiry.

**Show management:** the number of findings per state (fixed / accepted with expiry /
pending) and the score trend.

### Worked example: a GitHub token found in the code

Gitleaks (or a scan with `--gitleaks`) finds a GitHub token in the code; here it was used
to **read releases**. The usual reaction: "it's read-only, I'll mark it as a false
positive." **Don't.** It's a real credential: not a false positive.

Correct handling, in this order:

1. **Get it out of the code.** Move it to an environment variable / CI *secret*, or
   replace it with a **read-only deploy key per repo** (not a user credential).
2. **Revoke it now.** Even if you delete it from the file, it **stays in git history**
   (and in every clone or fork). Until you revoke it on GitHub it is still valid.
3. If you still decide to keep it (e.g. you can't rotate it yet), mark it
   **Risk Accepted** — **never False Positive** — with a justification (what it grants,
   why you accept it, what mitigates it) and an **expiry date**. When it expires it
   counts again. For standalone Gitleaks/Trivy, that acceptance is written in
   `config/wolf-ignore.yaml`:
   ```yaml
   projects:
     my-project:
       - { id: <gitleaks-finding-identifier>, motivo: "Risk accepted: read-only token for releases; rotation pending until the move to a deploy key", aprobado_por: <your-name>, fecha: 2026-11-04, vence: 2026-12-04, tool: gitleaks }
   ```
   (Keep `vence` short: 30 days. A risk accepted "forever" is hidden debt.)

Why it matters: marking it a false positive says "this was never a problem" and erases
the evidence; Risk Accepted says "it is a problem and someone decided to live with it
until a given date", which is what an audit needs to see.
