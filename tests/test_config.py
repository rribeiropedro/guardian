"""Tests for backend/config.py — settings loading and caching."""
from __future__ import annotations

import pytest


def test_default_settings_all_empty():
    from backend.config import get_settings
    s = get_settings()
    assert s.anthropic_api_key == ""
    assert s.google_maps_api_key == ""
    assert s.mapbox_token == ""
    assert s.demo_mode is False
    assert s.fallback_to_haiku is True


@pytest.mark.parametrize("value", ["1", "true", "yes", "True", "YES"])
def test_demo_mode_true_variants(monkeypatch, value):
    from backend.config import get_settings
    monkeypatch.setenv("DEMO_MODE", value)
    assert get_settings().demo_mode is True


def test_demo_mode_false_when_not_set(monkeypatch):
    from backend.config import get_settings
    monkeypatch.delenv("DEMO_MODE", raising=False)
    assert get_settings().demo_mode is False


def test_fallback_to_haiku_default_true(monkeypatch):
    from backend.config import get_settings
    monkeypatch.delenv("FALLBACK_TO_HAIKU", raising=False)
    assert get_settings().fallback_to_haiku is True


def test_fallback_to_haiku_false(monkeypatch):
    from backend.config import get_settings
    monkeypatch.setenv("FALLBACK_TO_HAIKU", "false")
    assert get_settings().fallback_to_haiku is False


def test_api_keys_loaded_from_env(monkeypatch):
    from backend.config import get_settings
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "gm-test")
    monkeypatch.setenv("MAPBOX_TOKEN", "mb-test")
    s = get_settings()
    assert s.anthropic_api_key == "sk-test"
    assert s.google_maps_api_key == "gm-test"
    assert s.mapbox_token == "mb-test"


def test_get_settings_is_cached():
    from backend.config import get_settings
    assert get_settings() is get_settings()
