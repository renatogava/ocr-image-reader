from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def api_key(monkeypatch: pytest.MonkeyPatch) -> str:
    key = "test-api-key"
    monkeypatch.setenv("OCR_API_KEY", key)
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
