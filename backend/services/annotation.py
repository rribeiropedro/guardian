"""Image annotation service — MVP stub. Person C will replace with Pillow bounding-box logic."""
from __future__ import annotations

from ..models.schemas import Finding


async def annotate_image(image_bytes: bytes, findings: list[Finding]) -> bytes:
    """Return the original image unchanged. Person C implements Pillow bbox drawing."""
    return image_bytes
