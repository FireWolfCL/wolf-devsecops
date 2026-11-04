"""E4 — cargador de licencia por la web: /api/v1/settings/license (BD>env).

Verifica que subir el token JWS por el panel (admin+MFA) fija la edición con
precedencia sobre el entorno, que la validación de firma/producto rechaza con 422
ANTES de guardar, que borrar revierte al WOLF_LICENSE del entorno, y que el gate
admin+MFA protege los tres verbos.

Sigue el patrón de test_settings_a4 (SQLite temporal + Bearer de create_access_token)
y de test_licensing (par EC efímero; la pública se embebe con monkeypatch, sin tocar
la privada real de la WLA).
"""

from __future__ import annotations

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from wolf_devsecops import licensing
from wolf_devsecops.api import deps
from wolf_devsecops.auth import security
from wolf_devsecops.db import engine as db_engine
from wolf_devsecops.db.base import Base
from wolf_devsecops.main import create_app
from wolf_devsecops.models_db.user import User
from wolf_devsecops.rbac.roles import Role
from wolf_devsecops.services import config_service
from wolf_devsecops.settings import get_settings

LIC = "/api/v1/settings/license"


@pytest.fixture
def keypair(monkeypatch):
    """Par EC P-256 efímero; embebe la pública como la del producto. Limpia caché."""
    priv = ec.generate_private_key(ec.SECP256R1())
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    pub_pem = priv.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    monkeypatch.setattr(licensing, "_public_key", lambda: pub_pem)
    licensing._sig_cache.clear()
    yield priv_pem
    licensing._sig_cache.clear()


@pytest.fixture
async def env(tmp_path):
    """App + SQLite temporal + 3 usuarios (admin con MFA, admin sin MFA, operator).

    Fija WOLF_LICENSE del entorno a "" (baseline vacío) y resetea el baseline
    capturado en config_service, para que cada test controle la precedencia BD>env.
    """
    settings = get_settings()
    prev_lic, prev_req, prev_ed = settings.license, settings.license_required, settings.edition
    settings.database_url = f"sqlite+aiosqlite:///{tmp_path}/license_e4.db"
    settings.license = ""           # entorno sin licencia (baseline por defecto)
    settings.license_required = False
    config_service.reset_license_baseline()  # recaptura "" en el próximo resolve

    await db_engine.dispose_engine()
    async with db_engine.get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    headers: dict[str, dict] = {}
    async with db_engine.get_sessionmaker()() as s:
        specs = [
            ("admin", Role.admin, True),       # admin con MFA → pasa el gate
            ("admin_nomfa", Role.admin, False),  # admin sin MFA → 403 en acciones admin
            ("operator", Role.operator, False),  # rol insuficiente → 403
        ]
        for name, role, mfa in specs:
            u = User(username=f"u-{name}", role=role, is_active=True, token_version=0,
                     mfa_enabled=mfa)
            s.add(u)
            await s.flush()
            tok = security.create_access_token(subject=u.id, role=role.value, token_version=0)
            headers[name] = {"Authorization": f"Bearer {tok}"}
        await s.commit()

    await config_service.refresh_config()  # captura baseline "" y carga config vacía
    deps.reset_aggregator()
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        yield c, headers

    # Teardown: restaura los settings globales y olvida el baseline (otros tests
    # recapturan su propio entorno, que es "").
    settings.license, settings.license_required, settings.edition = prev_lic, prev_req, prev_ed
    config_service.reset_license_baseline()
    config_service.invalidate_config()
    deps.reset_aggregator()
    await db_engine.dispose_engine()


async def _meta_edition(c: httpx.AsyncClient) -> str:
    return (await c.get("/api/meta")).json()["edition"]


