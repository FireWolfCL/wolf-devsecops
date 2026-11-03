"""Licencia firmada (fase 2 del gate de edición). ADR-020-bis.

El producto VERIFICA licencias firmadas por la WLA (Wolf-Licenses) con ES256/JWS:
embebe la llave PÚBLICA (`wolf-devsecops-license.pub`) y nunca ve la privada. Una
licencia válida determina la edición; ausente/ inválida/ vencida → se degrada (ver
`edition.current_edition`). La emisión (firma con la privada) vive en la WLA; aquí
hay un `issue_license` solo para pruebas y para la CLI del emisor.

Seguridad: la llave privada NUNCA está en este repo. El token no lleva secretos.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
from jwt import InvalidTokenError

PRODUCT = "wolf-devsecops"
_ALG = "ES256"
_PUBKEY_FILE = Path(__file__).with_name("wolf-devsecops-license.pub")

# Caché de verificación de FIRMA por token (lo caro es la cripto; la expiración se
# evalúa en vivo sobre las claims cacheadas, para que un token caduque sin reinicio).
_sig_cache: dict[str, dict | None] = {}


def _public_key() -> str:
    return _PUBKEY_FILE.read_text(encoding="utf-8")


@dataclass(frozen=True)
class LicenseClaims:
    product: str
    edition: str
    customer: str
    issued_at: datetime | None
    expires_at: datetime | None

    def is_valid(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        if self.product != PRODUCT:
            return False
        if self.expires_at is None:
            return False
        return now < self.expires_at


def _decode(token: str) -> dict | None:
    """Verifica la FIRMA (no la expiración) y devuelve el payload, o None si la
    firma/el formato son inválidos. Cacheado por token."""
    if token in _sig_cache:
        return _sig_cache[token]
    try:
        payload = jwt.decode(
            token, _public_key(), algorithms=[_ALG],
            options={"verify_exp": False, "verify_aud": False},
        )
    except InvalidTokenError:
        payload = None
    _sig_cache[token] = payload
    return payload


def _ts_to_dt(ts: object) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(ts), tz=UTC)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def parse(token: str) -> LicenseClaims | None:
    """Token → claims (firma verificada). None si la firma es inválida."""
    payload = _decode(token)
    if payload is None:
        return None
    return LicenseClaims(
        product=str(payload.get("product", "")),
        edition=str(payload.get("edition", "community")),
        customer=str(payload.get("customer", "")),
        issued_at=_ts_to_dt(payload.get("iat")),
        expires_at=_ts_to_dt(payload.get("exp")),
    )


def _read_token(raw: str) -> str:
    """WOLF_LICENSE puede ser el token o '@/ruta/al/archivo'."""
    raw = (raw or "").strip()
    if raw.startswith("@"):
        try:
            return Path(raw[1:]).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return raw


def load(settings) -> LicenseClaims | None:
    """Claims de la licencia del despliegue (WOLF_LICENSE), o None si no hay."""
    token = _read_token(getattr(settings, "license", "") or "")
    if not token:
        return None
    return parse(token)


def effective_edition(settings, now: datetime | None = None) -> str | None:
    """Edición que concede una licencia VÁLIDA (firma + producto + no vencida), o None."""
    claims = load(settings)
    if claims is not None and claims.is_valid(now):
        return claims.edition
    return None


def status(settings, now: datetime | None = None) -> dict:
    """Estado de la licencia para /api/meta (sin exponer el token)."""
    now = now or datetime.now(UTC)
    token = _read_token(getattr(settings, "license", "") or "")
    if not token:
        return {"present": False, "valid": False}
    claims = parse(token)
    if claims is None:
        return {"present": True, "valid": False, "error": "invalid_signature"}
    valid = claims.is_valid(now)
    days_left = None
    if claims.expires_at is not None:
        days_left = (claims.expires_at - now).days
    return {
        "present": True,
        "valid": valid,
        "edition": claims.edition,
        "customer": claims.customer,
        "expires_at": claims.expires_at.isoformat() if claims.expires_at else None,
        "days_left": days_left,
        "error": None if valid else ("expired" if claims.product == PRODUCT else "wrong_product"),
    }


def issue_license(
    *, edition: str, customer: str, days: int, private_key_pem: str,
    product: str = PRODUCT, now: datetime | None = None,
) -> str:
    """Firma una licencia (LADO WLA / pruebas). Requiere la llave PRIVADA en PEM.
    El producto solo verifica; esto no se usa con secretos del repo."""
    now = now or datetime.now(UTC)
    claims = {
        "product": product,
        "edition": edition,
        "customer": customer,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=days)).timestamp()),
    }
    return jwt.encode(claims, private_key_pem, algorithm=_ALG)
