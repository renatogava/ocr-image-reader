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
        "(data, cabeçalho, paciente, inscrição, posologia). "
        "Chave OpenAI resolvida por tenantId (OPENAI_API_KEYS_BY_TENANT)."
    ),
    version="1.3.0",
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


def _require_tenant_id(raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tenantId é obrigatório",
        )
    return raw.strip()


def _settings_for_tenant(settings: Settings, tenant_id: str) -> Settings:
    resolved = settings.get_openai_api_key_for_tenant(tenant_id)
    return settings.model_copy(update={"openai_api_key": resolved})


@app.get("/health", response_model=HealthResponse)
def health(settings: Annotated[Settings, Depends(_settings_dep)]) -> HealthResponse:
    version = tesseract_version()
    return HealthResponse(
        status="ok" if version else "degraded",
        tesseract=version,
        openai_configured=settings.has_any_openai_key(),
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
    tenant_id: str

    try:
        if "application/json" in content_type:
            payload = await request.json()
            try:
                body = OcrUrlRequest.model_validate(payload)
            except ValidationError as exc:
                # tenantId/imageUrl ausentes → 422 do pydantic; normaliza tenantId vazio
                errors = exc.errors()
                for err in errors:
                    if list(err.get("loc") or [])[-1:] == ["tenantId"] or list(
                        err.get("loc") or []
                    )[-1:] == ["tenant_id"]:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="tenantId é obrigatório",
                        ) from exc
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=errors,
                ) from exc
            tenant_id = body.tenant_id.strip()
            if not tenant_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="tenantId é obrigatório",
                )
            data = await download_image(str(body.image_url), settings)
            if body.engine:
                chosen_engine = body.engine
            if body.structure is not None:
                want_structure = body.structure
        elif "multipart/form-data" in content_type:
            form = await request.form()
            tenant_id = _require_tenant_id(form.get("tenantId"))
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

        effective = _settings_for_tenant(settings, tenant_id)
        result = await extract_text_async(data, effective, engine=chosen_engine)

        structured = None
        structured_by = None
        should_structure = (
            effective.ocr_structure_enabled if want_structure is None else want_structure
        )
        if should_structure and effective.openai_api_key:
            structured = await structure_prescription_text(result.text, effective)
            structured_by = "openai"
        elif should_structure and want_structure is True and not effective.openai_api_key:
            raise OcrError(
                f"Nenhuma chave OpenAI configurada para o tenantId '{tenant_id}'",
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
