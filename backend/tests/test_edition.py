"""Edición del producto (Community/Pro/Enterprise) y gate de features."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from wolf_devsecops import edition
from wolf_devsecops.settings import get_settings


def test_normalize_degrada_a_community():
    assert edition.normalize(None) == edition.COMMUNITY
    assert edition.normalize("") == edition.COMMUNITY
    assert edition.normalize("PRO") == edition.PRO
    assert edition.normalize("  Enterprise ") == edition.ENTERPRISE
    assert edition.normalize("galactic") == edition.COMMUNITY  # desconocido → community


def test_community_sin_features_premium_y_una_org():
    assert edition.features_for(edition.COMMUNITY) == frozenset()
    limits = edition.limits_for(edition.COMMUNITY)
    assert limits == {"max_projects": None, "max_orgs": 1}  # proyectos ilimitados, 1 org
    assert not edition.has_feature(edition.COMMUNITY, edition.FEATURE_AI_CODEFIX)


def test_pro_desbloquea_premium_sin_limite_de_org():
    assert edition.has_feature(edition.PRO, edition.FEATURE_MULTI_ORG)
    assert edition.has_feature(edition.PRO, edition.FEATURE_AI_CODEFIX)
    assert edition.has_feature(edition.PRO, edition.FEATURE_NOTIFICATIONS)
    assert edition.limits_for(edition.PRO)["max_orgs"] is None


def test_enterprise_incluye_pro_y_extras():
    assert edition.features_for(edition.PRO) <= edition.features_for(edition.ENTERPRISE)
    assert edition.has_feature(edition.ENTERPRISE, edition.FEATURE_WHITELABEL)
    assert edition.has_feature(edition.ENTERPRISE, edition.FEATURE_MULTI_TENANT)
    assert not edition.has_feature(edition.PRO, edition.FEATURE_WHITELABEL)


def test_history_window_segun_edicion():
    s = get_settings()
    s.edition = edition.COMMUNITY
    assert edition.history_window_days() == edition.COMMUNITY_HISTORY_DAYS
    s.edition = edition.PRO
    assert edition.history_window_days() is None


def test_require_feature_402_en_community_y_ok_en_pro():
    s = get_settings()
    dep = edition.require_feature(edition.FEATURE_AI_CODEFIX)

    s.edition = edition.COMMUNITY
    with pytest.raises(HTTPException) as exc:
        dep()
    assert exc.value.status_code == 402

    s.edition = edition.PRO
    assert dep() is None  # no levanta
