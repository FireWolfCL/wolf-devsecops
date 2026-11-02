"""Primitivas criptográficas de autenticación (A1, ADR-013).

Porta `WIF/wif-server/app/core/security.py` (fuente probada en producción) y lo
endurece a **argon2id** para el hashing de password (patrón de
`Check4Work/server/services/identity_service.py`), que es memory-hard y no sufre
el truncado a 72 bytes de bcrypt.

Este módulo es SOLO primitivas: no toca la base de datos, no decide auth y no
emite respuestas HTTP. La capa de servicio (A2 `auth_service`/`user_service`) y
las dependencias de FastAPI (A3 `auth/dependencies.py`) lo consumen.

Decisiones de seguridad (ver cada función):
- Password → argon2id, con bcrypt como verify-only para importar hashes ajenos.
- JWT de sesión → firmado HS256, con claim `tv` (token_version) para revocación
  sin tabla de sesiones, `type="access"` y `exp` obligatorio (nunca sin expiry).
- Token MFA → JWT corto `type="mfa"`, de un solo propósito (cruzar /auth/mfa/verify).
- Secreto TOTP en reposo → cifrado Fernet (AES-128-CBC + HMAC, AEAD), clave
  dedicada `mfa_fernet_key` (≠ clave de firma JWT: separación de claves).
- Recovery codes → se muestran UNA vez en claro; en BD solo su hash SHA-256; un
  solo uso, verificados en tiempo constante.
"""

from __future__ import annotations

import base64
import hashlib
import io
import secrets
import time

import pyotp
import qrcode
import qrcode.image.svg
from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt
from passlib.context import CryptContext

from ..rbac.roles import Role
from ..settings import get_settings

# argon2id es el KDF preferido para passwords (memory-hard, sin el límite de 72
# bytes de bcrypt). bcrypt se mantiene SOLO para verificar hashes heredados o
# importados de otra instancia; passlib re-hashea al siguiente login
# (`deprecated="auto"` + `needs_rehash`). NUNCA usar este contexto para tokens de
# alta entropía (esos se hashean one-way con SHA-256, no con un KDF de password).
_pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")

# Discriminadores del claim `type` (no son secretos: S105 es falso positivo).
_TOKEN_TYPE_ACCESS = "access"  # noqa: S105
_TOKEN_TYPE_MFA = "mfa"  # noqa: S105


