from __future__ import annotations

import io
import re
from dataclasses import dataclass

import httpx
import pytesseract
from PIL import Image, ImageEnhance, ImageOps, UnidentifiedImageError
from pytesseract import Output

from app.config import Settings, get_settings

ALLOWED_CONTENT_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/tiff",
    "image/tif",
    "image/bmp",
}

MIN_UPSCALE_SIDE = 1000


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


def preprocess_image(image: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(image)
    width, height = gray.size
    longest = max(width, height)
    if longest < MIN_UPSCALE_SIDE and longest > 0:
        scale = MIN_UPSCALE_SIDE / longest
        gray = gray.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.Resampling.LANCZOS,
        )
    enhanced = ImageEnhance.Contrast(gray).enhance(1.5)
    return enhanced


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


def extract_text_from_bytes(data: bytes, settings: Settings | None = None) -> OcrResult:
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
    if not text:
        raise OcrError("Não foi possível extrair texto da imagem", status_code=422)

    return OcrResult(
        text=text,
        language=cfg.ocr_language,
        confidence=_average_confidence(data_dict),
    )


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
        # Alguns CDNs omitem ou usam application/octet-stream; validamos pelo conteúdo depois
        if content_type not in {"application/octet-stream", "binary/octet-stream"}:
            raise OcrError(
                f"Content-Type não suportado: {content_type}",
                status_code=400,
            )

    data = response.content
    _validate_size(data, cfg)
    return data
