"""Image sent to every model tier (ADR-001 §4, amended 2026-11-12).

The Jev capture returned ``400 Invalid image dimensions``: Jev accepts 256–1568 px per side and
78 400–2 458 624 px in total (OpenRouter Multimodal Decisions guide). The crops range from 49 px
to 2048 px, so each crop is scaled once, keeping its aspect ratio, and re-encoded as PNG. The same
bytes go to tier 1 and tier 2 (AC-12); the cache key uses their SHA-256. Input files are only read.
"""

from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path

from PIL import Image

MIN_SIDE = 512  # measured 2026-11-12: 274x287 px -> 400, 512x512 px -> 200 (published minimum is 256)
MAX_SIDE = 1568
MIN_PIXELS = 78_400
MIME_TYPE = "image/png"


def _target_size(width: int, height: int) -> tuple[int, int]:
    scale = max(1.0, MIN_SIDE / min(width, height), math.sqrt(MIN_PIXELS / (width * height)))
    if max(width, height) * scale > MAX_SIDE:
        scale = MAX_SIDE / max(width, height)
    if scale == 1.0:
        return width, height
    w, h = width * scale, height * scale
    rounding = math.ceil if scale > 1 else math.floor
    return max(1, rounding(w - 1e-9)), max(1, rounding(h - 1e-9))


def model_image(crop_path: str | Path) -> tuple[bytes, str, str]:
    """Return ``(png_bytes, mime_type, sha256)`` of the normalized crop."""
    with Image.open(crop_path) as source:
        image = source.convert("RGB")
    size = _target_size(*image.size)
    if size != image.size:
        resample = Image.Resampling.BICUBIC if size[0] > image.size[0] else Image.Resampling.LANCZOS
        image = image.resize(size, resample)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False, compress_level=6)
    data = buffer.getvalue()
    return data, MIME_TYPE, hashlib.sha256(data).hexdigest()
