"""DTOs Pydantic de la capa de administración (A2: auth / sesión / MFA / perfil).

Espejo podado de `WIF/wif-server/app/schemas/user.py`, single-tenant y login por
`username` (ADR-013; decisión abierta #1 del doc de arquitectura, resuelta aquí a
favor de `username`, con `email` opcional solo para el otpauth:// URI).

A4 ampliará este módulo con los DTOs de users-admin (UserCreate/UserUpdate/
UserListResponse/…) y de settings. Aquí viven solo los que A2 necesita.

Nota: NO se usa `EmailStr` a propósito — `email-validator` no es dependencia del
backend y `email` es un campo opcional de conveniencia (identifica la cuenta en la
app autenticadora), no una credencial. Se valida como `str`.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .rbac.roles import Role


# ─── Login / sesión ──────────────────────────────────────────────────────────
class LoginResponse(BaseModel):
    """Respuesta de POST /auth/login.

    Dos formas según el usuario:
    - Sin MFA: trae `access_token` (sesión ya emitida).
    - Con MFA: trae `mfa_required=true` + `mfa_token` (efímero), SIN access_token;
      el front completa con POST /auth/mfa/verify.

    `must_change_password` y `must_enroll_mfa` son banderas de ergonomía para el
    front (ADR-013, decisión abierta #2): un admin sin MFA puede entrar, pero el
    front debe empujarlo a enrolarse. La verificación dura es A6; aquí solo se expone.
    """

    access_token: str | None = None
    token_type: str = "bearer"  # noqa: S105 — nombre de esquema, no un secreto
    mfa_required: bool = False
    mfa_token: str | None = None
    must_change_password: bool = False
    must_enroll_mfa: bool = False


class Token(BaseModel):
    """Respuesta de POST /auth/mfa/verify: la sesión ya emitida tras el 2º factor."""

    access_token: str
    token_type: str = "bearer"  # noqa: S105
    must_change_password: bool = False
    must_enroll_mfa: bool = False


class MFAVerifyRequest(BaseModel):
    mfa_token: str
    code: str


class MeOut(BaseModel):
    """GET /auth/me — identidad del portador del token (sin material sensible)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: str | None = None
    full_name: str | None = None
    role: Role
    is_active: bool
    mfa_enabled: bool
    must_change_password: bool
    # Calculado por el router: admin sin MFA ⇒ el front debe forzar el enrolamiento.
    must_enroll_mfa: bool = False
    last_login_at: datetime | None = None


# ─── Self-service de perfil (/users/me/*) ─────────────────────────────────────
class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class MFASetupResponse(BaseModel):
    """Datos del enrolamiento MFA. `secret` y `qr_code_svg` se muestran UNA vez en
    la pantalla de setup; el secreto queda guardado CIFRADO (Fernet) como tentativo
    hasta que /mfa/enable confirme que el usuario genera códigos válidos."""

    secret: str
    otpauth_uri: str
    qr_code_svg: str


class MFAEnableRequest(BaseModel):
    code: str


class MFAEnableResponse(BaseModel):
    """Recovery codes en claro — se muestran UNA sola vez. En BD solo sus hashes."""

    recovery_codes: list[str]


class MFADisableRequest(BaseModel):
    password: str


# ═══ A4 — settings (fuentes, proyectos, IA) y users-admin ═════════════════════════
# Los DTOs de entrada de endpoints sensibles usan `extra="forbid"`: un cliente que
# envíe un campo `token`/`password` inesperado recibe 422 y el valor jamás se procesa.
# NINGÚN DTO de salida lleva valores de token: solo `token_env` (nombre) y
# `token_present` (bool resuelto server-side).
_STRICT = ConfigDict(extra="forbid")


class SourceIn(BaseModel):
    """PUT /settings/sources/{name}. `token_env` es el NOMBRE de la variable de entorno."""

    model_config = _STRICT

    url: str = Field(min_length=1, max_length=400)
    token_env: str = Field(min_length=1, max_length=120)
    enabled: bool = True


class SourceOut(BaseModel):
    name: str
    url: str
    token_env: str
    enabled: bool
    token_present: bool  # hay un token efectivo (de BD cifrada O del entorno), nunca el valor
    token_source: str = "none"  # noqa: S105 (origen del token: "db"|"env"|"none", no un secreto)
    token_expires_at: datetime | None = None  # expiración conocida del token (no sensible)
    token_expiring_soon: bool = False  # vence en < 7 días
    updated_at: datetime | None = None


class SourceTokenIn(BaseModel):
    """PUT /settings/sources/{name}/token — write-only. El VALOR del token se cifra y se
    guarda; jamás se devuelve, loguea ni audita. `extra="forbid"`: cualquier campo extra
    inesperado → 422 y nada se procesa."""

    model_config = _STRICT

    token: str = Field(min_length=1, max_length=500)


class SourceTokenStatusOut(BaseModel):
    """Respuesta de PUT/DELETE del token: SOLO confirma presencia y origen, nunca el
    valor ni un prefijo."""

    name: str
    token_present: bool
    token_source: str  # "db" | "env" | "none"
    token_expires_at: datetime | None = None
    token_expiring_soon: bool = False


class ProjectIn(BaseModel):
    """Cuerpo de POST/PUT /settings/projects. `key` es obligatorio en POST y, en PUT,
    si viene debe coincidir con la ruta. `sources` = sub-config por fuente, p. ej.
    {"github": {"repo": "o/r", "ci_workflow": "ci.yml"}, "defectdojo": {"product": "x"}}."""

    model_config = _STRICT

    key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
    name: str = Field(min_length=1, max_length=200)
    # Organización dueña: agrupa el repo en el dashboard. None = conserva la actual
    # al editar, o el default ("Wolf") al crear. Multi-org es feature Pro.
    org: str | None = Field(default=None, min_length=1, max_length=100)
    sources: dict[str, dict] = Field(default_factory=dict)
    enabled: bool = True


class ProjectOut(BaseModel):
    key: str
    name: str
    org: str = "Wolf"
    enabled: bool
    sources: dict[str, dict]
    created_at: datetime | None = None


class AISettingsIn(BaseModel):
    model_config = _STRICT

    provider: str = Field(min_length=1, max_length=20)
    model: str = Field(default="", max_length=120)
    base_url: str | None = Field(default=None, max_length=400)
    token_env: str | None = Field(default=None, max_length=120)
    enabled: bool = False


class AISettingsOut(BaseModel):
    provider: str
    model: str
    base_url: str | None = None
    token_env: str | None = None
    token_present: bool = False
    enabled: bool  # toggle guardado en BD
    env_allows_ai: bool  # techo WOLF_AI_ENABLED del entorno
    effective_enabled: bool  # env AND db AND bloque válido (lo que decide el gate 503)


class LDAPConfigIn(BaseModel):
    """Cuerpo de PUT /settings/ldap. NO incluye el bind password (va por su endpoint
    write-only, igual que el token de una fuente). `bind_password_env` es el NOMBRE de
    la env var, nunca el valor."""

    model_config = _STRICT

    enabled: bool = False
    hosts: list[str] = Field(default_factory=list)
    base_dn: str = Field(default="", max_length=400)
    use_ssl: bool = True
    start_tls: bool = False
    tls_verify: str = Field(default="required", pattern=r"^(required|relaxed)$")
    tls_ca_pem: str | None = Field(default=None, max_length=32000)  # CA PEM (cert público)
    user_dn_template: str | None = Field(default=None, max_length=400)
    user_filter: str = Field(default="(uid={username})", max_length=400)
    bind_dn: str | None = Field(default=None, max_length=400)
    bind_password_env: str | None = Field(default=None, max_length=120)
    timeout_seconds: int = Field(default=8, ge=1, le=60)


class LDAPConfigOut(BaseModel):
    enabled: bool
    hosts: list[str]
    base_dn: str
    use_ssl: bool
    start_tls: bool
    tls_verify: str
    tls_ca_pem: str | None = None  # CA PEM (cert público, no secreto)
    user_dn_template: str | None = None
    user_filter: str
    bind_dn: str | None = None
    bind_password_env: str | None = None
    bind_password_present: bool = False  # hay un bind password cifrado en BD (nunca el valor)
    timeout_seconds: int
    sso_available: bool  # la edición actual habilita FEATURE_SSO (si no, 402 al escribir)


class LDAPBindPasswordIn(BaseModel):
    """PUT /settings/ldap/bind-password — write-only. El VALOR se cifra y jamás se devuelve."""

    model_config = _STRICT

    password: str = Field(min_length=1, max_length=400)


class UserCreate(BaseModel):
    model_config = _STRICT

    username: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9._@-]+$")
    # Opcional: una cuenta LDAP (auth_source="ldap") no tiene password local (bind contra
    # el directorio). Para una cuenta local es obligatoria (lo valida el model_validator).
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role: Role = Role.viewer
    email: str | None = Field(default=None, max_length=255)
    full_name: str | None = Field(default=None, max_length=100)
    must_change_password: bool = True
    auth_source: str = Field(default="local", pattern=r"^(local|ldap)$")

    @model_validator(mode="after")
    def _password_required_for_local(self) -> UserCreate:
        if self.auth_source == "local" and not self.password:
            raise ValueError("Una cuenta local requiere password.")
        return self


