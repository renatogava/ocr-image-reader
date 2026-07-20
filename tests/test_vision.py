from __future__ import annotations

import pytest

from app.ocr_service import OcrError
from app.vision_service import (
    _adjust_confidence_for_illegible,
    _parse_vision_payload,
)


def test_parse_vision_json_with_confidence() -> None:
    text, confidence = _parse_vision_payload(
        '{"text":"Dipirona 500mg","confidence":88.5}'
    )
    assert text == "Dipirona 500mg"
    assert confidence == 88.5


def test_parse_vision_json_penalizes_illegible() -> None:
    text, confidence = _parse_vision_payload(
        '{"text":"A [ilegível] B [ilegível]","confidence":90}'
    )
    assert "[ilegível]" in text
    # 2 hits * 8 = 16 de penalidade
    assert confidence == 74.0


def test_parse_vision_plain_text_fallback() -> None:
    text, confidence = _parse_vision_payload("Receita manuscrita clara")
    assert text == "Receita manuscrita clara"
    assert confidence == 70.0


def test_parse_vision_code_fence() -> None:
    raw = """```json
{"text":"Metformina","confidence":80}
```"""
    text, confidence = _parse_vision_payload(raw)
    assert text == "Metformina"
    assert confidence == 80.0


def test_parse_vision_clamps_confidence() -> None:
    _, confidence = _parse_vision_payload('{"text":"ok","confidence":150}')
    assert confidence == 100.0


def test_parse_vision_empty_text_raises() -> None:
    with pytest.raises(OcrError) as exc:
        _parse_vision_payload('{"text":"  ","confidence":50}')
    assert exc.value.status_code == 422


def test_adjust_confidence_cap() -> None:
    text = " ".join(["[ilegível]"] * 10)
    assert _adjust_confidence_for_illegible(text, 100.0) == 60.0
