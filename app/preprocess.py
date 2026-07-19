from __future__ import annotations

import numpy as np
from PIL import Image

MIN_UPSCALE_SIDE = 1000


def _deskew(gray: np.ndarray) -> np.ndarray:
    """Corrige inclinação estimando o ângulo via minAreaRect no conteúdo binarizado."""
    import cv2

    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thresh > 0))
    if coords.size == 0 or len(coords) < 20:
        return gray

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    if abs(angle) < 0.5 or abs(angle) > 45:
        return gray

    height, width = gray.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    return cv2.warpAffine(
        gray,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def preprocess_image(image: Image.Image) -> Image.Image:
    """
    Pré-processamento para Tesseract:
    grayscale → denoise → deskew → binarização adaptativa → upscale se necessário.
    """
    import cv2

    rgb = image.convert("RGB")
    array = np.array(rgb)
    gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)

    denoised = cv2.fastNlMeansDenoising(gray, None, h=10, templateWindowSize=7, searchWindowSize=21)
    deskewed = _deskew(denoised)

    binary = cv2.adaptiveThreshold(
        deskewed,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        15,
    )

    kernel = np.ones((1, 1), np.uint8)
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    height, width = cleaned.shape[:2]
    longest = max(width, height)
    if 0 < longest < MIN_UPSCALE_SIDE:
        scale = MIN_UPSCALE_SIDE / longest
        cleaned = cv2.resize(
            cleaned,
            (max(1, int(width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_CUBIC,
        )

    return Image.fromarray(cleaned)
