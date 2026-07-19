from __future__ import annotations

import io
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image, ImageDraw
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.ocr_service import OcrResult
from app.preprocess import preprocess_image


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def api_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = "test-api-key"
    monkeypatch.setenv("OCR_API_KEY", key)
    monkeypatch.setenv("OCR_ENGINE", "tesseract")
    get_settings.cache_clear()
    return key


@pytest.fixture
def client(api_key: str) -> TestClient:
    return TestClient(app)


def _make_png_bytes(text: str = "Dipirona 500mg") -> bytes:
    image = Image.new("RGB", (400, 120), color="white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 40), text, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_health(client: TestClient) -> None:
    with patch("app.main.tesseract_version", return_value="5.3.0"):
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["tesseract"] == "5.3.0"
    assert body["openai_configured"] is False


def test_preprocess_returns_binary_image() -> None:
    image = Image.new("RGB", (200, 80), color="white")
    draw = ImageDraw.Draw(image)
    draw.text((10, 30), "ABC", fill="black")
    processed = preprocess_image(image)
    assert processed.mode in {"L", "1"}
    assert processed.size[0] >= 200
    assert processed.size[1] >= 80


def test_ocr_unauthorized(client: TestClient) -> None:
    response = client.post(
        "/ocr",
        files={"file": ("rx.png", _make_png_bytes(), "image/png")},
    )
    assert response.status_code == 401


def test_ocr_missing_body(client: TestClient, api_key: str) -> None:
    response = client.post(
        "/ocr",
        headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
        content=b"{}",
    )
    assert response.status_code == 422


def test_ocr_multipart_success(client: TestClient, api_key: str) -> None:
    fake_data = {
        "conf": ["90", "85", "-1"],
        "text": ["Dipirona", "500mg", ""],
    }
    with (
        patch("app.ocr_service.configure_tesseract"),
        patch("app.ocr_service.pytesseract.image_to_string", return_value="Dipirona 500mg\n"),
        patch("app.ocr_service.pytesseract.image_to_data", return_value=fake_data),
    ):
        response = client.post(
            "/ocr",
            headers={"X-Api-Key": api_key},
            files={"file": ("rx.png", _make_png_bytes(), "image/png")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "Dipirona" in body["text"]
    assert body["language"] == "por"
    assert body["confidence"] == 87.5
    assert body["engine"] == "tesseract"


def test_ocr_json_url_success(client: TestClient, api_key: str) -> None:
    png = _make_png_bytes()
    fake_data = {"conf": ["95"], "text": ["Receita"]}

    async def fake_download(url: str, settings=None) -> bytes:
        return png

    with (
        patch("app.main.download_image", side_effect=fake_download),
        patch("app.ocr_service.configure_tesseract"),
        patch("app.ocr_service.pytesseract.image_to_string", return_value="Receita medica"),
        patch("app.ocr_service.pytesseract.image_to_data", return_value=fake_data),
    ):
        response = client.post(
            "/ocr",
            headers={"X-Api-Key": api_key},
            json={"imageUrl": "https://cdn.example.com/receita.png"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["text"] == "Receita medica"
    assert body["confidence"] == 95.0
    assert body["engine"] == "tesseract"


def test_ocr_empty_text_returns_422(client: TestClient, api_key: str) -> None:
    with (
        patch("app.ocr_service.configure_tesseract"),
        patch("app.ocr_service.pytesseract.image_to_string", return_value="   \n"),
        patch(
            "app.ocr_service.pytesseract.image_to_data",
            return_value={"conf": [], "text": []},
        ),
    ):
        response = client.post(
            "/ocr",
            headers={"X-Api-Key": api_key},
            files={"file": ("rx.png", _make_png_bytes(), "image/png")},
        )

    assert response.status_code == 422


def test_ocr_auto_falls_back_to_vision_on_low_confidence(
    client: TestClient,
    api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OCR_ENGINE", "auto")
    monkeypatch.setenv("OCR_CONFIDENCE_THRESHOLD", "60")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OCR_STRUCTURE_ENABLED", "false")
    get_settings.cache_clear()

    fake_data = {"conf": ["30", "25"], "text": ["xx", "yy"]}
    vision_result = OcrResult(
        text="Dipirona 500mg manuscrito",
        language="por",
        confidence=None,
        engine="openai",
    )

    with (
        patch("app.ocr_service.configure_tesseract"),
        patch("app.ocr_service.pytesseract.image_to_string", return_value="xx yy"),
        patch("app.ocr_service.pytesseract.image_to_data", return_value=fake_data),
        patch(
            "app.vision_service.extract_text_with_vision",
            new=AsyncMock(return_value=vision_result),
        ),
    ):
        response = client.post(
            "/ocr",
            headers={"X-Api-Key": api_key},
            files={"file": ("rx.png", _make_png_bytes(), "image/png")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["engine"] == "openai"
    assert "manuscrito" in body["text"]


def test_ocr_openai_engine_uses_vision(
    client: TestClient,
    api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OCR_STRUCTURE_ENABLED", "false")
    get_settings.cache_clear()

    vision_result = OcrResult(
        text="Receita via Vision",
        language="por",
        confidence=None,
        engine="openai",
    )

    with patch(
        "app.vision_service.extract_text_with_vision",
        new=AsyncMock(return_value=vision_result),
    ):
        response = client.post(
            "/ocr?engine=openai",
            headers={"X-Api-Key": api_key},
            files={"file": ("rx.png", _make_png_bytes(), "image/png")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["engine"] == "openai"
    assert body["text"] == "Receita via Vision"


def test_ocr_structures_prescription_when_openai_configured(
    client: TestClient,
    api_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.schemas import StructuredPrescription

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OCR_STRUCTURE_ENABLED", "true")
    get_settings.cache_clear()

    fake_data = {"conf": ["90"], "text": ["Dipirona"]}
    structured = StructuredPrescription(
        date="19/07/2026",
        header="Dr. Fulano CRM 12345\nClínica Exemplo",
        patient="Maria Silva",
        inscription="Dipirona 500mg comprimido",
        posology="1 comprimido a cada 6 horas",
        items=[],
    )

    with (
        patch("app.ocr_service.configure_tesseract"),
        patch("app.ocr_service.pytesseract.image_to_string", return_value="Dipirona 500mg"),
        patch("app.ocr_service.pytesseract.image_to_data", return_value=fake_data),
        patch(
            "app.main.structure_prescription_text",
            new=AsyncMock(return_value=structured),
        ),
    ):
        response = client.post(
            "/ocr",
            headers={"X-Api-Key": api_key},
            files={"file": ("rx.png", _make_png_bytes(), "image/png")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["engine"] == "tesseract"
    assert body["structuredBy"] == "openai"
    assert body["structured"]["date"] == "19/07/2026"
    assert body["structured"]["patient"] == "Maria Silva"
    assert "Dipirona" in body["structured"]["inscription"]
    assert "6 horas" in body["structured"]["posology"]


def test_parse_structured_payload_accepts_portuguese_keys() -> None:
    from app.structure_service import parse_structured_payload

    parsed = parse_structured_payload(
        {
            "data": "01/01/2026",
            "cabecalho": "Dr. A CRM 1",
            "paciente": "João",
            "inscricao": "Amoxicilina 500mg cápsula",
            "posologia": "1 cápsula 8/8h",
            "items": [
                {
                    "drugName": "Amoxicilina",
                    "pharmaceuticalForm": "cápsula",
                    "concentration": "500mg",
                    "posology": "1 cápsula 8/8h",
                }
            ],
        }
    )
    assert parsed.date == "01/01/2026"
    assert parsed.header == "Dr. A CRM 1"
    assert parsed.patient == "João"
    assert parsed.inscription is not None
    assert len(parsed.items) == 1
    assert parsed.items[0].drug_name == "Amoxicilina"