# ─── Precedencia BD > flag ────────────────────────────────────────────────────
async def test_put_token_valido_fija_edicion_bd_sobre_flag(env, keypair):
    c, h = env
    get_settings().edition = "community"  # el flag dice community…
    token = licensing.issue_license(
        edition="pro", customer="ACME", days=30, private_key_pem=keypair
    )
    r = await c.put(LIC, headers=h["admin"], json={"token": token})
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True and body["edition"] == "pro"
    assert body["customer"] == "ACME" and body["source"] == "db"
    # …pero la licencia de BD manda: el dashboard ve "pro" sin reiniciar.
    assert await _meta_edition(c) == "pro"
    # El GET nunca devuelve el token, solo el estado + origen.
    g = await c.get(LIC, headers=h["admin"])
    assert g.status_code == 200 and g.json()["source"] == "db"
    assert "token" not in g.json()


# ─── Validación antes de guardar ──────────────────────────────────────────────
async def test_put_firma_invalida_422_y_no_guarda(env, keypair):
    c, h = env
    get_settings().edition = "community"
    good = licensing.issue_license(edition="pro", customer="X", days=30, private_key_pem=keypair)
    tampered = good[:-3] + ("aaa" if not good.endswith("aaa") else "bbb")
    r = await c.put(LIC, headers=h["admin"], json={"token": tampered})
    assert r.status_code == 422 and "firma" in r.json()["detail"].lower()
    # No se guardó nada: la edición sigue viniendo del flag (community).
    assert await _meta_edition(c) == "community"
    async with db_engine.get_sessionmaker()() as s:
        assert await config_service.get_license_token(s) is None


async def test_put_producto_equivocado_422(env, keypair):
    c, h = env
    token = licensing.issue_license(
        edition="enterprise", customer="X", days=30, private_key_pem=keypair, product="otro"
    )
    r = await c.put(LIC, headers=h["admin"], json={"token": token})
    assert r.status_code == 422
    msg = r.json()["detail"].lower()
    assert "producto" in msg and "otro" in msg


# ─── Borrado revierte al entorno ──────────────────────────────────────────────
async def test_delete_revierte_al_token_del_entorno(env, keypair):
    c, h = env
    s = get_settings()
    s.edition = "community"
    # Simula WOLF_LICENSE del entorno = licencia enterprise, y recaptura el baseline.
    env_token = licensing.issue_license(
        edition="enterprise", customer="ENV-CORP", days=30, private_key_pem=keypair
    )
    s.license = env_token
    config_service.reset_license_baseline()  # el próximo resolve captura env_token

    # BD sube una licencia pro → manda sobre el entorno.
    db_token = licensing.issue_license(
        edition="pro", customer="DB-CORP", days=30, private_key_pem=keypair
    )
    assert (await c.put(LIC, headers=h["admin"], json={"token": db_token})).status_code == 200
    assert await _meta_edition(c) == "pro"

    # Al borrar, se revierte al token del entorno (enterprise), no a "ninguna".
    d = await c.delete(LIC, headers=h["admin"])
    assert d.status_code == 200
    assert d.json()["source"] == "env" and d.json()["edition"] == "enterprise"
    assert await _meta_edition(c) == "enterprise"


# ─── Gate admin + MFA ─────────────────────────────────────────────────────────
async def test_gate_admin_mfa(env, keypair):
    c, h = env
    token = licensing.issue_license(edition="pro", customer="X", days=30, private_key_pem=keypair)
    body = {"token": token}
    # Anónimo → 401 en los tres verbos.
    assert (await c.get(LIC)).status_code == 401
    assert (await c.put(LIC, json=body)).status_code == 401
    assert (await c.delete(LIC)).status_code == 401
    # Operator (rol insuficiente) → 403.
    assert (await c.get(LIC, headers=h["operator"])).status_code == 403
    assert (await c.put(LIC, headers=h["operator"], json=body)).status_code == 403
    # Admin SIN MFA → 403 (T-AUTH-04): no puede administrar hasta enrolar MFA.
    assert (await c.get(LIC, headers=h["admin_nomfa"])).status_code == 403
    assert (await c.put(LIC, headers=h["admin_nomfa"], json=body)).status_code == 403
    assert (await c.delete(LIC, headers=h["admin_nomfa"])).status_code == 403
