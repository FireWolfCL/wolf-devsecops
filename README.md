<div align="center">

# 🐺 Wolf DevSecOps

**Un solo panel para toda tu seguridad de código.**

*Community Edition · by Wolf Industries · Apache-2.0*

</div>

---

Wolf DevSecOps es una **consola agregadora** open-source: reúne en un único
dashboard el estado de seguridad y calidad de tus proyectos, leyendo lo que ya
producen tus herramientas —**GitHub Actions**, **DefectDojo**, **SonarQube**—
sin reemplazarlas ni volver a escanear. Un *single pane of glass*.

No está atada a ningún proyecto: apuntas Wolf DevSecOps a **tus** repos y **tus**
fuentes mediante un archivo de configuración, y listo. Pensada para que cualquier
equipo la integre en su propio pipeline con un `docker compose up`.

## ¿Qué muestra?

Por cada proyecto que configures:

- **Estado de CI** — ¿pasaron los gates del último run? (GitHub Actions)
- **Vulnerabilidades** — CVEs de dependencias, SAST, config, secretos (DefectDojo)
- **Calidad** — bugs, code smells, cobertura (SonarQube)
- **Releases** — última versión publicada y su SBOM
- **Remediación** — PRs abiertos que proponen arreglos

## Arquitectura (resumen)

```
  Navegador                Backend (FastAPI)              Fuentes
 ┌──────────┐   /api/*    ┌───────────────────┐  tokens  ┌──────────────┐
 │ Dashboard │ ─────────▶ │ proxy + conectores │ ───────▶ │ GitHub API    │
 │ (React)   │ ◀───────── │ normaliza + cachea │ ◀─────── │ DefectDojo API│
 └──────────┘   JSON      └───────────────────┘          │ SonarQube API │
                           ▲ config/projects.yaml         └──────────────┘
```

El backend es necesario para que los **tokens vivan en el servidor**, nunca en
el navegador. Normaliza las tres fuentes a un formato común y cachea.

## Estado

🚧 **En construcción (MVP).** La arquitectura la está definiendo el equipo antes
de la implementación. Ver `docs/`.

## Stack

- **Backend:** Python 3.12 · FastAPI
- **Frontend:** React 18 · Vite · TypeScript
- **Despliegue:** Docker Compose

## Parte del ecosistema Wolf

Wolf DevSecOps Community corre **por sí solo**, sin depender de nada más de Wolf.
Cuando además usas **Wolf Security Suite (WSS)** —la plataforma GRC/vCISO de Wolf
Industries—, Wolf DevSecOps se integra con ella y suma a la vista de código la de
vulnerabilidades de **infraestructura y activos**. WSS es una integración
**opcional**: si no la tienes, Wolf DevSecOps funciona igual.

## Licencia

Apache License 2.0 — ver [LICENSE](LICENSE) y [NOTICE](NOTICE).
