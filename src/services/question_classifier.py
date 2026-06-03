"""Stage 5: Phân loại Question Type (rule-based, KHÔNG dùng VLM).

Cây quyết định theo §4.1 PHASE2_GUIDE:
1. Group PASSAGE → READING_COMPREHENSION
2. n_answers >= 3 → MCQ_SINGLE (hoặc MCQ_MULTI nếu có keyword)
3. n_answers == 0:
   - Có sub-question a)/b)/c)/d) → TRUE_FALSE
   - Có chỗ trống → FILL_BLANK
   - Content ngắn → SHORT_ANSWER
   - Còn lại → ESSAY
4. n_answers in {1,2} → UNKNOWN + needs_review

Usage:
    from src.services.question_classifier import classify_all
    classify_all(questions, groups)
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

from src.core.logging import logger
from src.schemas.exam import Group, GroupType, Question, QuestionType


# ============================================================
# Từ khóa nhận diện (strip dấu, lowercase)
# ============================================================

# MCQ chọn nhiều
_MCQ_MULTI_KEYWORDS = [
    "chon nhieu", "chon tat ca", "chon 2", "chon hai",
    "select all", "more than one", "nhieu dap an",
]

# Chỗ trống (fill blank)
_FILL_BLANK_REGEX = re.compile(
    r"_{2,}|"         # ____
    r"\.{3,}|"        # ...
    r"\(\s*\.\.\.\s*\)|"  # (...)
    r"…"              # …
)

# Tự luận dài
_ESSAY_KEYWORDS = [
    "giai", "chung minh", "trinh bay", "tinh", "viet doan",
    "phan tich", "giai thich", "giai cac phuong trinh",
    "hay giai", "hay trinh bay", "hay tinh", "hay chung minh",
    "hay viet", "hay phan tich", "lam bai",
    "tim tat ca", "tim nghiem", "tim gia tri",
]


def _strip_accents(text: str) -> str:
    """Bỏ dấu tiếng Việt."""
    nfkd = unicodedata.normalize("NFKD", text)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return no_accents.replace("đ", "d").replace("Đ", "D")


def classify(question: Question, group: Optional[Group] = None) -> QuestionType:
    """Phân loại QuestionType cho 1 câu hỏi theo cây quyết định rule-based.

    Args:
        question: câu hỏi cần phân loại.
        group: group chứa câu (nếu có), dùng để phát hiện READING_COMPREHENSION.

    Returns:
        QuestionType đã phân loại.
    """
    content_norm = _strip_accents(question.content_text).lower().strip()

    # 1. Câu thuộc group PASSAGE → READING_COMPREHENSION
    if group and group.type == GroupType.PASSAGE:
        return QuestionType.READING_COMPREHENSION

    # 2. Đếm đáp án có label A-D
    answer_labels = [a.label.upper() for a in question.answers if a.label.upper() in "ABCD"]
    n_answers = len(answer_labels)

    if n_answers >= 3:
        # Kiểm tra chọn nhiều
        for kw in _MCQ_MULTI_KEYWORDS:
            if kw in content_norm:
                return QuestionType.MCQ_MULTI
        return QuestionType.MCQ_SINGLE

    if n_answers == 0:
        # Kiểm tra sub-question (đúng/sai 4 ý)
        # Sub-question pattern: a) b) c) d) trong content
        sub_pattern = re.findall(r"\b[a-d]\s*\)", content_norm)
        if len(sub_pattern) >= 2:
            return QuestionType.TRUE_FALSE

        # Kiểm tra chỗ trống → FILL_BLANK
        if _FILL_BLANK_REGEX.search(question.content_text):
            return QuestionType.FILL_BLANK

        # Phân biệt SHORT_ANSWER vs ESSAY
        word_count = len(content_norm.split())

        # Kiểm tra keyword tự luận dài
        for kw in _ESSAY_KEYWORDS:
            if kw in content_norm:
                return QuestionType.ESSAY

        # Content ngắn (<15 từ) và yêu cầu tính/điền → SHORT_ANSWER
        if word_count < 15:
            return QuestionType.SHORT_ANSWER

        # Mặc định tự luận dài
        return QuestionType.ESSAY

    # n_answers in {1, 2} → bất thường
    question.needs_review = True
    logger.warning(
        f"Câu {question.number}: chỉ có {n_answers} đáp án → UNKNOWN, needs_review"
    )
    return QuestionType.UNKNOWN


def classify_all(questions: list[Question], groups: list[Group]) -> None:
    """Gán type in-place cho tất cả câu hỏi.

    Args:
        questions: danh sách câu hỏi (sẽ gán .type in-place).
        groups: danh sách groups (để tra cứu PASSAGE).
    """
    # Xây map group_id → Group
    group_map: dict[str, Group] = {g.id: g for g in groups}

    # Thống kê
    type_counts: dict[str, int] = {}

    for q in questions:
        group = group_map.get(q.group_id) if q.group_id else None
        q.type = classify(q, group)

        # MCQ nhưng số đáp án ≠ 4 → needs_review
        if q.type in (QuestionType.MCQ_SINGLE, QuestionType.READING_COMPREHENSION):
            n_ans = len(q.answers)
            if n_ans != 4 and n_ans > 0:
                q.needs_review = True
                logger.warning(
                    f"Câu {q.number}: type={q.type.value} nhưng chỉ có {n_ans} đáp án"
                )

        type_name = q.type.value
        type_counts[type_name] = type_counts.get(type_name, 0) + 1

    # Log breakdown
    breakdown = ", ".join(f"{t}={n}" for t, n in sorted(type_counts.items()))
    logger.info(f"Classifier: {breakdown}")
