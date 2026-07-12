from __future__ import annotations

from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.auth import require_api_key
from app.config import Settings, get_settings
from app.ocr_service import OcrError, download_image, extract_text_from_bytes, tesseract_version
from app.schemas import HealthResponse, OcrResponse, OcrUrlRequest

app = FastAPI(
    title="OCR Image Reader",
    description=(
        "API de OCR para receitas médicas usando Tesseract. "
        "Consumida pelo PlaceQuotation do e-commerce."
    ),
    version="1.0.0",
)


def _settings_dep() -> Settings:
    return get_settings()


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    version = tesseract_version()
    return HealthResponse(
        status="ok" if version else "degraded",
        tesseract=version,
    )


@app.post("/ocr", response_model=OcrResponse)
async def ocr(
    request: Request,
    settings: Annotated[Settings, Depends(_settings_dep)],
    x_api_key: Annotated[str | None, Header(alias="X-Api-Key")] = None,
) -> OcrResponse:
    require_api_key(x_api_key, settings)

    content_type = (request.headers.get("content-type") or "").lower()

    try:
        if "application/json" in content_type:
            payload = await request.json()
            try:
                body = OcrUrlRequest.model_validate(payload)
            except ValidationError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=exc.errors(),
                ) from exc
            data = await download_image(str(body.image_url), settings)
        elif "multipart/form-data" in content_type:
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Campo file é obrigatório no multipart",
                )
            data = await upload.read()
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Content-Type deve ser multipart/form-data ou application/json",
            )

        result = extract_text_from_bytes(data, settings)
    except HTTPException:
        raise
    except OcrError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    return OcrResponse(
        success=True,
        text=result.text,
        language=result.language,
        confidence=result.confidence,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Erro interno do servidor"},
    )
