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

## What does it show?

For each project you configure:

- **CI status** — did the last run's gates pass? (GitHub Actions)
- **Vulnerabilities** — dependency CVEs, SAST, config, secrets (DefectDojo)
- **Quality** — bugs, code smells, coverage (SonarQube)
- **Containers** — CVEs in the images you're about to deploy (Trivy / Harbor)
- **Releases** — latest published version and its SBOM
- **Remediation** — open PRs that propose fixes

Findings are grouped by scan type (secrets · SAST · SCA · container · IaC · DAST).
To feed them from **your** CI → DefectDojo there are ready-made pipelines: see
**[docs/PIPELINES.md](docs/PIPELINES.md)** and [`deploy/ci-examples/`](deploy/ci-examples/).

## Architecture (overview)

```
  Browser                  Backend (FastAPI)              Sources
 ┌──────────┐   /api/*    ┌───────────────────┐  tokens  ┌───────────────┐
 │ Dashboard │ ─────────▶ │ proxy + connectors │ ───────▶ │ GitHub API     │
 │ (React)   │ ◀───────── │ normalize + cache  │ ◀─────── │ SonarQube API  │
 └──────────┘   JSON      └───────────────────┘          │ DefectDojo API │
                           ▲ config/projects.yaml         │ Trivy · Harbor │
                                                          │ WSS (optional) │
                                                          └───────────────┘
```

The backend exists so **tokens live on the server**, never in the browser. It
normalizes the sources into a common format and caches them.

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
