"""Edición del producto (Community / Pro / Enterprise) y gate de features.

Fase 1 (edition-flag): la edición se fija por entorno (`WOLF_EDITION`). La ausencia
o un valor inválido → Community (degradación grácil, nunca kill-switch). Más adelante
(fase 2) una licencia firmada por la WLA (Wolf-Licenses, ES256/JWS) determinará la
edición, los módulos y la expiración; el contrato de este módulo (`features_for`,
`limits_for`, `has_feature`) no cambiará: solo cambia la FUENTE de la edición.

Principio de corte: NO se gatea el valor de seguridad base (conectores, postura,
reporte, usuarios locales+MFA, IA local). Solo se gatea escala, automatización,
integración empresarial y features premium. Las features listadas son las PREMIUM;
lo que no aparece es base y está disponible en toda edición.

El BACKEND es la autoridad: el frontend solo refleja. Los límites None = ilimitado.
"""

from __future__ import annotations

from fastapi import HTTPException

from . import licensing
from .settings import get_settings

COMMUNITY = "community"
PRO = "pro"
ENTERPRISE = "enterprise"
_VALID = (COMMUNITY, PRO, ENTERPRISE)

# ─── Claves de features PREMIUM (canónicas; el label vive en el i18n del frontend) ──
FEATURE_MULTI_ORG = "multi_org"                 # más de 1 organización
FEATURE_NOTIFICATIONS = "notifications.external"  # Telegram / email / bot
FEATURE_SCHEDULER = "scheduler"                 # snapshots periódicos automáticos
FEATURE_AI_HOSTED = "ai.hosted"                 # proveedores de IA hosted
FEATURE_AI_CODEFIX = "ai.codefix"               # propuesta de fix con código (Feature B)
FEATURE_REMEDIATION_PR = "remediation.pr"       # branch + PR-draft (Feature D)
FEATURE_HISTORY_UNLIMITED = "history.unlimited"  # tendencia más allá de la ventana base
FEATURE_REPORTS_SCHEDULED = "reports.scheduled"  # reportes programados (roadmap)
FEATURE_REPORTS_COMPLIANCE = "reports.compliance"  # reporte de cumplimiento (roadmap)
FEATURE_SSO = "sso"                             # SSO / LDAP / AD (roadmap)
FEATURE_AUDIT_EXPORT = "audit.export"           # export/retención de auditoría (roadmap)
FEATURE_UPDATES_CHANNEL = "updates.channel"     # canal de updates firmado (roadmap)
FEATURE_WHITELABEL = "whitelabel"               # marca propia (enterprise, roadmap)
FEATURE_MULTI_TENANT = "multi_tenant"           # multi-tenant aislado (enterprise, roadmap)

# Ventana de historial (días) para Community; Pro+ = ilimitado (None).
COMMUNITY_HISTORY_DAYS = 30

_PRO_FEATURES = frozenset({
    FEATURE_MULTI_ORG, FEATURE_NOTIFICATIONS, FEATURE_SCHEDULER, FEATURE_AI_HOSTED,
    FEATURE_AI_CODEFIX, FEATURE_REMEDIATION_PR, FEATURE_HISTORY_UNLIMITED,
    FEATURE_REPORTS_SCHEDULED, FEATURE_REPORTS_COMPLIANCE, FEATURE_SSO,
    FEATURE_AUDIT_EXPORT, FEATURE_UPDATES_CHANNEL,
})
_ENTERPRISE_EXTRA = frozenset({FEATURE_WHITELABEL, FEATURE_MULTI_TENANT})

_FEATURES: dict[str, frozenset[str]] = {
    COMMUNITY: frozenset(),
    PRO: _PRO_FEATURES,
    ENTERPRISE: _PRO_FEATURES | _ENTERPRISE_EXTRA,
}

# None = ilimitado. Proyectos ilimitados en todas las ediciones (no se taxa la
# cobertura de seguridad); el límite real de Community es 1 organización.
_LIMITS: dict[str, dict[str, int | None]] = {
    COMMUNITY: {"max_projects": None, "max_orgs": 1},
    PRO: {"max_projects": None, "max_orgs": None},
    ENTERPRISE: {"max_projects": None, "max_orgs": None},
}


def normalize(edition: str | None) -> str:
    """Edición válida o Community (fail-safe ante valor ausente/desconocido)."""
    e = (edition or "").strip().lower()
    return e if e in _VALID else COMMUNITY


def current_edition() -> str:
    """Edición efectiva del despliegue. Precedencia: licencia firmada VÁLIDA (WLA)
    > flag WOLF_EDITION (salvo que license_required). Ausente/ inválida → community."""
    settings = get_settings()
    lic = licensing.effective_edition(settings)
    if lic is not None:
        return normalize(lic)
    if settings.license_required:
        return COMMUNITY
    return normalize(settings.edition)


def features_for(edition: str) -> frozenset[str]:
    return _FEATURES[normalize(edition)]


def limits_for(edition: str) -> dict[str, int | None]:
    return dict(_LIMITS[normalize(edition)])


def has_feature(edition: str, feature: str) -> bool:
    return feature in features_for(edition)


def is_enabled(feature: str) -> bool:
    """¿La feature está activa en la edición efectiva del despliegue?"""
    return has_feature(current_edition(), feature)


def history_window_days() -> int | None:
    """Días de historial visibles; None = ilimitado (Pro+)."""
    return None if is_enabled(FEATURE_HISTORY_UNLIMITED) else COMMUNITY_HISTORY_DAYS


def org_allowed(existing_orgs: set[str], new_org: str) -> bool:
    """¿Usar `new_org` respeta el límite de organizaciones de la edición actual?
    None = ilimitado (Pro+); Community = 1. No penaliza re-usar una org existente."""
    limit = limits_for(current_edition())["max_orgs"]
    if limit is None:
        return True
    return len(set(existing_orgs) | {new_org}) <= limit


def require_feature(feature: str):
    """Dependency de FastAPI: 402 (Payment Required) si la feature no está en la
    edición actual. El mensaje es apto para el usuario (sin filtrar nada sensible)."""

    def _dep() -> None:
        if not is_enabled(feature):
            raise HTTPException(
                status_code=402,
                detail=(
                    f"La función '{feature}' requiere la edición Pro "
                    f"(edición actual: {current_edition()})."
                ),
            )

    return _dep
