from __future__ import annotations

import base64
import logging

import httpx

from app.config import Settings
from app.ocr_service import OcrError, OcrResult

logger = logging.getLogger(__name__)

VISION_PROMPT = """Você é um OCR especializado em receitas médicas em português do Brasil.

Extraia TODO o texto legível da imagem da receita (impressa ou manuscrita).
Regras:
- Transcreva fielmente; não invente medicamentos, doses ou nomes.
- Se algo estiver ilegível, use [ilegível].
- Preserve quebras de linha relevantes (itens, posologia).
- Responda SOMENTE com o texto extraído, sem comentários, markdown ou prefixos.
"""


def _guess_mime(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if image_bytes.startswith(b"RIFF") and b"WEBP" in image_bytes[:16]:
        return "image/webp"
    if image_bytes.startswith(b"II*\x00") or image_bytes.startswith(b"MM\x00*"):
        return "image/tiff"
    if image_bytes.startswith(b"BM"):
        return "image/bmp"
    return "image/jpeg"


async def extract_text_with_vision(
    image_bytes: bytes,
    settings: Settings,
) -> OcrResult:
    if not settings.openai_api_key:
        raise OcrError(
            "OPENAI_API_KEY não configurada para OCR com Vision",
            status_code=503,
        )

    mime = _guess_mime(image_bytes)
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"

    payload = {
        "model": settings.openai_vision_model,
        "temperature": 0,
        "max_tokens": settings.openai_max_tokens,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url, "detail": "high"},
                    },
                ],
            }
        ],
    }

    headers = {
        "Authorization": f"Bearer {settings.openai_api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=settings.openai_timeout_seconds) as client:
            response = await client.post(
                f"{settings.openai_base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
            )
    except httpx.HTTPError as exc:
        logger.warning("Falha de rede na API OpenAI Vision: %s", exc)
        raise OcrError("Falha ao chamar a API de Vision", status_code=502) from exc

    if response.status_code >= 400:
        detail = response.text[:300]
        logger.warning("OpenAI Vision HTTP %s: %s", response.status_code, detail)
        raise OcrError(
            f"API de Vision retornou erro HTTP {response.status_code}",
            status_code=502,
        )

    try:
        body = response.json()
        text = (body["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise OcrError("Resposta inválida da API de Vision", status_code=502) from exc

    if not text:
        raise OcrError("Vision não extraiu texto da imagem", status_code=422)

    return OcrResult(
        text=text,
        language=settings.ocr_language,
        confidence=None,
        engine="openai",
    )