# ---------------------------------------------------------------------------
# Password hashing (argon2id)
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """Hashea una password con argon2id. El resultado lleva el algoritmo, los
    parámetros y la sal embebidos (prefijo `$argon2id$`)."""
    return _pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Verifica una password contra su hash (argon2id o bcrypt heredado). Nunca
    lanza ante un hash corrupto: devuelve False (fail-closed)."""
    try:
        return _pwd_context.verify(password, hashed_password)
    except ValueError:
        return False


def needs_rehash(hashed_password: str) -> bool:
    """True si el hash usa un esquema o parámetros obsoletos (p. ej. un bcrypt
    heredado, o un argon2id con coste anterior). La capa de servicio re-hashea con
    `hash_password` en el próximo login exitoso."""
    return _pwd_context.needs_update(hashed_password)


# ---------------------------------------------------------------------------
# JWT de sesión (con claim `tv` para revocación) y token MFA
# ---------------------------------------------------------------------------
def create_access_token(
    subject: str,
    role: Role | str,
    token_version: int,
    extra: dict | None = None,
) -> str:
    """Emite un JWT de sesión firmado.

    Claims: `sub` (id del usuario como string), `role`, `tv` (token_version al
    momento de emitir — fuente de la revocación: A3 lo compara contra
    `users.token_version` y rechaza con 401 si no coincide), `type="access"` y
    `exp` (siempre presente; `access_token_expire_minutes`). `extra` permite
    añadir claims no sensibles sin que pueda pisar los reservados.
    """
    settings = get_settings()
    role_value = role.value if isinstance(role, Role) else str(role)
    payload: dict = dict(extra or {})
    payload.update(
        {
            "sub": subject,
            "role": role_value,
            "tv": token_version,
            "type": _TOKEN_TYPE_ACCESS,
        }
    )
    return _encode(payload, settings.access_token_expire_minutes)


def decode_access_token(token: str) -> dict:
    """Decodifica y verifica firma + expiry de un token de SESIÓN y devuelve el
    payload. Rechaza (JWTError) si la firma o el `exp` no validan, o si el token
    no es de tipo `access` (p. ej. un token MFA presentado como sesión)."""
    payload = _decode(token)
    if payload.get("type") != _TOKEN_TYPE_ACCESS:
        raise JWTError("expected an access token")
    return payload


def create_mfa_token(subject: str) -> str:
    """Emite un JWT CORTO de un solo propósito: llevar al usuario a través del
    paso MFA (/auth/mfa/verify). No autoriza nada por sí mismo: no lleva `role`
    ni `tv`, y expira en `mfa_token_expire_minutes`."""
    settings = get_settings()
    payload = {"sub": subject, "type": _TOKEN_TYPE_MFA}
    return _encode(payload, settings.mfa_token_expire_minutes)


def decode_mfa_token(token: str) -> dict:
    """Decodifica y verifica un token MFA. Rechaza (JWTError) si la firma/exp no
    validan o si el token no es de tipo `mfa` (p. ej. un token de sesión
    presentado en el paso MFA)."""
    payload = _decode(token)
    if payload.get("type") != _TOKEN_TYPE_MFA:
        raise JWTError("expected an mfa token")
    return payload


def _encode(payload: dict, expire_minutes: int) -> str:
    """Firma `payload` añadiendo `exp` (segundos epoch UTC, siempre presente: un
    token sin expiry no se emite)."""
    settings = get_settings()
    to_encode = dict(payload)
    to_encode["exp"] = int(time.time()) + expire_minutes * 60
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode(token: str) -> dict:
    """Decodifica validando firma y expiry contra el algoritmo configurado, en
    una allowlist estricta de un solo algoritmo (rechaza `alg:none` y la confusión
    HS/RS). Lanza JWTError (incluye ExpiredSignatureError) si no valida."""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


# ---------------------------------------------------------------------------
# Cifrado del secreto TOTP en reposo (Fernet, clave dedicada)
# ---------------------------------------------------------------------------
def _fernet() -> Fernet:
    """Construye el Fernet desde `mfa_fernet_key`.

    Acepta una clave Fernet nativa (32 bytes urlsafe-b64, lo que genera
    `Fernet.generate_key()` — el formato recomendado) y, de forma tolerante, una
    passphrase arbitraria, de la que deriva una clave con SHA-256 (patrón WIF). Es
    una clave de propósito ÚNICO: cifra el secreto MFA, nunca firma JWT."""
    key = get_settings().mfa_fernet_key
    raw = key.encode("utf-8") if isinstance(key, str) else key
    try:
        return Fernet(raw)
    except (ValueError, TypeError):
        digest = hashlib.sha256(raw).digest()
        return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_mfa_secret(secret: str) -> str:
    """Cifra el secreto TOTP para guardarlo en `users.mfa_secret` (nunca en claro)."""
    return _fernet().encrypt(secret.encode("utf-8")).decode("utf-8")


def decrypt_mfa_secret(token: str) -> str:
    """Descifra el secreto TOTP. Lanza ValueError si el ciphertext es inválido o
    la clave no corresponde (nunca devuelve basura silenciosamente)."""
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("secreto MFA inválido o corrupto") from exc


# ---------------------------------------------------------------------------
# TOTP (pyotp)
# ---------------------------------------------------------------------------
def generate_totp_secret() -> str:
    """Genera un secreto TOTP base32 nuevo (el que se cifra con Fernet en reposo)."""
    return pyotp.random_base32()


def get_totp_uri(secret: str, account: str, issuer: str = "Wolf DevSecOps") -> str:
    """Construye el otpauth:// URI que la app autenticadora escanea. `account`
    identifica la cuenta (username/email); `issuer` etiqueta la entrada."""
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=issuer)


def verify_totp_code(secret: str, code: str, valid_window: int = 1) -> bool:
    """Verifica un código TOTP. `valid_window=1` tolera ±1 período (±30 s) por
    desfase de reloj. pyotp compara el dígito en tiempo constante."""
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=valid_window)


# ---------------------------------------------------------------------------
# Recovery codes (de un solo uso)
# ---------------------------------------------------------------------------
def generate_recovery_codes(n: int = 10) -> list[str]:
    """Genera `n` recovery codes de alta entropía, que se muestran al usuario UNA
    sola vez al activar MFA. En la BD solo se guardan sus hashes
    (`hash_recovery_code`), nunca el código en claro."""
    return [f"{secrets.token_hex(4)}-{secrets.token_hex(4)}" for _ in range(n)]


def hash_recovery_code(code: str) -> str:
    """SHA-256 hex de un recovery code, normalizado (strip + lower), tal como se
    guarda en `users.mfa_recovery_codes`. SHA-256 es correcto aquí porque el
    código es de alta entropía (no una password): no necesita un KDF lento."""
    return hashlib.sha256(code.strip().lower().encode("utf-8")).hexdigest()


def verify_and_consume_recovery_code(code: str, hashed_list: list[str]) -> list[str] | None:
    """Verifica `code` contra la lista de hashes almacenada y, si coincide,
    devuelve una NUEVA lista sin el hash consumido (semántica de un solo uso). Si
    no coincide con ninguno, devuelve None. La comparación es en tiempo constante
    (`secrets.compare_digest`) para no filtrar por timing qué código acertó.

    No muta `hashed_list`: devuelve una copia para que la capa de servicio la
    persista explícitamente."""
    candidate = hash_recovery_code(code)
    matched_index: int | None = None
    for i, stored in enumerate(hashed_list or []):
        if secrets.compare_digest(candidate, stored):
            matched_index = i
            # No cortamos el bucle: recorrer todo evita un canal lateral por
            # timing según la posición del código acertado.
    if matched_index is None:
        return None
    return [h for i, h in enumerate(hashed_list) if i != matched_index]


# ---------------------------------------------------------------------------
# QR del enrolamiento MFA (SVG, sin PIL)
# ---------------------------------------------------------------------------
def generate_qr_code_svg(uri: str) -> str:
    """Renderiza un otpauth:// URI como SVG inline para la pantalla de
    enrolamiento MFA. Usa el image_factory SVG de qrcode (sin dependencia de
    PIL/Pillow); el frontend lo inyecta con `dangerouslySetInnerHTML`."""
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")
