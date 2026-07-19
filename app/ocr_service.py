from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Literal

import httpx
import pytesseract
from PIL import Image, UnidentifiedImageError
from pytesseract import Output

from app.config import Settings, get_settings
from app.preprocess import preprocess_image

ALLOWED_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/tiff",
    "image/tif",
    "image/bmp",
}

OcrEngine = Literal["auto", "tesseract", "openai"]


class OcrError(Exception):
    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class OcrResult:
    text: str
    language: str
    confidence: float | None
    engine: str = "tesseract"


def configure_tesseract(settings: Settings | None = None) -> None:
    cfg = settings or get_settings()
    if cfg.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = cfg.tesseract_cmd


def tesseract_version() -> str | None:
    try:
        configure_tesseract()
        return str(pytesseract.get_tesseract_version())
    except Exception:
        return None


def _validate_size(data: bytes, settings: Settings) -> None:
    if len(data) == 0:
        raise OcrError("Imagem vazia", status_code=400)
    if len(data) > settings.max_image_bytes:
        raise OcrError(
            f"Imagem excede o limite de {settings.max_image_bytes} bytes",
            status_code=400,
        )


def _open_image(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return image
    except UnidentifiedImageError as exc:
        raise OcrError("Formato de imagem inválido ou não suportado", status_code=400) from exc
    except OSError as exc:
        raise OcrError("Não foi possível abrir a imagem", status_code=400) from exc


def _clean_text(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _average_confidence(data: dict) -> float | None:
    confidences: list[float] = []
    for conf, word in zip(data.get("conf", []), data.get("text", []), strict=False):
        try:
            value = float(conf)
        except (TypeError, ValueError):
            continue
        if value >= 0 and word and str(word).strip():
            confidences.append(value)
    if not confidences:
        return None
    return round(sum(confidences) / len(confidences), 1)


def _should_fallback_to_vision(
    text: str,
    confidence: float | None,
    settings: Settings,
) -> bool:
    if not settings.openai_api_key:
        return False
    if not text:
        return True
    if confidence is None:
        return True
    return confidence < settings.ocr_confidence_threshold


def extract_text_with_tesseract(data: bytes, settings: Settings | None = None) -> OcrResult:
    cfg = settings or get_settings()
    configure_tesseract(cfg)
    _validate_size(data, cfg)

    image = _open_image(data)
    processed = preprocess_image(image)

    try:
        custom_config = f"--psm 6 -l {cfg.ocr_language}"
        raw_text = pytesseract.image_to_string(processed, config=custom_config)
        data_dict = pytesseract.image_to_data(
            processed,
            config=custom_config,
            output_type=Output.DICT,
        )
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrError(
            "Tesseract não encontrado. Instale o binário ou configure TESSERACT_CMD.",
            status_code=500,
        ) from exc
    except pytesseract.TesseractError as exc:
        raise OcrError(f"Falha no OCR: {exc}", status_code=422) from exc

    text = _clean_text(raw_text)
    return OcrResult(
        text=text,
        language=cfg.ocr_language,
        confidence=_average_confidence(data_dict),
        engine="tesseract",
    )


def extract_text_from_bytes(data: bytes, settings: Settings | None = None) -> OcrResult:
    """Compat: apenas Tesseract (síncrono). Preferir extract_text_async."""
    result = extract_text_with_tesseract(data, settings)
    if not result.text:
        raise OcrError("Não foi possível extrair texto da imagem", status_code=422)
    return result


async def extract_text_async(
    data: bytes,
    settings: Settings | None = None,
    engine: OcrEngine | None = None,
) -> OcrResult:
    cfg = settings or get_settings()
    _validate_size(data, cfg)
    chosen: OcrEngine = engine or cfg.ocr_engine  # type: ignore[assignment]

    if chosen == "openai":
        from app.vision_service import extract_text_with_vision

        return await extract_text_with_vision(data, cfg)

    tesseract_error: OcrError | None = None
    result: OcrResult | None = None
    try:
        result = extract_text_with_tesseract(data, cfg)
    except OcrError as exc:
        tesseract_error = exc
        if chosen == "tesseract":
            raise

    if chosen == "tesseract":
        assert result is not None
        if not result.text:
            raise OcrError("Não foi possível extrair texto da imagem", status_code=422)
        return result

    # auto
    if result and not _should_fallback_to_vision(result.text, result.confidence, cfg):
        return result

    if not cfg.openai_api_key:
        if result and result.text:
            return result
        if tesseract_error:
            raise tesseract_error
        raise OcrError("Não foi possível extrair texto da imagem", status_code=422)

    from app.vision_service import extract_text_with_vision

    try:
        return await extract_text_with_vision(data, cfg)
    except OcrError:
        if result and result.text:
            return result
        raise


async def download_image(url: str, settings: Settings | None = None) -> bytes:
    cfg = settings or get_settings()
    try:
        async with httpx.AsyncClient(
            timeout=cfg.download_timeout_seconds,
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OcrError("Falha ao baixar a imagem da URL informada", status_code=502) from exc

    content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        if content_type not in {"application/octet-stream", "binary/octet-stream"}:
            raise OcrError(
                f"Content-Type não suportado: {content_type}",
                status_code=400,
            )

    data = response.content
    _validate_size(data, cfg)
    return data
