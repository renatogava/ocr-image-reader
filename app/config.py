from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    ocr_api_key: str = "change-me-to-a-secure-key"
    tesseract_cmd: str | None = None
    ocr_language: str = "por"
    max_image_bytes: int = 10 * 1024 * 1024
    download_timeout_seconds: float = 30.0

    # auto: Tesseract primeiro; Vision se confiança baixa / sem texto
    # tesseract: só Tesseract
    # openai: só Vision
    ocr_engine: Literal["auto", "tesseract", "openai"] = "auto"
    ocr_confidence_threshold: float = 60.0

    # JSON: {"tenant-a":"sk-...","tenant-b":"sk-..."}
    openai_api_keys_by_tenant: str | None = None
    # Preenchido só via model_copy por request (não lê OPENAI_API_KEY do ambiente)
    openai_api_key: str | None = Field(
        default=None,
        validation_alias="OCR_INTERNAL_RESOLVED_OPENAI_KEY",
    )
    openai_base_url: str = "https://api.openai.com/v1"
    openai_vision_model: str = "gpt-4o-mini"
    openai_max_tokens: int = 2000
    openai_timeout_seconds: float = 60.0

    # Estruturação pós-OCR (sempre que possível, se houver chave do tenant)
    ocr_structure_enabled: bool = True
    openai_structure_model: str = "gpt-4o-mini"
    openai_structure_max_tokens: int = 1500

    def parsed_openai_keys_by_tenant(self) -> dict[str, str]:
        raw = (self.openai_api_keys_by_tenant or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("OPENAI_API_KEYS_BY_TENANT inválido (JSON)")
            return {}
        if not isinstance(parsed, dict):
            logger.warning("OPENAI_API_KEYS_BY_TENANT deve ser um objeto JSON")
            return {}
        result: dict[str, str] = {}
        for key, value in parsed.items():
            tenant = str(key).strip()
            secret = str(value).strip() if value is not None else ""
            if tenant and secret:
                result[tenant] = secret
        return result

    def has_any_openai_key(self) -> bool:
        return bool(self.parsed_openai_keys_by_tenant())

    def get_openai_api_key_for_tenant(self, tenant_id: str) -> str | None:
        tenant = (tenant_id or "").strip()
        if not tenant:
            return None
        return self.parsed_openai_keys_by_tenant().get(tenant)


@lru_cache
def get_settings() -> Settings:
    return Settings()
