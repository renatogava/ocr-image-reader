from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class OcrUrlRequest(BaseModel):
    image_url: HttpUrl = Field(..., alias="imageUrl")
    tenant_id: str = Field(..., alias="tenantId", min_length=1)
    engine: Literal["auto", "tesseract", "openai"] | None = None
    structure: bool | None = None

    model_config = {"populate_by_name": True}


class PrescriptionItem(BaseModel):
    drug_name: str | None = Field(None, alias="drugName")
    pharmaceutical_form: str | None = Field(None, alias="pharmaceuticalForm")
    concentration: str | None = None
    posology: str | None = None

    model_config = {"populate_by_name": True}


class StructuredPrescription(BaseModel):
    date: str | None = None
    header: str | None = None
    patient: str | None = None
    inscription: str | None = None
    posology: str | None = None
    items: list[PrescriptionItem] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class OcrResponse(BaseModel):
    success: bool
    text: str
    language: str
    confidence: float | None = None
    engine: str = "tesseract"
    structured: StructuredPrescription | None = None
    structured_by: str | None = Field(None, alias="structuredBy")

    model_config = {"populate_by_name": True}


class HealthResponse(BaseModel):
    status: str
    tesseract: str | None = None
    openai_configured: bool = False
