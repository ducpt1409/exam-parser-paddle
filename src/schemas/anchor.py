"""Anchor schema - marker phát hiện được trong text."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel

from src.schemas.block import BBox


class AnchorType(str, Enum):
    """Loại anchor."""
    QUESTION = "question"          # "Câu N:", "Bài N.", "Question N:"
    ANSWER = "answer"              # "A.", "B.", "C.", "D."
    SUB_QUESTION = "sub_question"  # "a)", "b)" trong đúng/sai
    GROUP_HEADER = "group_header"  # "Phần I", "Mark the letter", "Đọc đoạn"
    PASSAGE = "passage"            # passage content marker
    METADATA = "metadata"          # "Mã đề", "Thời gian"
    FOOTER = "footer"              # page number, "Hết"


class Anchor(BaseModel):
    """1 anchor được detect."""
    page_index: int
    type: AnchorType
    bbox: BBox
    text: str                       # raw text của marker
    value: Optional[str] = None     # vd: "1" cho "Câu 1", "A" cho "A."
    confidence: float = 1.0
    source: str = "regex"           # "regex" / "vlm" / "hybrid"

    @property
    def number(self) -> Optional[int]:
        """Parse số nếu value là số (cho question/sub-question)."""
        if self.value and self.value.isdigit():
            return int(self.value)
        return None

    def global_position(self) -> tuple[int, float]:
        """Position để sort: (page_index, y_top)."""
        return (self.page_index, self.bbox[1])
