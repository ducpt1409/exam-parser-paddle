"""Schemas cho visual blocks từ PaddleOCR PP-Structure output."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class BlockType(str, Enum):
    """Loại block từ PaddleOCR PP-Structure."""
    TEXT = "text"
    TITLE = "title"
    LIST = "list"
    TABLE = "table"
    FIGURE = "figure"
    EQUATION = "equation"   # formula
    HEADER = "header"
    FOOTER = "footer"
    REFERENCE = "reference"
    OTHER = "other"


BBox = tuple[float, float, float, float]
"""(x1, y1, x2, y2) - pixel coordinates."""


class TextLine(BaseModel):
    """1 dòng text trong block (từ PaddleOCR OCR)."""
    text: str
    bbox: BBox
    confidence: float = 1.0


class Block(BaseModel):
    """1 visual block trên trang (text/figure/table/...)."""
    page_index: int
    block_index: int                    # vị trí block trong trang
    type: BlockType
    bbox: BBox
    lines: list[TextLine] = Field(default_factory=list)
    confidence: float = 1.0
    extra: dict = Field(default_factory=dict)   # vd: table_html cho table

    @property
    def text(self) -> str:
        """Concatenated text từ tất cả lines."""
        return " ".join(line.text for line in self.lines)

    @property
    def is_text_block(self) -> bool:
        return self.type in (BlockType.TEXT, BlockType.TITLE,
                              BlockType.LIST, BlockType.HEADER, BlockType.FOOTER)

    @property
    def is_visual_block(self) -> bool:
        """Block không có text - figure, table image, equation image."""
        return self.type in (BlockType.FIGURE, BlockType.TABLE, BlockType.EQUATION)
