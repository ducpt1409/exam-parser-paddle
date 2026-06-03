"""Stage 2: PaddleOCR PP-StructureV3 wrapper - layout + OCR per page.

Architecture:
  - PP-StructureV3 (lang='en'): layout detection ONLY (text/figure/table boxes)
    Layout model chỉ hỗ trợ en/ch, không có Vietnamese.
  - PaddleOCR (lang='vi'): Vietnamese OCR riêng, sau đó merge text vào layout blocks
    bằng bbox containment.

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


def _bbox_overlap_ratio(inner: BBox, outer: BBox) -> float:
    """Tỷ lệ diện tích inner nằm trong outer. 0 = không overlap, 1 = fully inside."""
    ix1, iy1, ix2, iy2 = inner
    ox1, oy1, ox2, oy2 = outer
    # Overlap rectangle
    x1 = max(ix1, ox1)
    y1 = max(iy1, oy1)
    x2 = min(ix2, ox2)
    y2 = min(iy2, oy2)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    overlap_area = (x2 - x1) * (y2 - y1)
    inner_area = max((ix2 - ix1) * (iy2 - iy1), 1)
    return overlap_area / inner_area


class PaddleParser:
    """Wrapper PaddleOCR: PP-StructureV3 (layout) + PaddleOCR (Vietnamese OCR).

    PP-Structure không hỗ trợ layout model tiếng Việt nên ta tách:
      - Layout: PP-StructureV3 với lang='en'
      - OCR: PaddleOCR với lang='vi'
      - Merge: gán OCR lines vào layout blocks theo bbox containment.

    Cache predictor instances để tránh reload model mỗi page.
    """

    # Layout model chỉ hỗ trợ 'en' và 'ch'
    LAYOUT_LANG = "en"

    def __init__(
        self,
        use_gpu: Optional[bool] = None,
        lang: str = "vi",
        det_limit_side_len: int = 2400,
        cpu_threads: int = 8,
    ):
        self.use_gpu = use_gpu if use_gpu is not None else settings.paddle_use_gpu
        self.lang = lang   # cho OCR
        self.det_limit_side_len = det_limit_side_len
        self.cpu_threads = cpu_threads
        self._structure = None  # PP-Structure (layout only)
        self._ocr = None        # PaddleOCR (Vietnamese OCR)

    def _ensure_engines(self):
        """Lazy load cả 2 engines."""
        from paddleocr import PaddleOCR, PPStructure

        if self._structure is None:
            logger.info(
                f"Loading PP-StructureV3 layout-only "
                f"(GPU={self.use_gpu}, layout_lang={self.LAYOUT_LANG})..."
            )
            self._structure = PPStructure(
                layout=True,
                table=True,
                ocr=False,             # ← KHÔNG dùng OCR của PP-Structure
                use_gpu=self.use_gpu,
                lang=self.LAYOUT_LANG, # 'en' cho layout model
                show_log=False,
                cpu_threads=self.cpu_threads,
                det_limit_side_len=self.det_limit_side_len,
            )
            logger.info("PP-Structure loaded")

        if self._ocr is None:
            logger.info(
                f"Loading PaddleOCR (GPU={self.use_gpu}, lang={self.lang})..."
            )
            self._ocr = PaddleOCR(
                use_angle_cls=False,
                lang=self.lang,
                use_gpu=self.use_gpu,
                show_log=False,
                cpu_threads=self.cpu_threads,
                det_limit_side_len=self.det_limit_side_len,
            )
            logger.info("PaddleOCR Vietnamese loaded")

    def parse_page(self, image: Image.Image, page_index: int) -> list[Block]:
        """Parse 1 trang → list[Block] với text + bbox."""
        self._ensure_engines()
        arr = np.array(image)

        # Layout detection (không OCR)
        raw_blocks = self._structure(arr)

        # Vietnamese OCR toàn trang
        ocr_result = self._ocr.ocr(arr, cls=False)
        ocr_lines = self._parse_ocr_result(ocr_result)

        # Convert + merge OCR vào blocks theo bbox containment
        blocks = self._convert_and_merge(raw_blocks, ocr_lines, page_index)

        logger.debug(
            f"  Page {page_index}: {len(blocks)} blocks "
            f"({sum(1 for b in blocks if b.is_text_block)} text, "
            f"{sum(1 for b in blocks if b.is_visual_block)} visual), "
            f"{len(ocr_lines)} OCR lines"
        )
        return blocks

    def parse_pages(self, images: list[Image.Image]) -> list[list[Block]]:
        """Parse list pages → list[list[Block]]."""
        result = []
        for idx, img in enumerate(images):
            result.append(self.parse_page(img, idx))
        return result

    @staticmethod
    def _parse_ocr_result(ocr_result) -> list[TextLine]:
        """Convert PaddleOCR raw output → list[TextLine]."""
        lines: list[TextLine] = []
        if not ocr_result or not ocr_result[0]:
            return lines
        for item in ocr_result[0]:
            # item = [polygon, (text, confidence)]
            if not item or len(item) < 2:
                continue
            polygon, (text, conf) = item[0], item[1]
            text = (text or "").strip()
            if not text:
                continue
            lines.append(TextLine(
                text=text,
                bbox=_to_bbox(polygon),
                confidence=float(conf),
            ))
        return lines

    def _convert_and_merge(
        self,
        raw_blocks: list[dict],
        ocr_lines: list[TextLine],
        page_index: int,
    ) -> list[Block]:
        """Convert layout blocks + assign OCR lines theo bbox overlap."""
        blocks: list[Block] = []
        # Track lines đã assign để không double-count
        used_indices: set[int] = set()

        for block_idx, raw in enumerate(raw_blocks):
            raw_type = (raw.get("type") or "text").lower()
            block_type = _TYPE_MAP.get(raw_type, BlockType.OTHER)
            bbox = _to_bbox(raw["bbox"]) if "bbox" in raw else (0, 0, 0, 0)

            # Assign OCR lines có >= 50% overlap với block bbox
            block_lines: list[TextLine] = []
            for li, line in enumerate(ocr_lines):
                if li in used_indices:
                    continue
                if _bbox_overlap_ratio(line.bbox, bbox) >= 0.5:
                    block_lines.append(line)
                    used_indices.add(li)

            # Sort lines theo y (top-down)
            block_lines.sort(key=lambda l: l.bbox[1])

            # Table extra
            extra = {}
            res = raw.get("res")
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
                lines=block_lines,
                confidence=float(raw.get("confidence", 1.0)),
                extra=extra,
            ))

        # Orphan OCR lines (không thuộc block layout nào) → tạo block TEXT cho mỗi line
        # Đảm bảo không miss text. Phổ biến: text giữa figures, ở rìa.
        for li, line in enumerate(ocr_lines):
            if li in used_indices:
                continue
            blocks.append(Block(
                page_index=page_index,
                block_index=len(blocks),
                type=BlockType.TEXT,
                bbox=line.bbox,
                lines=[line],
                confidence=line.confidence,
                extra={"orphan": True},
            ))

        return blocks
