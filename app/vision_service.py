from __future__ import annotations

import base64
import json
import logging
import re

import httpx

from app.config import Settings
from app.ocr_service import OcrError, OcrResult

logger = logging.getLogger(__name__)

ILLEGIBLE_TOKEN = "[ilegível]"
ILLEGIBLE_PENALTY_PER_HIT = 8.0
ILLEGIBLE_PENALTY_CAP = 40.0

VISION_PROMPT = """Você é um OCR especializado em receitas médicas em português do Brasil.

Extraia TODO o texto legível da imagem da receita (impressa ou manuscrita) e estime a confiança da transcrição.

Regras:
- Transcreva fielmente; não invente medicamentos, doses ou nomes.
- Se algo estiver ilegível, use [ilegível].
- Preserve quebras de linha relevantes (itens, posologia).
- "confidence" é um número de 0 a 100: 100 = leitura muito segura; 0 = quase nada legível.
  Considere qualidade da imagem, caligrafia e trechos [ilegível].
- Responda SOMENTE com JSON válido (sem markdown), neste formato:
{"text":"...","confidence":85.0}
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


def _strip_code_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _clamp_confidence(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def _adjust_confidence_for_illegible(text: str, confidence: float) -> float:
    hits = len(re.findall(re.escape(ILLEGIBLE_TOKEN), text, flags=re.IGNORECASE))
    if hits <= 0:
        return _clamp_confidence(confidence)
    penalty = min(ILLEGIBLE_PENALTY_CAP, hits * ILLEGIBLE_PENALTY_PER_HIT)
    return _clamp_confidence(confidence - penalty)


def _parse_vision_payload(raw: str) -> tuple[str, float]:
    """Extrai (text, confidence) da resposta Vision.

    Aceita JSON {"text","confidence"} ou texto puro (fallback, confidence heurística).
    """
    cleaned = _strip_code_fence(raw)
    if not cleaned:
        raise OcrError("Vision não extraiu texto da imagem", status_code=422)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Modelo às vezes devolve texto puro; estima confiança por [ilegível]
        base = 70.0
        return cleaned, _adjust_confidence_for_illegible(cleaned, base)

    if not isinstance(parsed, dict):
        raise OcrError("Resposta inválida da API de Vision", status_code=502)

    text = parsed.get("text")
    if text is None:
        raise OcrError("Resposta da Vision sem campo text", status_code=502)
    text_str = str(text).strip()
    if not text_str:
        raise OcrError("Vision não extraiu texto da imagem", status_code=422)

    conf_raw = parsed.get("confidence")
    try:
        confidence = float(conf_raw) if conf_raw is not None else 70.0
    except (TypeError, ValueError):
        confidence = 70.0

    return text_str, _adjust_confidence_for_illegible(text_str, confidence)


async def extract_text_with_vision(
    image_bytes: bytes,
    settings: Settings,
) -> OcrResult:
    if not settings.openai_api_key:
        raise OcrError(
            "Nenhuma chave OpenAI configurada para OCR com Vision (tenantId)",
            status_code=503,
        )

    mime = _guess_mime(image_bytes)
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"

    payload = {
        "model": settings.openai_vision_model,
        "temperature": 0,
        "max_tokens": settings.openai_max_tokens,
        "response_format": {"type": "json_object"},
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
        raw_content = (body["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise OcrError("Resposta inválida da API de Vision", status_code=502) from exc

    text, confidence = _parse_vision_payload(raw_content)

    return OcrResult(
        text=text,
        language=settings.ocr_language,
        confidence=confidence,
        engine="openai",
    )
