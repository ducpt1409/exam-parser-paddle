"""Response models cho AI service API.

API CHỈ trả trạng thái xử lý + exam_id (KHÔNG trả cấu trúc JSON câu hỏi).
Khi lỗi → trả stage + error_code để client bắt theo mã.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ParseSuccess(BaseModel):
    """Xử lý thành công."""
    status: str = "done"
    exam_id: str
    message: str = "Đã xử lý xong"
    # Vài số liệu tóm tắt để client biết kết quả (không phải full JSON)
    n_pages: int = 0
    n_questions: int = 0
    n_groups: int = 0
    bucket: str = ""
    minio_prefix: str = ""        # vd "exams/abc123/"


class ParseError(BaseModel):
    """Xử lý lỗi ở 1 stage."""
    status: str = "failed"
    exam_id: Optional[str] = None
    stage: str                    # preprocess | ocr | anchor | snake_walker | classify | crop | minio_upload | mongo_save | input | unknown
    error_code: str               # Exxx
    message: str
    detail: str = ""


class HealthResponse(BaseModel):
    status: str = "ok"
    vlm_enabled: bool = False
    minio_endpoint: str = ""
    mongo_enabled: bool = False
