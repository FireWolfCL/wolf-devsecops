"""Lectura ACOTADA de código para la propuesta de fix (Feature B, ADR-018).

Pieza central de la frontera de egreso del flujo "código → modelo". Dado el
`file_path`+`line` de un finding, trae una ventana de ±N líneas de UN solo archivo
vía la **GitHub Contents API (read-only)**. NUNCA clona el repo, nunca lista el
árbol, nunca sale de la raíz del repo configurado.

Controles de seguridad (wolf-security-crypto-agent):
- Validación anti path-traversal DURA (`validate_repo_path`): normaliza y rechaza
  `..`, rutas absolutas, separadores de Windows, bytes nulos y todo lo que escape
  de la raíz del repo. La ruta viene del finding agregado, pero se valida igual.
- La Contents API devuelve el `type` del objeto: se exige `type == "file"`. Un
  symlink (`type == "symlink"`) o un submódulo (`type == "submodule"`) se rechaza:
  así nadie usa un enlace simbólico para leer fuera del repo.
- Pre-scan + redacción de secretos de la ventana (`scan_and_redact_secrets`) antes
  de cualquier egreso hosted (best-effort; ver TODO).
"""

from __future__ import annotations

import base64
import posixpath
import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx


class CodeContextError(Exception):
    """Error al resolver/leer la ventana de código (mensaje saneado al usuario)."""


# Rechaza de plano cualquier ruta con estos rasgos ANTES de normalizar.
_NULL = "\x00"
_WIN_DRIVE = re.compile(r"^[A-Za-z]:")


def validate_repo_path(raw_path: str) -> str:
    """Valida y normaliza una ruta RELATIVA al repo. Devuelve la ruta posix limpia
    o lanza `CodeContextError`. Rechazo DURO (fail-closed): ante cualquier duda, no
    se lee nada.

    Rechaza: vacío, byte nulo, backslashes (separador Windows), ruta absoluta,
    letra de unidad (`C:`), y cualquier intento de escapar la raíz (`..`), incluso
    si queda escondido tras normalizar (`a/../../etc`).
    """
    if raw_path is None:
        raise CodeContextError("Ruta de archivo ausente en el finding.")
    path = str(raw_path).strip()
    if not path:
        raise CodeContextError("Ruta de archivo vacía.")
    if _NULL in path:
        raise CodeContextError("Ruta de archivo inválida (byte nulo).")
    if "\\" in path:
        raise CodeContextError("Ruta de archivo inválida (separador de Windows).")
    if path.startswith("/") or _WIN_DRIVE.match(path):
        raise CodeContextError("Ruta de archivo absoluta no permitida.")
    # Normaliza en espacio posix y verifica que no se escape de la raíz.
    normalized = posixpath.normpath(path)
    if normalized == ".." or normalized.startswith("../") or normalized.startswith("/"):
        raise CodeContextError("La ruta escapa de la raíz del repositorio.")
    # Defensa en profundidad: ningún segmento puede ser '..' (ni antes ni después).
    if any(seg == ".." for seg in normalized.split("/")):
        raise CodeContextError("La ruta contiene un segmento de traversal ('..').")
    if normalized in ("", "."):
        raise CodeContextError("Ruta de archivo inválida.")
    return normalized


@dataclass
class CodeWindow:
    path: str
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    text: str


def _slice_window(content: str, line: int | None, window_lines: int) -> CodeWindow:
    lines = content.splitlines()
    n = len(lines)
    if n == 0:
        return CodeWindow(path="", start_line=0, end_line=0, text="")
    if line is None or line < 1:
        # Sin línea conocida: ventana desde el inicio (2·N+1 líneas), degradación clara.
        start = 1
        end = min(n, 2 * window_lines + 1)
    else:
        center = min(line, n)
        start = max(1, center - window_lines)
        end = min(n, center + window_lines)
    text = "\n".join(lines[start - 1 : end])
    return CodeWindow(path="", start_line=start, end_line=end, text=text)


