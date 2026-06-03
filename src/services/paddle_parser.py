"""Stage 2: PaddleOCR PP-StructureV3 wrapper - layout + OCR per page.

Output: list[Block] với bbox + text per line, type (text/figure/table/...).

Usage:
    from src.services.paddle_parser import PaddleParser
    parser = PaddleParser(use_gpu=False, lang="vi")
    blocks = parser.parse_page(image, page_index=0)
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from PIL import Image

from src.core.config import settings
from src.core.logging import logger
from src.schemas.block import BBox, Block, BlockType, TextLine

# Map từ PaddleOCR PP-Structure type → BlockType enum
_TYPE_MAP = {
    "text": BlockType.TEXT,
    "title": BlockType.TITLE,
    "list": BlockType.LIST,
    "table": BlockType.TABLE,
    "figure": BlockType.FIGURE,
    "image": BlockType.FIGURE,
    "equation": BlockType.EQUATION,
    "formula": BlockType.EQUATION,
    "header": BlockType.HEADER,
    "footer": BlockType.FOOTER,
    "reference": BlockType.REFERENCE,
}


def _to_bbox(coords) -> BBox:
    """Convert PaddleOCR polygon hoặc rect → (x1, y1, x2, y2)."""
    arr = np.array(coords).reshape(-1, 2)
    x1, y1 = arr.min(axis=0)
    x2, y2 = arr.max(axis=0)
    return (float(x1), float(y1), float(x2), float(y2))


class PaddleParser:
    """Wrapper PaddleOCR PP-StructureV3.

    Cache predictor instance để tránh reload model mỗi page.
    """

    def __init__(
        self,
        use_gpu: Optional[bool] = None,
        lang: str = "vi",
        det_limit_side_len: int = 2400,
        cpu_threads: int = 8,
    ):
        self.use_gpu = use_gpu if use_gpu is not None else settings.paddle_use_gpu
        self.lang = lang
        self.det_limit_side_len = det_limit_side_len
        self.cpu_threads = cpu_threads
        self._engine = None  # lazy init

    def _ensure_engine(self):
        """Lazy load PaddleOCR engine."""
        if self._engine is not None:
            return
        # Import trong method để tránh load Paddle khi import module
        from paddleocr import PPStructure

        logger.info(
            f"Loading PaddleOCR PP-StructureV3 "
            f"(GPU={self.use_gpu}, lang={self.lang})..."
        )
        self._engine = PPStructure(
            layout=True,
            table=True,
            ocr=True,
            use_gpu=self.use_gpu,
            lang=self.lang,
            show_log=False,
            cpu_threads=self.cpu_threads,
            det_limit_side_len=self.det_limit_side_len,
        )
        logger.info("PaddleOCR engine loaded")

    def parse_page(self, image: Image.Image, page_index: int) -> list[Block]:
        """Parse 1 trang → list[Block]."""
        self._ensure_engine()
        arr = np.array(image)
        raw_results = self._engine(arr)
        blocks = self._convert(raw_results, page_index)
        logger.debug(
            f"  Page {page_index}: {len(blocks)} blocks "
            f"({sum(1 for b in blocks if b.is_text_block)} text, "
            f"{sum(1 for b in blocks if b.is_visual_block)} visual)"
        )
        return blocks

    def parse_pages(self, images: list[Image.Image]) -> list[list[Block]]:
        """Parse list pages → list[list[Block]] (1 list per page)."""
        result = []
        for idx, img in enumerate(images):
            result.append(self.parse_page(img, idx))
        return result

    def _convert(self, raw_blocks: list[dict], page_index: int) -> list[Block]:
        """Convert PaddleOCR raw output → list[Block] schema."""
        blocks = []
        for block_idx, raw in enumerate(raw_blocks):
            raw_type = (raw.get("type") or "text").lower()
            block_type = _TYPE_MAP.get(raw_type, BlockType.OTHER)
            bbox = _to_bbox(raw["bbox"]) if "bbox" in raw else (0, 0, 0, 0)

            # Extract text lines (chỉ cho text blocks)
            lines: list[TextLine] = []
            res = raw.get("res")

            # Trường hợp text block: res là list[dict]
            if isinstance(res, list):
                for item in res:
                    text = item.get("text", "").strip()
                    if not text:
                        continue
                    text_bbox = _to_bbox(
                        item.get("text_region")
                        or item.get("bbox")
                        or bbox
                    )
                    lines.append(TextLine(
                        text=text,
                        bbox=text_bbox,
                        confidence=float(item.get("confidence", 1.0)),
                    ))

            # Trường hợp table: res là dict với html
            extra = {}
            if isinstance(res, dict):
                if "html" in res:
                    extra["table_html"] = res["html"]
                if "cell_bbox" in res:
                    extra["cell_bbox"] = res["cell_bbox"]

            blocks.append(Block(
                page_index=page_index,
                block_index=block_idx,
                type=block_type,
                bbox=bbox,
                lines=lines,
                confidence=float(raw.get("confidence", 1.0)),
                extra=extra,
            ))
        return blocks
