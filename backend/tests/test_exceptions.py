"""MEJORAS 6.3.b/6.3.d (ADR-021) — excepciones con escritura a la fuente.

Verifica la orquestación tras la revisión de amenazas: risk_accepted SIEMPRE pasa por
propuesta→aprobación (A1); el vencimiento que no puede revertir queda `expire_failed`
con alerta danger (A2); no se apilan excepciones sobre el mismo hallazgo (M2); la fila
se persiste antes de escribir (M1).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.db.base import Base
from wolf_devsecops.models import ExceptionIn
from wolf_devsecops.models_db.events import ProjectEvent
from wolf_devsecops.models_db.exceptions import SourceException
from wolf_devsecops.services import exceptions_service as svc
from wolf_devsecops.settings import get_settings


class _StubDD:
    def __init__(self):
        self.calls = []
        self.fail_revoke = False

    async def set_finding_state(self, fid, *, active, false_p=False, risk_accepted=False,
                                out_of_scope=False, is_mitigated=False):
        if active and self.fail_revoke:  # revoke == active=True
            raise svc.ExceptionError("sin permiso de escritura")
        self.calls.append({"fid": fid, "active": active, "false_p": false_p,
                           "risk_accepted": risk_accepted, "out_of_scope": out_of_scope,
                           "is_mitigated": is_mitigated})


class _StubSonar:
    def __init__(self):
        self.calls = []

    async def transition_issue(self, key, tx):
        self.calls.append({"key": key, "tx": tx})


class _StubAgg:
    def __init__(self):
        self.dd = _StubDD()
        self.sonar = _StubSonar()

    def connector_for(self, source):
        return {"defectdojo": self.dd, "sonarqube": self.sonar}.get(source)


@pytest.fixture
async def session(tmp_path):
    get_settings().database_url = f"sqlite+aiosqlite:///{tmp_path}/exc.db"
    await db_engine.dispose_engine()
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with db_engine.get_sessionmaker()() as s:
        yield s
    await db_engine.dispose_engine()


def _in(kind="false_positive", source="defectdojo", ref="123"):
    return ExceptionIn(source=source, finding_ref=ref, kind=kind, justification="motivo suficiente")


async def test_fp_de_operador_escribe_active_false(session):
    agg = _StubAgg()
    row = await svc.create(session, agg, "p", _in("false_positive"), user_id="u1", username="oper")
    assert row.status == "applied"
    assert agg.dd.calls == [{"fid": "123", "active": False, "false_p": True,
                             "risk_accepted": False, "out_of_scope": False, "is_mitigated": False}]
    assert row.expires_at is None  # FP no vence


async def test_riesgo_siempre_pasa_por_propuesta_y_aprobacion(session):
    agg = _StubAgg()
    # A1: incluso creada por quien sea, risk_accepted queda proposed (no escribe).
    row = await svc.create(session, agg, "p", _in("risk_accepted"), user_id="u1", username="oper")
    assert row.status == "proposed" and agg.dd.calls == []
    approved = await svc.approve(session, agg, row.id, user_id="a1", username="boss")
    assert approved.status == "applied" and approved.approved_by == "boss"
    assert approved.expires_at is not None
    assert agg.dd.calls[-1]["risk_accepted"] is True and agg.dd.calls[-1]["active"] is False


async def test_approve_solo_risk_accepted(session):
    agg = _StubAgg()
    # Una FP no puede aprobarse (ya está applied, no proposed).
    row = await svc.create(session, agg, "p", _in("false_positive"), user_id="u", username="o")
    with pytest.raises(svc.ExceptionError):
        await svc.approve(session, agg, row.id, user_id="a", username="boss")


async def test_no_apila_excepciones(session):
    agg = _StubAgg()
    await svc.create(session, agg, "p", _in("false_positive", ref="9"), user_id="u", username="o")
    with pytest.raises(svc.ExceptionError):
        await svc.create(session, agg, "p", _in("out_of_scope", ref="9"), user_id="u", username="o")


async def test_sonar_transiciones(session):
    agg = _StubAgg()
    await svc.create(session, agg, "p", _in("false_positive", source="sonarqube", ref="AX-1"), user_id="u", username="o")
    r = await svc.create(session, agg, "p", _in("risk_accepted", source="sonarqube", ref="AX-2"), user_id="u", username="o")
    await svc.approve(session, agg, r.id, user_id="a", username="boss")
    assert [c["tx"] for c in agg.sonar.calls] == ["falsepositive", "accept"]


async def test_revoke_revierte_en_la_fuente(session):
    agg = _StubAgg()
    row = await svc.create(session, agg, "p", _in("false_positive"), user_id="u", username="o")
    revoked = await svc.revoke(session, agg, row.id, user_id="a", username="boss")
    assert revoked.status == "revoked" and agg.dd.calls[-1]["active"] is True


async def _applied_risk(session, agg):
    r = await svc.create(session, agg, "p", _in("risk_accepted"), user_id="u", username="o")
    await svc.approve(session, agg, r.id, user_id="a", username="boss")
    r.expires_at = datetime.now(UTC) - timedelta(days=1)  # backdate: ya venció
    await session.commit()
    return r


async def test_expire_due_revierte_y_alerta(session):
    agg = _StubAgg()
    r = await _applied_risk(session, agg)
    assert await svc.expire_due(session, agg) == 1
    assert (await session.get(SourceException, r.id)).status == "expired"
    assert agg.dd.calls[-1]["active"] is True
    kinds = {e.kind for e in (await session.execute(select(ProjectEvent))).scalars()}
    assert "exception_expired" in kinds


async def test_expire_fail_no_miente_con_expired(session):
    # A2: si no puede revertir en la fuente, queda expire_failed + alerta danger.
    agg = _StubAgg()
    r = await _applied_risk(session, agg)
    agg.dd.fail_revoke = True
    assert await svc.expire_due(session, agg) == 1
    assert (await session.get(SourceException, r.id)).status == "expire_failed"
    events = list((await session.execute(select(ProjectEvent))).scalars())
    assert any(e.kind == "exception_expire_failed" and e.severity == "danger" for e in events)


async def test_list_filtra_por_estado(session):
    agg = _StubAgg()
    await svc.create(session, agg, "p", _in("false_positive", ref="1"), user_id="u", username="o")
    await svc.create(session, agg, "p", _in("risk_accepted", ref="2"), user_id="u", username="o")
    applied = await svc.list_exceptions(session, status="applied")
    proposed = await svc.list_exceptions(session, status="proposed")
    assert len(applied) == 1 and applied[0].finding_ref == "1"
    assert len(proposed) == 1 and proposed[0].kind == "risk_accepted"
