"""Tests for backend/services/annotation.py — stub passthrough contract."""
from __future__ import annotations

import inspect

import pytest


@pytest.mark.asyncio
async def test_annotate_image_returns_same_bytes(fake_image_bytes):
    from backend.services.annotation import annotate_image
    result = await annotate_image(fake_image_bytes, [])
    assert result == fake_image_bytes


@pytest.mark.asyncio
async def test_annotate_image_with_findings_returns_bytes(fake_image_bytes):
    from backend.models.schemas import Finding
    from backend.services.annotation import annotate_image
    findings = [
        Finding(category="structural", description="crack", severity="CRITICAL"),
        Finding(category="overhead", description="power line", severity="MODERATE"),
    ]
    result = await annotate_image(fake_image_bytes, findings)
    assert isinstance(result, bytes)


def test_annotate_image_signature_contract():
    """Person C must preserve this signature when replacing the stub."""
    from backend.services.annotation import annotate_image
    sig = inspect.signature(annotate_image)
    params = list(sig.parameters.keys())
    assert params[0] == "image_bytes"
    assert params[1] == "findings"
