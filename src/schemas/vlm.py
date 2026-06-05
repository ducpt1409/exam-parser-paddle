"""Pydantic schemas cho VLM request/response — Phase 3.

Dùng Ollama `format=<json schema>` để ép model trả JSON đúng cấu trúc.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class VLMQuestionType(str, Enum):
    """Loại câu hỏi — mirror QuestionType nhưng dùng cho VLM output."""
    MCQ_SINGLE = "trac_nghiem_1_dap_an"
    MCQ_MULTI = "trac_nghiem_nhieu_dap_an"
    TRUE_FALSE = "dung_sai"
    FILL_BLANK = "dien_dap_an"
    SHORT_ANSWER = "tu_luan_ngan"
    ESSAY = "tu_luan_dai"
    READING_COMPREHENSION = "doc_hieu"
    UNKNOWN = "unknown"


class VLMAnswer(BaseModel):
    """1 đáp án nhận dạng bởi VLM."""
    label: str                     # "A".."D"
    text: str = ""                 # nội dung đáp án (LaTeX nếu công thức)


class VLMQuestionResult(BaseModel):
    """Kết quả phân tích 1 câu hỏi bởi VLM.

    Schema này được gửi qua Ollama `format` parameter
    để ép model trả JSON đúng cấu trúc.
    """
    question_type: VLMQuestionType = VLMQuestionType.UNKNOWN
    n_answers: int = 0                 # số đáp án trắc nghiệm A/B/C/D nhìn thấy
    answers: list[VLMAnswer] = Field(default_factory=list)
    content_text: str = ""             # đề bài, LaTeX cho công thức
    has_figure: bool = False           # có hình/đồ thị/sơ đồ
    has_formula: bool = False
    has_table: bool = False
    content_complete: bool = True      # ảnh chứa TRỌN đề (không bị cắt)
    figure_complete: bool = True       # nếu có hình: hình có bị cắt mép không
    notes: str = ""                    # ghi chú bất thường (tùy chọn)
