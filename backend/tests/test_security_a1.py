"""Tests de las primitivas criptográficas de auth (A1, ADR-013).

Cubre: hashing argon2id (ok/mala/formato), JWT de sesión (claim `tv` + expiry +
rechazo cruzado access↔mfa), TOTP (válido/inválido/ventana), round-trip Fernet del
secreto MFA, y recovery codes de un solo uso.
"""

from __future__ import annotations

import time

import pyotp
import pytest
from jose import JWTError, jwt

from wolf_devsecops.auth import security
from wolf_devsecops.rbac.roles import Role
from wolf_devsecops.settings import get_settings


# --- Password hashing (argon2id) ------------------------------------------
def test_hash_password_verifica_ok_y_mala():
    h = security.hash_password("correct horse battery staple")
    assert security.verify_password("correct horse battery staple", h) is True
    assert security.verify_password("wrong password", h) is False


def test_hash_es_argon2id():
    h = security.hash_password("whatever-123")
    assert h.startswith("$argon2id$"), f"se esperaba argon2id, se obtuvo: {h[:20]}"


def test_hash_no_es_deterministico():
    # Sal por hash: dos hashes de la misma password difieren.
    assert security.hash_password("same") != security.hash_password("same")


def test_verify_no_lanza_ante_hash_corrupto():
    assert security.verify_password("x", "no-es-un-hash-valido") is False


def test_needs_rehash_argon2id_fresco_es_false():
    assert security.needs_rehash(security.hash_password("fresh")) is False


# --- JWT de sesión ---------------------------------------------------------
def test_access_token_encode_decode_con_tv_y_role():
    token = security.create_access_token("user-123", Role.admin, token_version=7)
    payload = security.decode_access_token(token)
    assert payload["sub"] == "user-123"
    assert payload["role"] == "admin"
    assert payload["tv"] == 7
    assert payload["type"] == "access"
    assert "exp" in payload


def test_access_token_acepta_role_como_string():
    token = security.create_access_token("u", "viewer", token_version=0)
    assert security.decode_access_token(token)["role"] == "viewer"


def test_access_token_expirado_es_rechazado():
    settings = get_settings()
    # Emite un token ya expirado firmado con el mismo secreto/algoritmo.
    expired = jwt.encode(
        {"sub": "u", "type": "access", "exp": int(time.time()) - 10},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(JWTError):
        security.decode_access_token(expired)


def test_access_token_firma_invalida_es_rechazada():
    token = security.create_access_token("u", Role.viewer, token_version=0)
    with pytest.raises(JWTError):
        security.decode_access_token(token + "tampered")


# --- Rechazo cruzado access <-> mfa ---------------------------------------
def test_mfa_token_rechazado_como_access():
    mfa = security.create_mfa_token("user-123")
    with pytest.raises(JWTError):
        security.decode_access_token(mfa)


def test_access_token_rechazado_como_mfa():
    access = security.create_access_token("user-123", Role.operator, token_version=1)
    with pytest.raises(JWTError):
        security.decode_mfa_token(access)


def test_mfa_token_decode_ok_y_sin_autoridad():
    mfa = security.create_mfa_token("user-123")
    payload = security.decode_mfa_token(mfa)
    assert payload["sub"] == "user-123"
    assert payload["type"] == "mfa"
    # No autoriza nada por sí mismo: no lleva role ni tv.
    assert "role" not in payload
    assert "tv" not in payload


# --- TOTP ------------------------------------------------------------------
def test_totp_valido():
    secret = security.generate_totp_secret()
    code = pyotp.TOTP(secret).now()
    assert security.verify_totp_code(secret, code) is True


def test_totp_invalido():
    secret = security.generate_totp_secret()
    assert security.verify_totp_code(secret, "000000") is False


def test_totp_ventana_tolera_periodo_anterior():
    secret = security.generate_totp_secret()
    totp = pyotp.TOTP(secret)
    prev_code = totp.at(time.time() - 30)  # código del período anterior
    assert security.verify_totp_code(secret, prev_code, valid_window=1) is True
    # Con ventana 0 el código del período anterior ya no sirve.
    assert security.verify_totp_code(secret, prev_code, valid_window=0) is False


def test_totp_uri_contiene_issuer_y_cuenta():
    secret = security.generate_totp_secret()
    uri = security.get_totp_uri(secret, "alice@example.com", issuer="Wolf DevSecOps")
    assert uri.startswith("otpauth://totp/")
    assert "Wolf%20DevSecOps" in uri
    assert "alice%40example.com" in uri


# --- Cifrado del secreto MFA en reposo (Fernet) ---------------------------
def test_fernet_mfa_round_trip():
    secret = security.generate_totp_secret()
    ciphertext = security.encrypt_mfa_secret(secret)
    assert ciphertext != secret  # no está en claro
    assert security.decrypt_mfa_secret(ciphertext) == secret


def test_decrypt_mfa_secret_rechaza_ciphertext_invalido():
    with pytest.raises(ValueError):
        security.decrypt_mfa_secret("no-es-un-token-fernet")


# --- Recovery codes (un solo uso) -----------------------------------------
def test_generate_recovery_codes_cantidad_y_unicidad():
    codes = security.generate_recovery_codes(n=10)
    assert len(codes) == 10
    assert len(set(codes)) == 10  # alta entropía: sin colisiones


def test_hash_recovery_code_es_sha256_normalizado():
    # 64 hex chars y normalización (strip + lower) → mismo hash.
    h = security.hash_recovery_code("AB12-CD34")
    assert len(h) == 64
    assert security.hash_recovery_code("  ab12-cd34  ") == h


def test_recovery_code_un_solo_uso():
    codes = security.generate_recovery_codes(n=3)
    hashed = [security.hash_recovery_code(c) for c in codes]

    # Primer uso del código 0: consume y devuelve la lista sin ese hash.
    new_list = security.verify_and_consume_recovery_code(codes[0], hashed)
    assert new_list is not None
    assert len(new_list) == 2
    assert security.hash_recovery_code(codes[0]) not in new_list

    # Reusar el mismo código contra la lista nueva: ya no matchea.
    assert security.verify_and_consume_recovery_code(codes[0], new_list) is None

    # Otro código sigue siendo válido una vez.
    assert security.verify_and_consume_recovery_code(codes[1], new_list) is not None


def test_recovery_code_desconocido_devuelve_none():
    hashed = [security.hash_recovery_code(c) for c in security.generate_recovery_codes(n=2)]
    assert security.verify_and_consume_recovery_code("zzzz-zzzz", hashed) is None


def test_recovery_code_lista_vacia_no_lanza():
    assert security.verify_and_consume_recovery_code("algo", []) is None


# --- QR SVG ----------------------------------------------------------------
def test_generate_qr_code_svg():
    secret = security.generate_totp_secret()
    uri = security.get_totp_uri(secret, "bob", issuer="Wolf DevSecOps")
    svg = security.generate_qr_code_svg(uri)
    assert isinstance(svg, str)
    assert "<svg" in svg
