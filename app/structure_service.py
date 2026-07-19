from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.config import Settings
from app.ocr_service import OcrError
from app.schemas import PrescriptionItem, StructuredPrescription

logger = logging.getLogger(__name__)

STRUCTURE_SYSTEM_PROMPT = """Você organiza texto OCR de receitas médicas brasileiras em JSON estruturado.

Regras:
- Use APENAS informações presentes no texto. Não invente.
- Se um campo não existir ou estiver ilegível, use null.
- "header": dados do médico e do consultório/instituição, incluindo assinatura e registro no conselho (CRM/CRF/etc.) quando houver.
- "patient": identificação do paciente (nome e outros dados do paciente).
- "inscription": nome do fármaco, forma farmacêutica e concentração (texto consolidado; se houver vários, junte com quebras de linha).
- "posology": como tomar / posologia (texto consolidado; se houver várias, junte com quebras de linha).
- "date": data da receita no formato mais fiel ao texto (ex.: 19/07/2026).
- "items": lista opcional de medicamentos, cada um com drugName, pharmaceuticalForm, concentration e posology quando possível.
- Responda SOMENTE com JSON válido, sem markdown.
"""

STRUCTURE_SCHEMA_HINT = """
Formato JSON esperado:
{
  "date": string|null,
  "header": string|null,
  "patient": string|null,
  "inscription": string|null,
  "posology": string|null,
  "items": [
    {
      "drugName": string|null,
      "pharmaceuticalForm": string|null,
      "concentration": string|null,
      "posology": string|null
    }
  ]
}
"""


def _strip_code_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_items(raw_items: Any) -> list[PrescriptionItem]:
    if not isinstance(raw_items, list):
        return []
    items: list[PrescriptionItem] = []
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        item = PrescriptionItem(
            drugName=_as_optional_str(
                entry.get("drugName") or entry.get("drug_name") or entry.get("name")
            ),
            pharmaceuticalForm=_as_optional_str(
                entry.get("pharmaceuticalForm")
                or entry.get("pharmaceutical_form")
                or entry.get("form")
            ),
            concentration=_as_optional_str(entry.get("concentration")),
            posology=_as_optional_str(entry.get("posology")),
        )
        if any([item.drug_name, item.pharmaceutical_form, item.concentration, item.posology]):
            items.append(item)
    return items


def parse_structured_payload(payload: dict[str, Any]) -> StructuredPrescription:
    return StructuredPrescription(
        date=_as_optional_str(payload.get("date") or payload.get("data")),
        header=_as_optional_str(payload.get("header") or payload.get("cabecalho")),
        patient=_as_optional_str(
            payload.get("patient")
            or payload.get("paciente")
            or payload.get("patientIdentification")
        ),
        inscription=_as_optional_str(
            payload.get("inscription") or payload.get("inscricao")
        ),
        posology=_as_optional_str(payload.get("posology") or payload.get("posologia")),
        items=_parse_items(payload.get("items") or payload.get("itens") or []),
    )


async def structure_prescription_text(
    raw_text: str,
    settings: Settings,
) -> StructuredPrescription:
    if not settings.openai_api_key:
        raise OcrError(
            "OPENAI_API_KEY não configurada para estruturação da receita",
            status_code=503,
        )

    payload = {
        "model": settings.openai_structure_model,
        "temperature": 0,
        "max_tokens": settings.openai_structure_max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": STRUCTURE_SYSTEM_PROMPT + STRUCTURE_SCHEMA_HINT},
            {
                "role": "user",
                "content": (
                    "Organize o texto OCR da receita abaixo:\n\n"
                    f"{raw_text}"
                ),
            },
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
        logger.warning("Falha de rede na estruturação OpenAI: %s", exc)
        raise OcrError("Falha ao estruturar a receita via OpenAI", status_code=502) from exc

    if response.status_code >= 400:
        logger.warning(
            "OpenAI structure HTTP %s: %s",
            response.status_code,
            response.text[:300],
        )
        raise OcrError(
            f"API de estruturação retornou erro HTTP {response.status_code}",
            status_code=502,
        )

    try:
        body = response.json()
        content = (body["choices"][0]["message"]["content"] or "").strip()
        data = json.loads(_strip_code_fence(content))
        if not isinstance(data, dict):
            raise TypeError("JSON raiz não é objeto")
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise OcrError("Resposta inválida na estruturação da receita", status_code=502) from exc

    return parse_structured_payload(data)
