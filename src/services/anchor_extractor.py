"""Stage 3: Extract anchors (Câu N, A./B./C./D., Phần I, ...) từ Block.text.

Sử dụng regex OCR-tolerant (strip dấu) để robust với OCR errors về dấu tiếng Việt.

Usage:
    from src.services.anchor_extractor import extract_anchors
    anchors = extract_anchors(blocks_per_page)
"""
from __future__ import annotations

import re
import unicodedata
from typing import Pattern

from src.core.logging import logger
from src.schemas.anchor import Anchor, AnchorType
from src.schemas.block import Block, BlockType, TextLine


# ============================================================
# Regex patterns (OCR-tolerant, đã strip dấu)
# ============================================================
# Mỗi entry: (pattern, value_group_index)
#   - pattern: compiled regex (case-insensitive)
#   - value_group_index: regex group chứa value (None nếu không có)
ANCHOR_PATTERNS: dict[AnchorType, list[tuple[Pattern, int | None]]] = {
    AnchorType.QUESTION: [
        # "Câu 1:", "Câu 1.", "Bài 1.", "Question 1:"
        (re.compile(r"^\s*(?:cau|bai|question)\s+(\d+)\s*[\.\:]", re.IGNORECASE), 1),
        # "1." hoặc "1)" ở đầu dòng (cho tự luận đánh số)
        # CHỈ match nếu không có A-D ngay sau (tránh "1. A.")
        # (lower priority - check sau)
    ],
    AnchorType.ANSWER: [
        # "A.", "B.", "C.", "D." ở đầu dòng - đáp án trắc nghiệm
        (re.compile(r"^\s*([A-D])\s*[\.\)]\s+\S"), 1),
    ],
    AnchorType.SUB_QUESTION: [
        # "a)", "b)", "c)", "d)" - đúng/sai
        (re.compile(r"^\s*([a-d])\s*\)\s+\S"), 1),
    ],
    AnchorType.GROUP_HEADER: [
        (re.compile(r"^\s*(phan)\s+[ivxlcdm\d]", re.IGNORECASE), None),
        (re.compile(r"^\s*(doc\s+(?:doan|van\s+ban))", re.IGNORECASE), None),
        (re.compile(r"^\s*(cho\s+(?:doan|doan\s+van|bieu\s+do|bang))", re.IGNORECASE), None),
        (re.compile(r"^\s*(mark\s+the\s+letter)", re.IGNORECASE), None),
        (re.compile(r"^\s*(read\s+the\s+following)", re.IGNORECASE), None),
        (re.compile(r"^\s*(choose\s+the)", re.IGNORECASE), None),
        (re.compile(r"^\s*(dua\s+vao)", re.IGNORECASE), None),
    ],
    AnchorType.METADATA: [
        (re.compile(r"^\s*ma\s+de", re.IGNORECASE), None),
        (re.compile(r"^\s*thoi\s+gian", re.IGNORECASE), None),
        (re.compile(r"^\s*ho\s+va\s+ten", re.IGNORECASE), None),
        (re.compile(r"^\s*so\s+gd|truong\s+thpt|truong\s+thcs|truong\s+th", re.IGNORECASE), None),
        (re.compile(r"^\s*(de|de thi|de kiem tra)", re.IGNORECASE), None),
        (re.compile(r"^\s*(ky\s+thi|nam\s+hoc)", re.IGNORECASE), None),
        (re.compile(r"^\s*(mon|mon:)", re.IGNORECASE), None),
    ],
    AnchorType.FOOTER: [
        # Page number "trang N" hoặc số đứng lẻ
        (re.compile(r"^\s*(trang\s+\d+|---+|het|chuc)", re.IGNORECASE), None),
    ],
}

# Text blocks acceptable for anchor extraction
ANCHOR_BLOCK_TYPES = {
    BlockType.TEXT, BlockType.TITLE, BlockType.LIST,
    BlockType.HEADER, BlockType.FOOTER,
}


def strip_accents(text: str) -> str:
    """Bỏ dấu tiếng Việt: 'Câu' → 'Cau', 'đ' → 'd'."""
    nfkd = unicodedata.normalize("NFKD", text)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return no_accents.replace("đ", "d").replace("Đ", "D")


def _match_line(text: str, normalized_text: str) -> list[tuple[AnchorType, str | None, str]]:
    """Match 1 line text với tất cả patterns. Trả về list (type, value, matched_text).

    Có thể match nhiều anchor trên 1 line (vd line vừa là METADATA vừa là TITLE).
    Nhưng chỉ trả về 1 anchor "ưu tiên cao nhất" - theo thứ tự:
    1. QUESTION (số câu)
    2. GROUP_HEADER
    3. ANSWER
    4. SUB_QUESTION
    5. METADATA
    6. FOOTER
    """
    priority_order = [
        AnchorType.QUESTION,
        AnchorType.GROUP_HEADER,
        AnchorType.ANSWER,
        AnchorType.SUB_QUESTION,
        AnchorType.METADATA,
        AnchorType.FOOTER,
    ]

    for anchor_type in priority_order:
        for pattern, value_idx in ANCHOR_PATTERNS[anchor_type]:
            m = pattern.match(normalized_text)
            if m:
                value = m.group(value_idx) if value_idx else None
                return [(anchor_type, value, m.group(0))]
    return []


def extract_anchors(blocks_per_page: list[list[Block]]) -> list[Anchor]:
    """Extract anchors từ tất cả blocks per page.

    Args:
        blocks_per_page: output của PaddleParser.parse_pages()

    Returns:
        Flat list[Anchor] - chưa sort. Caller sort theo (page, y) nếu cần.
    """
    anchors: list[Anchor] = []

    for blocks in blocks_per_page:
        for block in blocks:
            if block.type not in ANCHOR_BLOCK_TYPES:
                continue
            for line in block.lines:
                text = line.text.strip()
                if not text:
                    continue
                normalized = strip_accents(text).strip()

                matches = _match_line(text, normalized)
                for anchor_type, value, matched_text in matches:
                    anchors.append(Anchor(
                        page_index=block.page_index,
                        type=anchor_type,
                        bbox=line.bbox,
                        text=matched_text,
                        value=value,
                        confidence=line.confidence,
                        source="regex",
                    ))

    logger.info(f"Extracted {len(anchors)} anchors total")
    _log_anchor_stats(anchors)
    return anchors


def _log_anchor_stats(anchors: list[Anchor]):
    """Log thống kê anchors theo type."""
    from collections import Counter
    counter = Counter(a.type.value for a in anchors)
    summary = ", ".join(f"{t}={n}" for t, n in counter.most_common())
    logger.info(f"  Anchor breakdown: {summary}")
