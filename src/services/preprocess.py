"""Stage 1: Load PDF/image + render + deskew → list[PIL.Image].

Usage:
    from src.services.preprocess import preprocess
    images = preprocess("input/de.pdf", dpi=300, do_deskew=True)
"""
from __future__ import annotations

from pathlib import Path

import cv2
import fitz  # PyMuPDF
import numpy as np
from PIL import Image

from src.core.logging import logger

SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}


def load_input(input_path: str, dpi: int = 300) -> list[Image.Image]:
    """Load PDF/image → list[PIL.Image].

    PDF: render mỗi trang ở DPI chỉ định (default 300).
    Image: load trực tiếp.
    """
    path = Path(input_path)
    ext = path.suffix.lower()

    if ext == ".pdf":
        return _render_pdf(path, dpi)
    if ext in SUPPORTED_IMAGE_EXTS:
        return [Image.open(input_path).convert("RGB")]

    raise ValueError(f"Định dạng không hỗ trợ: {ext}")


def _render_pdf(pdf_path: Path, dpi: int) -> list[Image.Image]:
    """Render PDF → list[PIL.Image]."""
    doc = fitz.open(str(pdf_path))
    zoom = dpi / 72.0  # PDF native = 72 DPI
    mat = fitz.Matrix(zoom, zoom)

    images = []
    for page_idx, page in enumerate(doc):
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        images.append(img)
        logger.debug(f"  Rendered page {page_idx + 1}: {pix.width}×{pix.height}")
    doc.close()

    logger.info(f"Loaded {len(images)} pages từ PDF (DPI={dpi})")
    return images


def deskew(img: Image.Image, threshold_degrees: float = 0.5) -> Image.Image:
    """Detect skew angle và xoay thẳng.

    Skip nếu angle < threshold_degrees (PDF render thường thẳng sẵn).
    """
    arr = np.array(img)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

    # Otsu threshold để binarize
    _, thresh = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )

    # Tìm các pixel text
    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) < 100:
        return img  # ảnh trống, không deskew

    # minAreaRect trả về (center, (w, h), angle)
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    if abs(angle) < threshold_degrees:
        return img  # không đáng để xoay

    # Rotate
    h, w = arr.shape[:2]
    M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    rotated = cv2.warpAffine(
        arr, M, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    logger.debug(f"  Deskewed by {angle:.2f}°")
    return Image.fromarray(rotated)


def preprocess(
    input_path: str,
    dpi: int = 300,
    do_deskew: bool = True,
    deskew_threshold: float = 0.5,
) -> list[Image.Image]:
    """Full preprocess: load + deskew.

    Returns:
        list[PIL.Image] ready for OCR
    """
    logger.info(f"Preprocessing: {input_path}")
    images = load_input(input_path, dpi=dpi)
    if do_deskew:
        images = [deskew(img, deskew_threshold) for img in images]
    return images
