from fastapi import Header, HTTPException, status

from app.config import Settings, get_settings


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
    settings: Settings | None = None,
) -> None:
    configured = (settings or get_settings()).ocr_api_key
    if not x_api_key or x_api_key != configured:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key inválida ou ausente",
        )
