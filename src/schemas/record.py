"""Schema bản ghi lịch sử đề thi lưu vào MongoDB.

Mỗi đề thi upload + trích xuất = 1 ExamRecord (1 document trong collection `exams`).
`_id` của Mongo = exam_id. `output` chứa toàn bộ cấu trúc Exam đã trích xuất.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from src.schemas.exam import Exam


class RawFile(BaseModel):
    """File gốc (PDF) đã upload lên MinIO."""
    filename: str
    minio_key: str
    url: Optional[str] = None
    size_bytes: Optional[int] = None
    content_type: str = "application/pdf"


class ExamRecord(BaseModel):
    """1 bản ghi lịch sử trích xuất đề thi (1 document MongoDB)."""
    exam_id: str                      # = _id trong Mongo
    source_file: str                  # tên file gốc
    status: str = "done"              # done | failed | processing
    created_at: str                   # ISO datetime (set lúc lưu)

    # Thống kê nhanh (để list/filter không cần đọc output)
    n_pages: int = 0
    n_questions: int = 0
    n_groups: int = 0
    n_mcq: int = 0
    n_essay: int = 0

    # Vị trí trên MinIO
    bucket: str = ""
    minio_prefix: str = ""            # vd "exams/{exam_id}/"
    raw: Optional[RawFile] = None     # file gốc PDF

    # Dữ liệu trích xuất
    metadata: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)  # full Exam.model_dump()

    @classmethod
    def from_exam(
        cls,
        exam: Exam,
        created_at: str,
        bucket: str = "",
        minio_prefix: str = "",
        raw: Optional[RawFile] = None,
        status: str = "done",
    ) -> "ExamRecord":
        """Dựng bản ghi từ Exam đã trích xuất."""
        return cls(
            exam_id=exam.exam_id,
            source_file=exam.source_file,
            status=status,
            created_at=created_at,
            n_pages=exam.n_pages,
            n_questions=exam.n_questions,
            n_groups=exam.n_groups,
            n_mcq=exam.n_mcq,
            n_essay=exam.n_essay,
            bucket=bucket,
            minio_prefix=minio_prefix,
            raw=raw,
            metadata=exam.metadata.model_dump(),
            output=exam.model_dump(mode="json"),
        )

    def to_mongo_doc(self) -> dict[str, Any]:
        """Chuyển sang dict cho Mongo, dùng exam_id làm _id."""
        doc = self.model_dump(mode="json")
        doc["_id"] = doc["exam_id"]
        return doc
