from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
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

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_vision_model: str = "gpt-4o-mini"
    openai_max_tokens: int = 2000
    openai_timeout_seconds: float = 60.0

    # Estruturação pós-OCR (sempre que possível, se houver OPENAI_API_KEY)
    ocr_structure_enabled: bool = True
    openai_structure_model: str = "gpt-4o-mini"
    openai_structure_max_tokens: int = 1500


@lru_cache
def get_settings() -> Settings:
    return Settings()
