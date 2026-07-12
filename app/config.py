from functools import lru_cache

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
