"""Escritura de config/projects.yaml y deploy/.env (ADR-010, F8).

El YAML NUNCA lleva tokens: solo el NOMBRE de la variable de entorno (token_env).
`build_config_dict` es pura (testeable); `write_config` persiste.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_SECRET_HINT = {"token", "api_token", "password", "secret"}


def build_config_dict(
    sources: dict[str, dict],
    projects: list[dict],
    ai: dict | None = None,
) -> dict:
    """Arma el dict de projects.yaml. Rechaza cualquier valor de secreto literal."""
    out: dict = {"sources": {}, "projects": projects}
    for name, s in sources.items():
        if _SECRET_HINT & set(s):
            raise ValueError(f"La fuente '{name}' no puede llevar un token literal, solo token_env.")
        out["sources"][name] = {"base_url": s["url"], "token_env": s["token_env"]}
    if ai:
        out["ai"] = ai
    return out


def write_config(path: str | Path, config: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8")


def write_env_example(path: str | Path, token_envs: list[str]) -> None:
    """Deja un .env de ejemplo con las variables que el operador debe rellenar."""
    lines = ["# Tokens de las fuentes — rellena y NO comitees este archivo.", ""]
    lines += [f"{name}=" for name in token_envs]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
