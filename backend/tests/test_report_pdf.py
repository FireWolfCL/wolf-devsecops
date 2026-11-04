"""MEJORAS 2.4 — reporte de cumplimiento en PDF (Pro)."""

from __future__ import annotations

from datetime import UTC, datetime

from wolf_devsecops.core.ignore import IgnoreEntry
from wolf_devsecops.models import (
    CheckResult,
    PostureScore,
    PostureSnapshot,
    ProjectSummary,
    SourceResult,
    SourceStatus,
)
from wolf_devsecops.services import report_pdf
from wolf_devsecops.services.report_pdf import COMPLIANCE_MAP


def _nc():
    return SourceResult(status=SourceStatus.not_configured)


def _summary(with_score=True) -> ProjectSummary:
    posture = None
    if with_score:
        posture = PostureScore(
            rubric_version="1", score=72, coverage=80, band="aceptable",
            checks=[
                CheckResult(id="ci-green", result="pass", weight=10, earned=10, reason_code="ok"),
                CheckResult(id="findings-no-critical", result="fail", weight=20, earned=0, reason_code="threshold_not_met"),
                CheckResult(id="quality-coverage", result="partial", weight=5, earned=2.5, reason_code="threshold_not_met"),
                CheckResult(id="release-sbom", result="na", weight=10, earned=0, reason_code="source_not_configured"),
            ],
        )
    return ProjectSummary(
        key="wolf-cloud", name="Wolf-Cloud", org="Wolf", generated_at=datetime.now(UTC),
        ci=_nc(), findings=_nc(), quality=_nc(), release=_nc(), containers=_nc(),
        posture=posture,
    )


def _history() -> list[PostureSnapshot]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        PostureSnapshot(captured_at=base, compliance_pct=60.0, band="en_riesgo", crit=2, high=1, med=0, low=0),
        PostureSnapshot(captured_at=base, compliance_pct=72.0, band="aceptable", crit=0, high=1, med=0, low=0),
    ]


def test_build_pdf_devuelve_pdf_valido():
    accepted = [IgnoreEntry(match="CVE-2024-1234", reason="FP upstream", approved_by="faal", expires="2027-01-01")]
    pdf = report_pdf.build_pdf(_summary(), _history(), accepted, lang="es", edition="pro")
    assert pdf[:4] == b"%PDF"
    assert len(pdf) > 1500  # tiene contenido real


def test_build_pdf_sin_score_no_revienta():
    pdf = report_pdf.build_pdf(_summary(with_score=False), [], [], lang="en", edition="community")
    assert pdf[:4] == b"%PDF"


def test_todos_los_checks_tienen_mapeo():
    # Cada id de check de la rúbrica debe mapear a los 3 marcos (evidencia completa).
    for cid in ("ci-green", "findings-no-critical", "findings-no-high", "containers-no-critical",
                "containers-no-high", "quality-gate", "quality-no-vulns", "quality-coverage", "release-sbom"):
        assert cid in COMPLIANCE_MAP
        assert {"iso", "ssdf", "dsomm"} <= set(COMPLIANCE_MAP[cid])
