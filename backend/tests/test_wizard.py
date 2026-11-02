"""F8 — el wizard escribe una config que el loader acepta (round-trip)."""

import pytest

from wolf_devsecops.cli.writer import build_config_dict, write_config
from wolf_devsecops.core.config import load_config


def test_writer_roundtrip(tmp_path):
    sources = {"github": {"url": "https://api.github.com", "token_env": "GITHUB_TOKEN"}}
    projects = [
        {"key": "example-app", "name": "Example App",
         "github": {"repo": "my-org/example-app", "ci_workflow": "ci.yml"}}
    ]
    config = build_config_dict(sources, projects)
    out = tmp_path / "projects.yaml"
    write_config(out, config)

    loaded = load_config(out)
    assert loaded.sources["github"].url == "https://api.github.com"
    assert loaded.sources["github"].token_env == "GITHUB_TOKEN"
    assert loaded.project("example-app").github.repo == "my-org/example-app"


def test_writer_rechaza_token_literal():
    with pytest.raises(ValueError):
        build_config_dict(
            {"github": {"url": "x", "token_env": "T", "token": "ghp_secreto"}},
            [],
        )
