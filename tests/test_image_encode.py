"""Tests for vision image preprocessing (production analyze-photo bugfix).

A large phone-sized photo must be downscaled + recompressed under Anthropic's
~5MB/image vision limit before it reaches the model, or multi-photo analysis
fails with an API error.
"""
from __future__ import annotations

import base64
import io

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

import claude_service  # noqa: E402


def _make_big_jpeg(tmp_path, w=4000, h=3000):
    p = tmp_path / "big.jpg"
    # Noise so JPEG can't trivially compress it to nothing — mimics a real photo.
    import os
    img = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))
    img.save(p, format="JPEG", quality=95)
    return p


def test_encode_image_downscales_and_fits_vision_limit(tmp_path):
    p = _make_big_jpeg(tmp_path)
    assert p.stat().st_size > 4_500_000  # the raw photo is over the API limit

    data_b64, media_type = claude_service._encode_image(str(p))
    assert media_type == "image/jpeg"

    raw = base64.b64decode(data_b64)
    assert len(raw) <= claude_service._VISION_MAX_BYTES  # under Anthropic's limit

    with Image.open(io.BytesIO(raw)) as im:
        assert max(im.size) <= claude_service._VISION_MAX_EDGE  # long edge clamped


def test_encode_image_small_image_passes_through(tmp_path):
    p = tmp_path / "small.png"
    Image.new("RGB", (300, 400), (120, 120, 120)).save(p)
    data_b64, media_type = claude_service._encode_image(str(p))
    assert media_type == "image/jpeg"
    raw = base64.b64decode(data_b64)
    with Image.open(io.BytesIO(raw)) as im:
        assert im.size == (300, 400)
