from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.auth import require_api_key
from app.config import Settings, get_settings
from app.ocr_service import OcrEngine, OcrError, download_image, extract_text_async, tesseract_version
from app.schemas import HealthResponse, OcrResponse, OcrUrlRequest
from app.structure_service import structure_prescription_text

app = FastAPI(
    title="OCR Image Reader",
    description=(
        "API de OCR para receitas médicas: Tesseract com pré-processamento "
        "(binarização, deskew, denoise), fallback opcional para OpenAI Vision "
        "(manuscritos / baixa confiança) e estruturação do texto em campos "
        "(data, cabeçalho, paciente, inscrição, posologia)."
    ),
    version="1.2.0",
)


def _settings_dep() -> Settings:
    return get_settings()


def _parse_bool_form(value: object) -> bool | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "sim"}:
        return True
    if normalized in {"0", "false", "no", "nao", "não"}:
        return False
    return None


@app.get("/health", response_model=HealthResponse)
def health(settings: Annotated[Settings, Depends(_settings_dep)]) -> HealthResponse:
    version = tesseract_version()
    return HealthResponse(
        status="ok" if version else "degraded",
        tesseract=version,
        openai_configured=bool(settings.openai_api_key),
    )


@app.post("/ocr", response_model=OcrResponse)
async def ocr(
    request: Request,
    settings: Annotated[Settings, Depends(_settings_dep)],
    x_api_key: Annotated[str | None, Header(alias="X-Api-Key")] = None,
    engine: Annotated[
        Literal["auto", "tesseract", "openai"] | None,
        Query(description="Sobrescreve OCR_ENGINE: auto | tesseract | openai"),
    ] = None,
    structure: Annotated[
        bool | None,
        Query(description="Se true/false, força ou desliga a estruturação via OpenAI"),
    ] = None,
) -> OcrResponse:
    require_api_key(x_api_key, settings)

    content_type = (request.headers.get("content-type") or "").lower()
    chosen_engine: OcrEngine | None = engine
    want_structure = structure

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
            if body.engine:
                chosen_engine = body.engine
            if body.structure is not None:
                want_structure = body.structure
        elif "multipart/form-data" in content_type:
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Campo file é obrigatório no multipart",
                )
            data = await upload.read()
            form_engine = form.get("engine")
            if isinstance(form_engine, str) and form_engine in {"auto", "tesseract", "openai"}:
                chosen_engine = form_engine  # type: ignore[assignment]
            form_structure = _parse_bool_form(form.get("structure"))
            if form_structure is not None:
                want_structure = form_structure
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Content-Type deve ser multipart/form-data ou application/json",
            )

        result = await extract_text_async(data, settings, engine=chosen_engine)

        structured = None
        structured_by = None
        should_structure = (
            settings.ocr_structure_enabled if want_structure is None else want_structure
        )
        if should_structure and settings.openai_api_key:
            structured = await structure_prescription_text(result.text, settings)
            structured_by = "openai"
        elif should_structure and want_structure is True and not settings.openai_api_key:
            raise OcrError(
                "OPENAI_API_KEY não configurada para estruturação da receita",
                status_code=503,
            )
    except HTTPException:
        raise
    except OcrError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    return OcrResponse(
        success=True,
        text=result.text,
        language=result.language,
        confidence=result.confidence,
        engine=result.engine,
        structured=structured,
        structured_by=structured_by,
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