async def fetch_code_window(
    *,
    api_base: str,
    repo: str,
    file_path: str,
    line: int | None,
    token: str | None,
    window_lines: int,
    ref: str | None = None,
    transport: httpx.BaseTransport | None = None,
) -> CodeWindow:
    """Lee ±`window_lines` alrededor de `line` de UN archivo vía Contents API.

    `token` es el token de LECTURA de la fuente github (ADR-002); se usa solo en la
    cabecera Authorization, nunca se devuelve ni se registra. `transport` inyectable
    para tests (MockTransport), None en producción (red real).
    """
    clean_path = validate_repo_path(file_path)
    base = api_base.rstrip("/")
    # El path ya está validado; se encodea cada segmento (safe="") para que ningún
    # carácter rompa la URL ni introduzca query/fragment. No hay '..' (ya rechazado).
    encoded = "/".join(quote(seg, safe="") for seg in clean_path.split("/"))
    url = f"{base}/repos/{repo}/contents/{encoded}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params = {"ref": ref} if ref else None
    try:
        async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
            resp = await client.get(url, headers=headers, params=params)
    except httpx.HTTPError as exc:
        raise CodeContextError("No se pudo contactar la API de GitHub.") from exc
    if resp.status_code == 404:
        raise CodeContextError("El archivo del finding no existe en el repositorio.")
    if resp.status_code != 200:
        raise CodeContextError(f"GitHub respondió HTTP {resp.status_code}.")
    try:
        data = resp.json()
    except ValueError as exc:
        raise CodeContextError("Respuesta de GitHub no es JSON válido.") from exc
    # Un directorio devuelve una lista; exigimos un objeto de tipo file.
    if isinstance(data, list):
        raise CodeContextError("La ruta apunta a un directorio, no a un archivo.")
    obj_type = data.get("type")
    if obj_type != "file":
        # symlink / submodule: se rechaza para no leer fuera del repo.
        raise CodeContextError(f"La ruta no es un archivo regular (type={obj_type!r}).")
    if data.get("encoding") != "base64" or not data.get("content"):
        # Archivos >1 MB: la Contents API no incrusta el contenido. No usamos la Blob
        # API: el objetivo es una ventana ACOTADA, no traer archivos enormes.
        raise CodeContextError("El archivo es demasiado grande para leer una ventana acotada.")
    try:
        decoded = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    except (ValueError, TypeError) as exc:
        raise CodeContextError("No se pudo decodificar el contenido del archivo.") from exc
    window = _slice_window(decoded, line, window_lines)
    window.path = clean_path
    return window


# ─── Pre-scan de secretos de la ventana (best-effort, ADR-018) ───────────────
# TODO(wolf-security-crypto-agent): esto es un pre-scan DEFENSIVO, no un escáner de
# secretos completo (gitleaks/trufflehog). Cubre los patrones de alto valor y
# alta confianza. Antes de GA conviene delegar al escáner de secretos real del
# pipeline (gitleaks) o a un servicio dedicado, y tratar este regex como red de
# seguridad, no como control único.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS Access Key ID
    re.compile(r"ghp_[A-Za-z0-9]{36}"),  # GitHub personal access token
    re.compile(r"github_pat_[A-Za-z0-9_]{60,}"),  # GitHub fine-grained PAT
    re.compile(r"gh[ouser]_[A-Za-z0-9]{36}"),  # otros tokens github (gho_/ghu_/ghs_/ghr_)
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),  # Slack
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI-style
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # JWT
    # Asignaciones tipo secret/password/token/api_key = "valor-largo".
    re.compile(
        r"""(?i)(?:password|passwd|secret|api[_-]?key|token|access[_-]?key)\s*[:=]\s*['"]?[^\s'"]{8,}"""
    ),
)
_REDACTION = "***REDACTED-SECRET***"


def scan_and_redact_secrets(text: str) -> tuple[str, int]:
    """Redacta posibles secretos de `text`. Devuelve (texto_redactado, nº_matches).

    Se aplica a lo que se envía a un proveedor HOSTED (egreso a un tercero): aunque
    el egreso de código es una decisión consciente del operador (ADR-018), no se le
    entrega una credencial a un tercero si se puede evitar.
    """
    redacted = text
    count = 0
    for pat in _SECRET_PATTERNS:
        redacted, n = pat.subn(_REDACTION, redacted)
        count += n
    return redacted, count
