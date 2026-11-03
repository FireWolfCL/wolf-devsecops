"""Configuración de la APLICACIÓN (no de las fuentes de datos).

Las fuentes y proyectos viven en config/projects.yaml (ver core/config.py).
Aquí solo lo que afecta al propio servidor, leído del entorno con prefijo WOLF_.
"""

from __future__ import annotations

import base64
import secrets
import warnings

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WOLF_", env_file=".env", extra="ignore")

    # Entorno de despliegue. En "production" el arranque es fail-closed:
    # exige secretos fuertes (JWT/Fernet) y gatea el backup de migraciones.
    environment: str = "development"

    # Edición del producto: "community" (default) | "pro" | "enterprise". Fase 1
    # (edition-flag): se fija por entorno. Fase 2: la determinará una licencia
    # firmada por la WLA. Un valor inválido degrada a community (ver edition.py).
    edition: str = "community"

    # Licencia firmada (fase 2). WOLF_LICENSE = token JWS o "@/ruta/al/archivo".
    # Si está presente y es válida, DETERMINA la edición. `license_required`=true
    # ignora el flag WOLF_EDITION (solo una licencia concede Pro; para producción).
    license: str = ""
    license_required: bool = False

    # Ruta al archivo de proyectos/fuentes (semilla de bootstrap YAML→BD, ADR-015).
    config_path: str = "config/projects.yaml"

    # Persistencia (ADR-012): SQLite por defecto (cero-ops para quien autohospeda).
    # Postgres opcional: WOLF_DATABASE_URL=postgresql+asyncpg://user:pass@host/db
    # La credencial vive SOLO aquí (entorno), nunca en el repo ni en la BD.
    database_url: str = "sqlite+aiosqlite:///./wolf_devsecops.db"

    # Caché: cuántos segundos se considera fresco un dato de una fuente.
    cache_ttl_seconds: int = 300

    # Mapa de módulos (codemap): límites para repos grandes.
    codemap_max_nodes: int = 2000
    codemap_max_depth: int = 6

    # Timeout por llamada HTTP a una fuente externa.
    http_timeout: float = 10.0

    # Pool de conexiones (solo motores no-SQLite). Regla: pool_size+overflow >=
    # (workers × concurrencia de escritura esperada) y < max_connections de Postgres
    # / nº de workers de uvicorn.
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout: float = 10.0

    # Agregador (PERFORMANCE-ADMIN.md): tope duro por fuente (cubre fuentes con
    # varias llamadas encadenadas, p. ej. Sonar) y tope global de llamadas
    # salientes simultáneas (protege a las fuentes y su rate-limit).
    source_timeout_seconds: float = 15.0
    max_upstream_concurrency: int = 32

    # Orígenes permitidos para CORS (el frontend). Coma-separados.
    cors_origins: str = "http://localhost:5173"

    # Reviewer IA (F6): DESHABILITADO por defecto hasta el visto bueno de
    # wolf-ai-safety-agent (ADR-007). Activar conscientemente con WOLF_AI_ENABLED=true.
    ai_enabled: bool = False

    # Timeout por llamada al proveedor de IA. Generoso por defecto porque un modelo
    # LOCAL en CPU (p. ej. Ollama sin GPU) puede tardar con prompts grandes; con un
    # endpoint hosted/GPU sobra. Configurable con WOLF_AI_TIMEOUT_SECONDS.
    ai_timeout_seconds: float = 120.0

    # Máximo de hallazgos que se envían al modelo por review. Los hallazgos llegan
    # ordenados por severidad (crítico primero), así que el tope conserva lo más
    # grave y acota la latencia (clave para un modelo local en CPU). WOLF_AI_MAX_FINDINGS.
    ai_max_findings: int = 40

    # --- Feature B: propuesta de fix con código (ADR-018, nivel assistive) ------
    # Ventana de código que se lee alrededor del `line` de un finding, vía la
    # GitHub Contents API (read-only, SIN clonar). Se leen ±N líneas de UN archivo.
    # Acota el egreso: nunca se manda el archivo completo ni el repo. WOLF_CODE_WINDOW_LINES.
    code_window_lines: int = 50
    # Cota de longitud de la salida del modelo (proposed_snippet + explanation).
    code_fix_max_output_chars: int = 8000
    # Allow-list de modelos para el proveedor HOSTED (egreso a un tercero). Un
    # `model` fuera de esta lista se rechaza con 422. Coma-separada. WOLF_AI_HOSTED_MODEL_ALLOWLIST.
    ai_hosted_model_allowlist: str = "claude-sonnet-4-5,claude-opus-4-8,gpt-4o"
    # Allow-list ADICIONAL de modelos LOCALES (además del modelo del bloque `ai`
    # configurado). Normalmente vacío: el default local = lo que diga ai_settings.
    ai_local_model_allowlist: str = ""
    # Endpoint/token del proveedor HOSTED cuando el bloque `ai` configurado NO es
    # hosted (selección por-petición, ADR-018). `ai_hosted_token_env` es el NOMBRE
    # de la variable de entorno con la API key (ADR-002: el valor vive SOLO en el
    # entorno, nunca en la BD ni en el repo). El cliente NUNCA manda base_url/token.
    ai_hosted_base_url: str = ""
    ai_hosted_token_env: str = ""

    # --- Feature D: remediación branch + PR draft (ADR-017, decision-assist) ----
    # Techo fail-closed (como WOLF_AI_ENABLED): si está en False, el endpoint de
    # remediación responde 503 aunque el rol y el `confirm` sean correctos. Default
    # False: escribir en el repo del proyecto auditado es una decisión consciente.
    remediation_write_enabled: bool = False
    # NOMBRE de la variable de entorno con el PAT de ESCRITURA (scope mínimo:
    # contents:write, pull_requests:write). SEPARADO del token de lectura de la
    # fuente github (ADR-002, ADR-017). El valor vive SOLO en el entorno; jamás se
    # devuelve, loguea ni audita. WOLF_GITHUB_WRITE_TOKEN_ENV.
    github_write_token_env: str = "GITHUB_WRITE_TOKEN"  # noqa: S105 (nombre de env var, no secreto)

    # ── Notificaciones (item M). Canales opcionales: si el token/host está vacío,
    #    ese canal se omite. El scheduler corre snapshot + dispatch cada intervalo.
    snapshot_interval_seconds: int = 3600  # WOLF_SNAPSHOT_INTERVAL_SECONDS
    notify_lang: str = "es"  # idioma de los mensajes salientes (es|en)
    notify_min_severity: str = "good"  # info|good|warn|danger — umbral mínimo (good = incluye mejoras)
    # Telegram (push + bot de estatus). Token de @BotFather, chat/grupo destino.
    telegram_bot_token: str = ""  # WOLF_TELEGRAM_BOT_TOKEN
    telegram_chat_id: str = ""  # WOLF_TELEGRAM_CHAT_ID
    # Email (SMTP). Si smtp_host está vacío, no se envía correo.
    smtp_host: str = ""  # WOLF_SMTP_HOST
    smtp_port: int = 587  # WOLF_SMTP_PORT
    smtp_user: str = ""  # WOLF_SMTP_USER
    smtp_password: str = ""  # WOLF_SMTP_PASSWORD
    smtp_from: str = ""  # WOLF_SMTP_FROM (remitente; por defecto = smtp_user)
    smtp_starttls: bool = True  # WOLF_SMTP_STARTTLS
    notify_email_to: str = ""  # WOLF_NOTIFY_EMAIL_TO (coma-separado)

    # --- Primitivas de auth (A1, ADR-013) ----------------------------------
    # Secretos criptográficos: viven SOLO en el entorno (WOLF_JWT_SECRET,
    # WOLF_MFA_FERNET_KEY), nunca en el repo ni en la BD. En production el
    # arranque ABORTA si faltan o son un default obvio (ver
    # `provision_security_keys`); en development se derivan claves EFÍMERAS con
    # aviso (no persisten entre reinicios: los tokens y secretos MFA cifrados
    # con una clave efímera dejan de ser válidos al reiniciar — aceptable en dev).
    #
    # Cómo generar valores fuertes para production:
    #   WOLF_JWT_SECRET      → python -c "import secrets; print(secrets.token_urlsafe(48))"
    #   WOLF_MFA_FERNET_KEY  → python -c "from cryptography.fernet import Fernet; \
    #                                     print(Fernet.generate_key().decode())"
    #
    # SEPARACIÓN DE CLAVES (principio no negociable): la clave que firma los JWT
    # de sesión (`jwt_secret`) y la que cifra el secreto MFA en reposo
    # (`mfa_fernet_key`) son DISTINTAS y de propósito único. Nunca se reutiliza
    # una para lo otro ni para cifrar datos de otra frontera.
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480  # 8 h — sesión de panel admin
    mfa_token_expire_minutes: int = 5  # token efímero que solo cruza /auth/mfa/verify
    mfa_fernet_key: str = ""
    # Emisor que aparece en el otpauth:// URI (app autenticadora del usuario).
    mfa_issuer: str = "Wolf DevSecOps"

    # --- Lockout de login (A2, ADR-013) ------------------------------------
    # Tras `login_max_attempts` fallos consecutivos la cuenta queda bloqueada
    # `login_lockout_minutes` (gate → HTTP 423). El contador se resetea en un
    # login completamente exitoso. Es defensa anti fuerza-bruta, no anti-DoS: el
    # rate-limit del endpoint lo pone el reverse proxy / A3.
    login_max_attempts: int = 5
    login_lockout_minutes: int = 15

    # --- Centro de Update v1 (A5, ADR-016) ---------------------------------
    # Fuente de releases: GitHub Releases de `owner/name`. Vacío => degradación
    # `no_repo` (el panel sigue vivo, sin "última versión"). Los tags de release
    # llevan prefijo `v` (vX.Y.Z); drafts/prereleases se ignoran.
    update_release_repo: str = ""
    # Token de lectura SOLO para repos privados. Vive en el entorno
    # (WOLF_UPDATE_RELEASE_TOKEN); jamás se devuelve, se loguea ni se audita.
    update_release_token: str = ""
    update_release_timeout_seconds: float = 6.0
    # DISPLAY-ONLY: comando de redeploy que el operador copia y corre en el host.
    # La app NUNCA lo ejecuta (ADR-016: sin self-update en v1).
    update_command: str = "git fetch --tags && git pull && docker compose up -d --build"

    # --- Enlaces "Abrir en <Herramienta>" (deep-links de salida del panel) --
    # URL PÚBLICA de cada herramienta: la que alcanza el NAVEGADOR del operador.
    # Es DISTINTA de la URL interna que el backend usa para la API (p. ej.
    # http://sonarqube:9000 dentro de Docker): esa no resuelve en el navegador.
    # Solo se usa para CONSTRUIR enlaces de salida, nunca para llamar a la API.
    # Default = puertos publicados del docker-compose de instalación. Dejar ""
    # desactiva el enlace de esa fuente (el panel no muestra el botón).
    tool_web_sonarqube: str = "http://localhost:9000"
    tool_web_defectdojo: str = "http://localhost:8086"
    tool_web_harbor: str = "http://localhost:8085"

    def tool_web_url(self, tool: str) -> str | None:
        """Base pública navegable de una herramienta, o None si no hay enlace."""
        raw = {
            "sonarqube": self.tool_web_sonarqube,
            "defectdojo": self.tool_web_defectdojo,
            "harbor": self.tool_web_harbor,
        }.get(tool, "")
        raw = (raw or "").strip().rstrip("/")
        # Solo http(s): nunca dejamos pasar un esquema raro a un href.
        return raw if raw.startswith(("http://", "https://")) else None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def ai_hosted_model_allowlist_set(self) -> frozenset[str]:
        return frozenset(m.strip() for m in self.ai_hosted_model_allowlist.split(",") if m.strip())

    @property
    def ai_local_model_allowlist_set(self) -> frozenset[str]:
        return frozenset(m.strip() for m in self.ai_local_model_allowlist.split(",") if m.strip())

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"