class UserUpdate(BaseModel):
    model_config = _STRICT

    role: Role | None = None
    is_active: bool | None = None
    full_name: str | None = Field(default=None, max_length=100)


class UserOut(BaseModel):
    """Usuario sin material sensible (nunca hash, secreto MFA ni recovery codes)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: str | None = None
    full_name: str | None = None
    role: Role
    is_active: bool
    auth_source: str = "local"
    mfa_enabled: bool
    must_change_password: bool
    locked_until: datetime | None = None
    last_login_at: datetime | None = None
    created_at: datetime | None = None


class UserListResponse(BaseModel):
    items: list[UserOut]
    total: int


class ResetPasswordResponse(BaseModel):
    """Password temporal: se entrega UNA vez; en BD solo queda su hash."""

    temporary_password: str


# ─── Centro de Update v1 (A5, ADR-016) ───────────────────────────────────────
class CurrentVersionOut(BaseModel):
    current_version: str


class LatestReleaseOut(BaseModel):
    """Última release publicada, o estado degradado (`available=False` + `reason`).

    `notes` es texto NO confiable de la fuente: el frontend debe renderizarlo como
    texto inerte (nunca HTML)."""

    available: bool
    reason: str | None = None  # no_repo | network_error | http_NNN | invalid_response | no_matching_release
    tag: str | None = None
    version: str | None = None
    name: str | None = None
    notes: str | None = None
    url: str | None = None
    published_at: str | None = None


class ServerVersionOut(BaseModel):
    current_version: str
    latest: LatestReleaseOut
    # Tri-estado: True/False cuando ambas versiones parsean; None = desconocido
    # (fuente caída o versión no parseable). Nunca se muestra "al día" con None.
    update_available: bool | None = None


class ReleaseSourceOut(BaseModel):
    repo: str
    configured: bool


class MigrationStatusOut(BaseModel):
    current: str | None = None
    head: str | None = None
    pending: int  # -1 = pendiente pero no contable (historial divergente)
    up_to_date: bool


class UpdateStatusOut(BaseModel):
    server: ServerVersionOut
    release_source: ReleaseSourceOut
    migrations: MigrationStatusOut
    update_command: str  # DISPLAY-ONLY: copiar y correr en el host
    environment: str


class ApplyMigrationsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_backup: bool = False


class ApplyMigrationsOut(BaseModel):
    migrations: MigrationStatusOut
