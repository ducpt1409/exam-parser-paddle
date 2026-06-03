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
        # "Câu 1:", "Câu 1.", "Bài 1.", "Question 1:", "Câu 5 (4đ).", "Câu 6. (3đ)"
        # Lookahead: sau số phải là khoảng trắng / . : ) ( hoặc hết dòng.
        # → nuốt được phần điểm "(4đ)" nằm giữa số và dấu chấm.
        (re.compile(r"^\s*(?:cau|bai|question)\s+(\d+)(?=[\s\.\:\)\(]|$)", re.IGNORECASE), 1),
        # "1." hoặc "1)" ở đầu dòng (cho tự luận đánh số)
        # CHỈ match nếu không có A-D ngay sau (tránh "1. A.")
        # (lower priority - check sau)
    ],
    AnchorType.ANSWER: [
        # "A.", "B.", "C.", "D." ở đầu dòng - đáp án trắc nghiệm
        # Bug 2 fix: \s+ → \s* (cho phép 0 khoảng trắng, vd OCR "B.m =4")
        (re.compile(r"^\s*([A-D])\s*[\.\)]\s*\S"), 1),
    ],
    AnchorType.SUB_QUESTION: [
        # "a)", "b)", "c)", "d)" - đúng/sai
        # Bug 2 fix: \s+ → \s*
        (re.compile(r"^\s*([a-d])\s*\)\s*\S"), 1),
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

# LƯU Ý: KHÔNG filter theo block type khi extract anchor.
# Layout model 'en' của PP-Structure phân loại sai nhiều vùng tiếng Việt /
# cột đáp án thành 'figure'/'table' → nếu filter sẽ mất câu hỏi nằm trong đó
# (đã quan sát: Q23-28 đề Anh, Câu 6-7 đề Toán bị nuốt vào block 'figure').
# OCR text đáng tin hơn layout classification → quét tất cả block có text.
# Riêng các block thuần ảnh (không có line text) tự khắc bị bỏ qua vì rỗng.
SKIP_BLOCK_TYPES: set[BlockType] = set()

# Bug 4: Pattern quét NHIỀU đáp án inline trong cùng 1 dòng OCR
# vd: "A. x=1  B. x=2  C. x=3  D. x=4" → 4 answer anchor
ANSWER_INLINE_RE = re.compile(r"(?:^|\s)([A-D])\s*[\.\)]\s*")


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


def _extract_inline_answers(
    line: TextLine, page_index: int, normalized: str,
) -> list[Anchor]:
    """Bug 4: Quét NHIỀU đáp án inline trong cùng 1 dòng OCR.

    Khi 1 line chứa "A. x=1  B. x=2  C. x=3  D. x=4", _match_line chỉ
    trả về anchor A (do ^). Hàm này dùng finditer tìm thêm B/C/D.
    Bbox mỗi đáp án ước lượng theo tỉ lệ ký tự trong line.
    """
    matches = list(ANSWER_INLINE_RE.finditer(normalized))
    if len(matches) <= 1:
        return []  # chỉ có 1 hoặc 0 → _match_line đã xử lý

    results: list[Anchor] = []
    line_x1, line_y1, line_x2, line_y2 = line.bbox
    line_w = line_x2 - line_x1
    text_len = max(len(normalized), 1)

    for i, m in enumerate(matches):
        label = m.group(1)
        # Ước lượng bbox theo vị trí ký tự
        char_start = m.start()
        if i + 1 < len(matches):
            char_end = matches[i + 1].start()
        else:
            char_end = text_len
        frac_start = char_start / text_len
        frac_end = char_end / text_len
        est_x1 = line_x1 + frac_start * line_w
        est_x2 = line_x1 + frac_end * line_w

        # Text đáp án = substring từ match hiện tại đến match kế
        ans_text = normalized[m.start():char_end].strip()

        results.append(Anchor(
            page_index=page_index,
            type=AnchorType.ANSWER,
            bbox=(est_x1, line_y1, est_x2, line_y2),
            text=ans_text,
            value=label,
            confidence=line.confidence,
            source="regex_inline",
        ))

    return results


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
            if block.type in SKIP_BLOCK_TYPES:
                continue
            for line in block.lines:
                text = line.text.strip()
                if not text:
                    continue
                normalized = strip_accents(text).strip()

                matches = _match_line(text, normalized)
                if matches:
                    anchor_type, value, matched_text = matches[0]
                    if anchor_type == AnchorType.ANSWER:
                        # Bug 4: Check đáp án inline (nhiều đáp án cùng 1 line)
                        inline = _extract_inline_answers(line, block.page_index, normalized)
                        if inline:
                            anchors.extend(inline)
                        else:
                            anchors.append(Anchor(
                                page_index=block.page_index,
                                type=anchor_type,
                                bbox=line.bbox,
                                text=matched_text,
                                value=value,
                                confidence=line.confidence,
                                source="regex",
                            ))
                    else:
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
