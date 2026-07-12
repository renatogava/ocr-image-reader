from pydantic import BaseModel, Field, HttpUrl


class OcrUrlRequest(BaseModel):
    image_url: HttpUrl = Field(..., alias="imageUrl")

    model_config = {"populate_by_name": True}


class OcrResponse(BaseModel):
    success: bool
    text: str
    language: str
    confidence: float | None = None


class HealthResponse(BaseModel):
    status: str
    tesseract: str | None = None
