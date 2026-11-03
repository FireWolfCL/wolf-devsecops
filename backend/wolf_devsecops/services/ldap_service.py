"""Autenticación contra un directorio LDAP/AD/FreeIPA (feature Pro, SSO).

Reglas de campo heredadas de la flota (HawkEye / Slurm-View-Piont, ambas ldap3==2.9.1):

1. **Failover manual, una `Server` plana por intento** — NO `ServerPool`: en ldap3 2.9.1
   el pool dispara el mismo bug de `struct.error` que los timeouts float. Se prueban
   TODAS las réplicas antes de rechazar.
2. **Timeouts enteros** — `connect_timeout`/`receive_timeout` float revientan con
   `struct.error` en cualquier bind sobre TLS. El esquema fuerza `int`; aquí se re-castea.
3. **Replication lag** — una réplica puede responder `invalidCredentials` justo tras un
   cambio de password mientras otra ya está al día. Por eso un `invalidCredentials` en UNA
   réplica NO rechaza: se reintenta en las demás; solo se rechaza si TODAS coinciden.
4. **Resultado de 3 estados** — `AUTHENTICATED` / `INVALID_CREDENTIALS` / `UNREACHABLE`.
   El caller trata UNREACHABLE distinto de INVALID: 503 honesto sin contar lockout, no 401.

Síncrono a propósito (ldap3 lo es): el caller lo cruza por `asyncio.to_thread`, igual que
`auth_service` ya hace con argon2. El valor de la password jamás se loguea.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class LdapAuthResult(enum.Enum):
    AUTHENTICATED = "authenticated"
    INVALID_CREDENTIALS = "invalid_credentials"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True)
class LdapConfig:
    """Config del directorio ya resuelta (bind password en claro, descifrada por el
    servicio justo antes de usarla; esta instancia no se persiste ni se loguea)."""

    hosts: list[str]
    base_dn: str
    user_filter: str = "(uid={username})"
    user_dn_template: str | None = None
    bind_dn: str | None = None
    bind_password: str | None = None
    use_ssl: bool = True
    start_tls: bool = False
    tls_verify: str = "required"
    tls_ca_pem: str | None = None  # CA interna (PEM) para validar el cert del DC
    timeout_seconds: int = 8
    extra: dict = field(default_factory=dict)


def _tls_object(cfg: LdapConfig):
    import ssl

    from ldap3 import Tls

    # Fail-safe por defecto: solo "relaxed" desactiva la validación; cualquier otro valor
    # (incluido uno inesperado) exige certificado válido. ldap3 valida además el hostname
    # contra el host configurado cuando validate != CERT_NONE.
    validate = ssl.CERT_NONE if cfg.tls_verify == "relaxed" else ssl.CERT_REQUIRED
    # ca_certs_data: PEM de la CA corporativa (AD/FreeIPA suelen usar una CA interna que no
    # está en el trust store del SO). Permite sostener "required" sin caer en CERT_NONE.
    ca_data = cfg.tls_ca_pem.strip() if cfg.tls_ca_pem else None
    return Tls(validate=validate, ca_certs_data=ca_data) if ca_data else Tls(validate=validate)


def _build_server(host: str, cfg: LdapConfig):
    from ldap3 import Server

    tls = _tls_object(cfg) if (cfg.use_ssl or cfg.start_tls) else None
    return Server(
        host,
        use_ssl=cfg.use_ssl,
        connect_timeout=int(cfg.timeout_seconds),  # int obligatorio (gotcha ldap3 2.9.1)
        tls=tls,
    )


def _bind(server, user: str, password: str, cfg: LdapConfig):
    """Abre una conexión NUEVA y bindea. Devuelve el objeto Connection ya bindeado o
    lanza LDAPException. Nunca reutiliza una conexión entre bind de servicio y de usuario."""
    from ldap3 import Connection

    conn = Connection(
        server,
        user=user,
        password=password,
        receive_timeout=int(cfg.timeout_seconds),  # int obligatorio
        auto_bind=False,
        raise_exceptions=False,
    )
    if cfg.start_tls and not cfg.use_ssl:
        conn.open()
        conn.start_tls()
    conn.bind()
    return conn


def _is_invalid_credentials(conn) -> bool:
    """¿El bind falló por credenciales inválidas (y no por transporte)?"""
    result = getattr(conn, "result", None) or {}
    desc = str(result.get("description", "")).lower()
    return desc in {"invalidcredentials", "invalid_credentials"} or result.get("result") == 49


def _escaped_filter(user_filter: str, username: str) -> str:
    from ldap3.utils.conv import escape_filter_chars

    return user_filter.replace("{username}", escape_filter_chars(username))


def _escaped_dn(template: str, username: str) -> str:
    from ldap3.utils.dn import escape_rdn

    return template.replace("{username}", escape_rdn(username))


def _try_host(host: str, cfg: LdapConfig, username: str, password: str) -> LdapAuthResult:
    """Un intento contra UNA réplica. Excepción de transporte/TLS → UNREACHABLE para
    esta réplica (el caller prueba la siguiente); jamás cuenta como password mala."""
    try:
        server = _build_server(host, cfg)

        if cfg.user_dn_template:
            # Bind directo: el DN del usuario sale de la plantilla.
            user_dn = _escaped_dn(cfg.user_dn_template, username)
            conn = _bind(server, user_dn, password, cfg)
            if conn.bound:
                return LdapAuthResult.AUTHENTICATED
            return (
                LdapAuthResult.INVALID_CREDENTIALS
                if _is_invalid_credentials(conn)
                else LdapAuthResult.UNREACHABLE
            )

        # search+bind: bind de servicio → buscar el DN → re-bind como el usuario.
        # Sin bind password es un unauthenticated/anonymous bind (RFC 4513): misconfig,
        # no autentica a nadie con garantías → UNREACHABLE (se ve en /test, no silencioso).
        if not (cfg.bind_password or "").strip():
            return LdapAuthResult.UNREACHABLE
        svc = _bind(server, cfg.bind_dn or "", cfg.bind_password, cfg)
        if not svc.bound:
            # El bind de SERVICIO falló: no podemos autenticar a nadie en esta réplica.
            # Es misconfig, no password del usuario → UNREACHABLE (se ve en /test).
            return LdapAuthResult.UNREACHABLE
        svc.search(cfg.base_dn, _escaped_filter(cfg.user_filter, username), attributes=["cn"])
        if not svc.entries:
            # Usuario no existe en el directorio: respuesta uniforme con password mala.
            return LdapAuthResult.INVALID_CREDENTIALS
        user_dn = svc.entries[0].entry_dn
        conn = _bind(server, user_dn, password, cfg)
        if conn.bound:
            return LdapAuthResult.AUTHENTICATED
        return (
            LdapAuthResult.INVALID_CREDENTIALS
            if _is_invalid_credentials(conn)
            else LdapAuthResult.UNREACHABLE
        )
    except Exception:
        # Defensa en profundidad: CUALQUIER excepción (LDAP, ssl.SSLError, el struct.error
        # del gotcha de timeouts, TimeoutError) → UNREACHABLE. Nunca se propaga un 500 ni
        # un traceback cuyo repr de Connection podría incluir la password en claro.
        return LdapAuthResult.UNREACHABLE


def authenticate_ldap(cfg: LdapConfig, username: str, password: str) -> LdapAuthResult:
    """Bindea `username`/`password` contra el directorio, probando TODAS las réplicas.

    - Alguna réplica AUTHENTICATED → AUTHENTICATED (corta en la primera que valida).
    - Ninguna valida pero alguna dio INVALID_CREDENTIALS → INVALID_CREDENTIALS.
    - Todas las réplicas inalcanzables → UNREACHABLE (el caller responde 503, sin lockout).

    Una password vacía se rechaza de entrada (un bind LDAP anónimo con password vacía
    puede devolver "éxito" en algunos directorios: sería un bypass de auth)."""
    if not password:
        return LdapAuthResult.INVALID_CREDENTIALS
    if not cfg.hosts:
        return LdapAuthResult.UNREACHABLE

    # Presupuesto total: con muchas réplicas, N×timeout retendría el hilo demasiado
    # (riesgo DoS, T-LDAP-01). Se prueban réplicas hasta agotar ~2×timeout; el failover
    # típico (2 réplicas) no se ve afectado.
    import time

    deadline = time.monotonic() + max(int(cfg.timeout_seconds), 1) * 2
    saw_invalid = False
    for host in cfg.hosts:
        result = _try_host(host, cfg, username, password)
        if result is LdapAuthResult.AUTHENTICATED:
            return LdapAuthResult.AUTHENTICATED
        if result is LdapAuthResult.INVALID_CREDENTIALS:
            saw_invalid = True  # recordar, pero seguir (replication lag)
        if time.monotonic() > deadline:
            break  # no retener el hilo más allá del presupuesto
    return LdapAuthResult.INVALID_CREDENTIALS if saw_invalid else LdapAuthResult.UNREACHABLE
