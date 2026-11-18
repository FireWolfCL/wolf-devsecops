<div align="center">

# 🐺 Wolf DevSecOps

**One panel for all your code security.**

*Community Edition · by Wolf Industries · Apache-2.0*

[Español](README.md) · **English**

</div>

---

Wolf DevSecOps is an open-source **aggregator console**: it brings the security and
quality status of your projects into a single dashboard, reading what your tools
already produce —**GitHub Actions**, **SonarQube**, **DefectDojo**, **Trivy** and
**Harbor**— without replacing them. The panel never scans on its own (ADR-001);
the all-in-one installer *can* run **Trivy** as a convenience to give you a first
score on day one. A *single pane of glass*.

It isn't tied to any project: you point Wolf DevSecOps at **your** repos and
**your** sources through a config file, and that's it. Designed so any team can
drop it into its own pipeline with a `docker compose up`.

## Quick install

One assisted command brings up the whole suite (panel + SonarQube + DefectDojo +
Trivy).

```bash
git clone https://github.com/FireWolfCL/wolf-devsecops.git
cd wolf-devsecops
bash deploy/install.sh --lang en     # Windows: pwsh deploy/install.ps1 -Lang en
```

→ Full guide (ES + EN): **[INSTALL.md](INSTALL.md)** · Kubernetes: [`deploy/DEPLOY.md`](deploy/DEPLOY.md)
→ On your own for security? **[First 30 days](docs/PRIMEROS-30-DIAS.md)** · Operations and backups: [docs/RUNBOOK.md](docs/RUNBOOK.md)

## What does it show?

For each project you configure:

- **CI status** — did the last run's gates pass? (GitHub Actions)
- **Vulnerabilities** — dependency CVEs, SAST, config, secrets (DefectDojo)
- **Quality** — bugs, code smells, coverage (SonarQube)
- **Containers** — CVEs in the images you're about to deploy (**Harbor** + trivy-adapter as the production path; standalone Trivy as the no-registry fallback — see [INSTALL.md](INSTALL.md))
- **Releases** — latest published version and its SBOM
- **Remediation** — open PRs that propose fixes

Findings are grouped by scan type (secrets · SAST · SCA · container · IaC · DAST).
To feed them from **your** CI → DefectDojo there are ready-made pipelines: see
**[docs/PIPELINES.md](docs/PIPELINES.md)** and [`deploy/ci-examples/`](deploy/ci-examples/).

## Screenshots

<div align="center">

<a href="docs/img/dashboard.jpg"><img src="docs/img/dashboard.jpg" width="46%" alt="Dashboard — compliance score per project"/></a>
<a href="docs/img/proyecto.jpg"><img src="docs/img/proyecto.jpg" width="46%" alt="Project detail — pipeline map and provenance"/></a>
<a href="docs/img/hallazgos.jpg"><img src="docs/img/hallazgos.jpg" width="46%" alt="Posture breakdown — rubric, findings and containers"/></a>
<a href="docs/img/settings.jpg"><img src="docs/img/settings.jpg" width="46%" alt="Integrations — connected sources and token status"/></a>

<sub>Dashboard · Project detail · Posture breakdown · Integrations — sample data, project names anonymized.</sub>

</div>

## Architecture

**How the system connects** — the panel is *read-only* (ADR-001): it reads what
your tools already produce, normalizes it to a common contract and scores it. It
never writes back to the sources.

```mermaid
flowchart LR
  subgraph SRC["External sources · read-only"]
    GH["GitHub / GitLab / Jenkins<br/>CI + releases"]
    TR["Trivy<br/>JSON reports"]
    DD["DefectDojo<br/>findings"]
    SQ["SonarQube<br/>quality + coverage"]
    HB["Harbor<br/>image CVEs"]
    WSS["Wolf Security Suite<br/>(optional)"]
  end

  subgraph PANEL["Wolf DevSecOps · read-only (ADR-001)"]
    direction TB
    CON["connectors/<br/>one per source → normalized contract"]
    AGG["core/aggregator<br/>concurrency · cache · degradation"]
    GOV["services/governance<br/>governed net (accepted risks)"]
    POS["core/posture<br/>rubric → compliance score"]
    API["api/ FastAPI<br/>/api/v1/projects"]
    DB[("DB · snapshots · exceptions · users")]
    SCH["scheduler<br/>poll ~2h"]
  end

  FE["React frontend<br/>dashboard · findings · settings"]
  WLA["Wolf License Authority<br/>verifies ES256 license"]

  SRC --> CON --> AGG --> POS --> API --> FE
  GOV --> AGG
  AGG --> DB
  SCH --> AGG
  DB --> GOV
  WLA -. pro/enterprise .-> API
```

The backend exists so **tokens live on the server**, never in the browser. It
normalizes the sources into a common format and caches them.

### Layers

| Layer | Responsibility |
|---|---|
| **Frontend** (React) | Dashboard, findings and settings. Never sees the tokens. |
| **API** (FastAPI) | Translates HTTP ⇄ aggregator; keeps tokens on the server. |
| **Aggregator** (`core/aggregator`) | Calls connectors in parallel, applies cache and degradation, bakes the governed net and the score. |
| **Connectors** (`connectors/`) | One per source; translate each external API into the normalized contract. Read-only. |
| **Scheduler** | Periodic poll (~2h) → stores a posture *snapshot* in the DB. |
| **DB** | Posture snapshots, exceptions (accepted risks) and users. |
| **WLA** | Verifies the signed (ES256) license that unlocks Pro/Enterprise. |

### Refresh cycle

```mermaid
sequenceDiagram
  participant S as Scheduler
  participant A as Aggregator
  participant C as Connectors
  participant X as External sources
  participant P as core/posture
  participant DB as DB
  S->>A: refresh project
  A->>C: fetch_summary (in parallel)
  C->>X: API / reports (read-only)
  X-->>C: data
  C-->>A: normalized contract
  A->>P: compute score (governed net)
  P-->>A: compliance %
  A->>DB: store snapshot
  Note over A,DB: the detail view reads live on each request
```

→ Full architecture (modules, contracts, ADRs): **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**

## Status

**Community working.** Backend (FastAPI) + frontend (React) operational; connectors
for GitHub, SonarQube, DefectDojo, Trivy and Harbor, plus optional WSS; assisted
installer (`deploy/install.sh`) and a posture rubric (`docs/POSTURE-RUBRIC.md`).
Pro/Enterprise editions are in development. Design and architecture decisions in `docs/`.

## Stack

- **Backend:** Python 3.12 · FastAPI
- **Frontend:** React 18 · Vite · TypeScript
- **Deployment:** Docker Compose

## Part of the Wolf ecosystem

Wolf DevSecOps Community runs **on its own**, with no other Wolf dependency. When
you also use **Wolf Security Suite (WSS)** —Wolf Industries' GRC/vCISO platform—,
Wolf DevSecOps integrates with it and adds **infrastructure and asset**
vulnerabilities to the code view. WSS is an **optional** integration: without it,
Wolf DevSecOps works just the same.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
