"""Model image normalization (ADR-001 §4, amended 2026-11-12 after the Jev capture: 400 Invalid image dimensions)."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image

from conftest import DATA_DIR
from map_qa.image import MAX_SIDE, MIN_PIXELS, MIN_SIDE, model_image


def _jpeg(tmp_path: Path, size: tuple[int, int], name: str = "x.jpg") -> Path:
    path = tmp_path / name
    Image.new("RGB", size, (200, 30, 30)).save(path, "JPEG", quality=90)
    return path


def _size(data: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(data)).size


@pytest.mark.parametrize("size", [(49, 56), (54, 50), (135, 141), (256, 256), (300, 200), (2048, 1536), (1600, 1600), (1568, 512)])
def test_output_fits_jev_limits(tmp_path, size):
    data, mime, sha = model_image(_jpeg(tmp_path, size))
    w, h = _size(data)
    assert MIN_SIDE <= w <= MAX_SIDE and MIN_SIDE <= h <= MAX_SIDE
    assert w * h >= MIN_PIXELS
    assert mime == "image/png"
    assert sha == hashlib.sha256(data).hexdigest()
    assert abs(w / h - size[0] / size[1]) < 0.02  # aspect ratio kept


def test_image_within_limits_keeps_its_size(tmp_path):
    data, _, _ = model_image(_jpeg(tmp_path, (600, 520)))
    assert _size(data) == (600, 520)


def test_output_is_deterministic(tmp_path):
    path = _jpeg(tmp_path, (61, 61))
    assert model_image(path) == model_image(path)


def test_every_real_crop_fits():
    for crop in sorted((DATA_DIR / "signs" / "crops").glob("*.jpg"))[:15]:
        w, h = _size(model_image(crop)[0])
        assert MIN_SIDE <= min(w, h) and max(w, h) <= MAX_SIDE and w * h >= MIN_PIXELS
