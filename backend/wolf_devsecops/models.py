"""Contrato de datos normalizado (ARCHITECTURE.md §1).

Todo conector traduce su fuente a estos modelos: son el único lenguaje que cruza
la frontera conector → agregador → API → frontend. Ningún modelo de salida
contiene jamás tokens, cabeceras de autenticación ni trazas internas.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field, HttpUrl

# ─── Enums ───────────────────────────────────────────────────────────────────


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


class CIStatus(str, Enum):
    success = "success"
    failure = "failure"
    running = "running"
    unknown = "unknown"  # la fuente respondió pero sin dato interpretable


class SourceStatus(str, Enum):
    ok = "ok"  # datos frescos
    stale = "stale"  # datos de caché servidos tras un fallo de refresco
    unavailable = "unavailable"  # fuente configurada pero caída / error
    not_configured = "not_configured"  # fuente no definida para este proyecto


# ─── Bloques por fuente ──────────────────────────────────────────────────────


class SeverityCount(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0

    @property
    def total(self) -> int:
        return self.critical + self.high + self.medium + self.low + self.info


class CISummary(BaseModel):  # desde GitHub Actions
    status: CIStatus
    workflow: str
    branch: str | None = None
    run_number: int | None = None
    html_url: HttpUrl | None = None
    finished_at: datetime | None = None


class FindingsSummary(BaseModel):  # desde DefectDojo (o WSS)
    counts: SeverityCount
    total_active: int
    # Conteo de hallazgos activos por tipo de escaneo (6.2): secrets, sast, sca,
    # container, iac, dast, other. Vacío si la fuente no expone el tipo.
    categories: dict[str, int] = Field(default_factory=dict)
    url: HttpUrl | None = None


class QualitySummary(BaseModel):  # desde SonarQube
    quality_gate: Literal["OK", "WARN", "ERROR", "NONE"] = "NONE"
    bugs: int = 0
    vulnerabilities: int = 0
    code_smells: int = 0
    coverage: float | None = None  # porcentaje 0..100
    url: HttpUrl | None = None


class ReleaseSummary(BaseModel):  # derivado de GitHub
    tag: str | None = None
    name: str | None = None
    published_at: datetime | None = None
    html_url: HttpUrl | None = None
    sbom_url: HttpUrl | None = None


# ─── Envoltura de resultado por fuente (clave para la degradación) ────────────

T = TypeVar("T")


class SourceResult(BaseModel, Generic[T]):
    status: SourceStatus
    data: T | None = None
    error: str | None = None  # mensaje saneado, nunca stack ni token
    fetched_at: datetime | None = None


# ─── Agregados que consume el frontend ───────────────────────────────────────


class ProjectSummary(BaseModel):
    key: str
    name: str
    org: str = "Wolf"  # organización dueña (Wolf vs externas como lsst-it)
    ci: SourceResult[CISummary]
    findings: SourceResult[FindingsSummary]
    quality: SourceResult[QualitySummary]
    release: SourceResult[ReleaseSummary]
    # Postura de contenedores: Harbor + Trivy fusionados (CVEs de imagen). ADR-006.
    containers: SourceResult[FindingsSummary]
    # % de cumplimiento calculado al vuelo (docs/POSTURE-RUBRIC.md).
    posture: PostureScore | None = None
    # Deep-links de salida hacia cada herramienta (URL PÚBLICA navegable). Se
    # construyen desde config + settings, NUNCA desde la respuesta de una fuente.
    # source → url ("sonarqube" | "defectdojo" | "harbor" | "github").
    links: dict[str, str] = {}
    generated_at: datetime


class ProjectListItem(BaseModel):  # versión liviana para el listado
    key: str
    name: str
    org: str = "Wolf"
    ci_status: CIStatus | None = None
    findings: SeverityCount | None = None
    container_findings: SeverityCount | None = None  # Harbor + Trivy fusionados
    quality_gate: str | None = None
    worst_source_status: SourceStatus
    # Postura (versión liviana para el listado; detalle completo en el resumen).
    posture_score: int | None = None
    posture_band: str = "sin_datos"
    posture_coverage: int = 0


class SourceDetail(BaseModel):  # detalle crudo-normalizado por fuente
    project_key: str
    source: Literal["github", "gitlab", "jenkins", "defectdojo", "sonarqube", "wss", "harbor", "trivy"]
    status: SourceStatus
    fetched_at: datetime | None = None
    payload: dict = {}
    error: str | None = None


class ProbeResult(BaseModel):  # salida de Connector.probe() — la usa el wizard
    source: str
    reachable: bool
    authenticated: bool
    version: str | None = None
    compatible: bool = False
    detail: str | None = None


# ─── Postura / % de cumplimiento (docs/POSTURE-RUBRIC.md) ────────────────────
# Score calculado al vuelo desde ProjectSummary (ADR-012: no se persiste). Sin
# fuga: cada check expone solo {id, result, weight, earned, reason_code}.

CheckReason = Literal[
    "ok", "source_not_configured", "source_unavailable",
    "no_verdict", "threshold_not_met", "missing_data",
    # Los CVEs de contenedor ya los mide el pilar de Contenedores; el espejo de
    # esos mismos CVEs en Hallazgos (DefectDojo) no se cuenta dos veces (opción A).
    "covered_by_containers",
]
PostureBand = Literal[
    "solido", "aceptable", "en_riesgo", "critico", "evidencia_insuficiente", "sin_datos",
]


class CheckResult(BaseModel):
    id: str
    result: Literal["pass", "partial", "fail", "na"]
    weight: int
    earned: float  # peso * valor (0, 0.5·peso, o peso)
    reason_code: CheckReason


class PostureScore(BaseModel):
    rubric_version: str
    score: int | None  # None = sin datos (todo NA)
    coverage: int  # % del peso total efectivamente evaluado
    band: PostureBand
    stale: bool = False
    degraded: bool = False
    capped_by: Literal["coverage", "critical"] | None = None
    checks: list[CheckResult] = []


class GlobalPosture(BaseModel):
    org: str = ""  # organización a la que corresponde este global (vacío = todas)
    global_score: int | None
    coverage: int
    band: PostureBand
    distribution: dict[str, int] = {}  # banda → nº de proyectos
    at_risk: list[str] = []  # keys en banda en_riesgo
    critical: list[str] = []  # keys en banda critico
    project_count: int = 0


class PostureOverview(BaseModel):
    """Postura POR organización: el % de Wolf no se mezcla con el de externas."""
    orgs: list[GlobalPosture] = []
    primary: str = "Wolf"  # organización cuyo % es el titular del dashboard


# ─── Dashboard POR HERRAMIENTA (vista transpuesta: una fuente, todos los
#     proyectos) — "single pane of glass" para no salir de Wolf. ───────────────
class ToolRow(BaseModel):
    key: str  # project key
    name: str
    status: SourceStatus
    summary: dict | None = None  # dump del bloque de esa herramienta (o None)
    error: str | None = None
    link: str | None = None  # deep-link al proyecto en la herramienta (URL pública)


class ToolDashboard(BaseModel):
    tool: str  # github | sonarqube | defectdojo | containers
    rows: list[ToolRow] = []
    totals: dict = {}  # agregados propios de la herramienta
    configured_count: int = 0  # proyectos que usan la herramienta
    tool_url: str | None = None  # home pública de la herramienta ("Abrir en…")
    generated_at: datetime


# ─── Reporte documental por proyecto (entregable) ────────────────────────────
class ReportSection(BaseModel):
    """Una herramienta dentro del reporte: QUÉ está afectado y CÓMO remediarlo.

    El contrato es DATO ESTRUCTURADO, no texto pre-redactado: el idioma del
    reporte lo decide el frontend con `t(...)`. Antes el backend componía frases
    en español ("6 activos · 0 críticos"), lo que mezclaba idiomas cuando el panel
    corría en inglés. Ahora cada sección lleva los números/estados crudos.
    """
    source: str  # containers | defectdojo | sonarqube | github
    kind: Literal["findings", "quality", "ci"] = "findings"
    status: SourceStatus
    # Secciones de hallazgos (contenedores/DefectDojo) y ahora también Sonar:
    counts: SeverityCount | None = None
    total_active: int | None = None
    # Sección de calidad (SonarQube): gate/bugs/vulns/cobertura.
    quality: QualitySummary | None = None
    # Sección CI/Release (GitHub).
    ci: CISummary | None = None
    release: ReleaseSummary | None = None
    # Lista accionable normalizada (contenedores/DefectDojo/Sonar). DATO, no marcado.
    findings: list[dict] = []
    link: str | None = None  # deep-link a la herramienta (URL pública)


class ProjectReport(BaseModel):
    key: str
    name: str
    org: str = "Wolf"
    generated_at: datetime
    # Postura estructurada: el frontend arma la línea humana en el idioma activo.
    posture: PostureScore | None = None
    sections: list[ReportSection] = []


# ─── Subsistema de IA (ADR-007, F6) ──────────────────────────────────────────
# INVARIANTE: la entrada de IA lleva SOLO texto de findings, NUNCA código ni diffs.


class FindingBrief(BaseModel):
    """Proyección con allow-list de un finding para la IA (P0-1, wolf-ai-safety).

    SOLO estos campos cruzan hacia el proveedor. Nunca file_path, line, código,
    ni los campos SAST/secret que incrustan ubicaciones o fragmentos.
    """

    title: str = Field(max_length=200)
    severity: Severity
    description: str = Field(default="", max_length=240)  # saneada + truncada


class AIReviewContext(BaseModel):
    project_key: str
    findings: list[FindingBrief] = []  # SOLO briefs con allow-list — jamás código


class PrioritizedFinding(BaseModel):
    title: str = Field(max_length=200)
    severity: Severity
    rationale: str = Field(max_length=500)
    recommended_action: str = Field(max_length=500)


class AIReviewResult(BaseModel):
    provider_type: Literal["local", "hosted"]
    model: str
    egress: bool  # True si el dato salió a un tercero (hosted)
    generated_at: datetime
    summary: str
    prioritized: list[PrioritizedFinding] = []


# ─── Feature B: propuesta de fix con CÓDIGO (ADR-018, assistive) ─────────────
# A diferencia del reviewer (solo texto de findings), este flujo SÍ puede mandar
# una ventana ACOTADA de código (±N líneas de un archivo) al modelo. El egreso se
# computa SIEMPRE server-side (local=False, hosted=True); el cliente nunca lo manda.


class AICodeFixRequest(BaseModel):
    """Body de POST /projects/{key}/ai/code-fix. `base_url`/`token`/`egress` NO
    viajan en el body: se resuelven server-side por provider (ADR-002)."""

    finding_ref: str = Field(min_length=1, max_length=300)
    provider: Literal["local", "hosted"]
    model: str = Field(min_length=1, max_length=120)
    # Fuente del finding a resolver (file_path+line). DefectDojo por defecto
    # (compat); "sonarqube" permite proponer fix sobre un issue de calidad.
    source: Literal["defectdojo", "sonarqube"] = "defectdojo"


class AICodeFixResult(BaseModel):
    provider_type: Literal["local", "hosted"]
    model: str
    egress: bool  # True si el código salió a un tercero (hosted) — rotulado "CON EGRESO"
    original_snippet: str  # ventana leída del repo (texto plano; el front NO lo renderiza como HTML)
    proposed_snippet: str  # propuesta del modelo (texto/diff); "" si no hubo propuesta
    explanation: str


# ─── Feature D: remediación branch + PR draft (ADR-017, decision-assist) ─────
# El panel CREA un branch aislado (prefijo reservado wolf-sec/) + un PR en estado
# DRAFT. NUNCA mergea, nunca toca main/default/otros branches, nunca force-push.


class RemediationFinding(BaseModel):
    """Finding elegido para la propuesta. Solo METADATOS (no código): el ref lo
    identifica; title/severity/action alimentan el .md de la propuesta."""

    ref: str = Field(min_length=1, max_length=300)
    title: str | None = Field(default=None, max_length=300)
    severity: str | None = Field(default=None, max_length=20)
    recommended_action: str | None = Field(default=None, max_length=1000)


class RemediationBranchRequest(BaseModel):
    picked_findings: list[RemediationFinding] = Field(min_length=1, max_length=50)
    confirm: bool = False  # fail-closed: sin confirm=true explícito no se escribe nada


class RemediationBranchResult(BaseModel):
    branch: str
    pr_url: str | None = None
    applied: bool  # True si esta petición creó algo nuevo (branch o PR)
    already_existed: bool  # True si el PR ya existía (idempotente: no se duplicó)


# ─── Inventario de dependencias (Feature A) ──────────────────────────────────


class DependencyItem(BaseModel):
    name: str
    version: str | None = None
    ecosystem: str | None = None
    target: str | None = None
    vulnerable: bool = False
    cve_ids: list[str] = []
    fixed_version: str | None = None


class DependencyInventory(BaseModel):
    project_key: str
    generated_at: datetime
    source: Literal["trivy"] = "trivy"
    # True = el reporte no trae Packages (sin --list-all-pkgs): solo paquetes con CVE.
    degraded: bool = False
    packages: list[DependencyItem] = []


# ─── Mapa de módulos (Feature C) ─────────────────────────────────────────────


class ModuleNode(BaseModel):
    path: str
    type: Literal["file", "dir"]
    size: int | None = None


class ModuleTree(BaseModel):
    project_key: str
    generated_at: datetime
    nodes: list[ModuleNode] = []
    truncated: bool = False  # recortado por límite de nodos/profundidad


# ─── Tendencia histórica de postura (ADR-019): SOLO métricas agregadas ───────
class PostureSnapshot(BaseModel):
    captured_at: datetime
    compliance_pct: float
    band: str
    crit: int
    high: int
    med: int
    low: int
    coverage_pct: float | None = None
    rubric_version: str | None = None  # rúbrica vigente al capturar (marca cambios)


class PostureHistory(BaseModel):
    project_key: str
    window_days: int | None = None  # ventana efectiva aplicada (None = ilimitada, Pro)
    snapshots: list[PostureSnapshot] = []  # orden ascendente por captured_at


class ExceptionIn(BaseModel):
    """Alta de una excepción de hallazgo (ADR-021). El motivo es obligatorio."""
    model_config = {"extra": "forbid"}
    source: Literal["defectdojo", "sonarqube"]
    finding_ref: str = Field(min_length=1, max_length=200)
    kind: Literal["false_positive", "risk_accepted", "out_of_scope", "mitigated"]
    justification: str = Field(min_length=3, max_length=2000)
    finding_title: str | None = Field(default=None, max_length=300)
    category: str | None = Field(default=None, max_length=20)
    expires_days: int | None = Field(default=None, ge=1, le=365)  # solo risk_accepted


class ExceptionOut(BaseModel):
    id: str
    project_key: str
    source: str
    finding_ref: str
    finding_title: str | None = None
    category: str | None = None
    kind: str
    justification: str
    status: str  # proposed | applied | revoked | expired
    created_by: str | None = None
    created_at: datetime
    approved_by: str | None = None
    approved_at: datetime | None = None
    expires_at: datetime | None = None


class ExceptionList(BaseModel):
    items: list[ExceptionOut] = []


class PostureSparklines(BaseModel):
    """Series compactas de % de cumplimiento por proyecto para los sparklines del
    dashboard. Una sola consulta a BD (ADR-012: nunca golpea las fuentes)."""
    window_days: int | None = None
    series: dict[str, list[float]] = {}  # key → [compliance_pct ascendente]


# ─── Notificaciones (campana + reporte de avance) ────────────────────────────


class EventItem(BaseModel):
    id: str
    created_at: datetime
    project_key: str
    kind: str  # improved|worsened|new_critical|resolved_critical|new_high|resolved_high
    severity: str  # good|warn|danger|info
    band_from: str | None = None
    band_to: str | None = None
    compliance_from: float | None = None
    compliance_to: float | None = None
    crit_delta: int = 0
    high_delta: int = 0


class EventList(BaseModel):
    events: list[EventItem] = []
    unread: int = 0


class UnreadCount(BaseModel):
    count: int = 0


class EventReportRow(BaseModel):
    project_key: str
    improved: int = 0
    worsened: int = 0
    new_critical: int = 0
    resolved_critical: int = 0


class EventReport(BaseModel):
    since: datetime
    days: int
    totals: dict[str, int] = {}
    by_project: list[EventReportRow] = []