# Valores que NUNCA deben aceptarse como secreto real (defaults heredados de WIF,
# placeholders típicos). La comparación se hace sobre el valor en minúsculas.
_INSECURE_SECRETS = frozenset(
    {
        "",
        "changeme",
        "change-me",
        "changeme-jwt-secret",
        "changeme-mfa-key",
        "changeme-fernet-key",
        "secret",
        "default",
        "test",
        "dev",
        "password",
    }
)


def _is_insecure(value: str, *, min_length: int = 32) -> bool:
    """True si `value` está vacío, es un placeholder conocido o es demasiado corto
    para ser un secreto de alta entropía."""
    if value is None:
        return True
    v = value.strip()
    if v.lower() in _INSECURE_SECRETS:
        return True
    return len(v) < min_length


def _ephemeral_fernet_key() -> str:
    """Clave Fernet válida (32 bytes urlsafe-b64), equivalente a
    `cryptography.fernet.Fernet.generate_key()` pero sin importar cryptography aquí."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def provision_security_keys(settings: Settings) -> Settings:
    """Chequeo FAIL-CLOSED de los secretos de auth, aplicado sobre `settings`.

    - En `environment == "production"`: ABORTA (RuntimeError) si `jwt_secret` o
      `mfa_fernet_key` están vacíos, son un placeholder conocido o son demasiado
      cortos. Un secreto débil en producción es una brecha, no un warning.
    - En development/test: deriva claves EFÍMERAS de alta entropía y avisa por
      `warnings`. Permite arrancar y correr tests sin configurar secretos, pero
      deja claro que no sirven para producción.

    Idempotente: una vez provistas claves válidas, no vuelve a tocarlas. Muta y
    devuelve el mismo objeto `settings` (singleton).
    """
    prod = settings.is_production

    if _is_insecure(settings.jwt_secret):
        if prod:
            raise RuntimeError(
                "WOLF_JWT_SECRET ausente, inseguro o demasiado corto en environment="
                "'production'. Genera uno con: "
                "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        settings.jwt_secret = secrets.token_urlsafe(48)
        warnings.warn(
            "WOLF_JWT_SECRET no configurado: se generó una clave JWT EFÍMERA (solo "
            "dev/test). Los tokens dejarán de ser válidos al reiniciar el proceso. "
            "Configura WOLF_JWT_SECRET para producción.",
            stacklevel=2,
        )

    if _is_insecure(settings.mfa_fernet_key):
        if prod:
            raise RuntimeError(
                "WOLF_MFA_FERNET_KEY ausente, inseguro o demasiado corto en environment="
                "'production'. Genera una con: python -c "
                "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        settings.mfa_fernet_key = _ephemeral_fernet_key()
        warnings.warn(
            "WOLF_MFA_FERNET_KEY no configurada: se generó una clave Fernet EFÍMERA "
            "(solo dev/test). Los secretos MFA cifrados con ella dejarán de ser "
            "descifrables al reiniciar. Configura WOLF_MFA_FERNET_KEY para producción.",
            stacklevel=2,
        )

    return settings


_settings: Settings | None = None


def get_settings() -> Settings:
    """Settings como singleton perezoso (se puede sobreescribir en tests).

    Aplica el chequeo fail-closed de secretos de auth (`provision_security_keys`)
    una sola vez, al construir el singleton: en production el proceso ABORTA aquí
    si faltan secretos fuertes; en dev/test se derivan claves efímeras con aviso.
    """
    global _settings
    if _settings is None:
        _settings = provision_security_keys(Settings())
    return _settings
