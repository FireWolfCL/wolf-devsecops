"""Licencia firmada (fase 2): verificación ES256, expiración y precedencia."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from wolf_devsecops import edition, licensing
from wolf_devsecops.settings import get_settings


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


@pytest.fixture(autouse=True)
def _reset_license():
    s = get_settings()
    prev_lic, prev_req = s.license, s.license_required
    yield
    s.license, s.license_required = prev_lic, prev_req


def test_licencia_valida_concede_edicion(keypair):
    token = licensing.issue_license(edition="pro", customer="ACME", days=30, private_key_pem=keypair)
    claims = licensing.parse(token)
    assert claims is not None
    assert claims.product == licensing.PRODUCT
    assert claims.edition == "pro"
    assert claims.is_valid()


def test_licencia_vencida_no_vale(keypair):
    token = licensing.issue_license(edition="pro", customer="ACME", days=-1, private_key_pem=keypair)
    claims = licensing.parse(token)
    assert claims is not None and not claims.is_valid()  # firma OK, pero vencida


def test_producto_erroneo_no_vale(keypair):
    token = licensing.issue_license(
        edition="enterprise", customer="X", days=30, private_key_pem=keypair, product="otro"
    )
    assert not licensing.parse(token).is_valid()


def test_token_manipulado_no_verifica(keypair):
    token = licensing.issue_license(edition="pro", customer="ACME", days=30, private_key_pem=keypair)
    tampered = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    assert licensing.parse(tampered) is None


def test_precedencia_licencia_sobre_flag(keypair):
    s = get_settings()
    s.edition = "community"  # el flag dice community…
    token = licensing.issue_license(edition="enterprise", customer="ACME", days=30, private_key_pem=keypair)
    s.license = token
    s.license_required = False
    assert edition.current_edition() == "enterprise"  # …pero la licencia manda


def test_license_required_ignora_el_flag(keypair):
    s = get_settings()
    s.edition = "enterprise"  # el flag dice enterprise…
    s.license = ""            # …pero no hay licencia…
    s.license_required = True  # …y se exige → community
    assert edition.current_edition() == "community"
