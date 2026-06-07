"""Mã lỗi + exception theo từng STAGE của pipeline (dùng cho AI service API).

Triết lý: API không trả JSON cấu trúc, chỉ trả trạng thái + exam_id; nếu lỗi thì
báo rõ LỖI Ở STAGE NÀO kèm mã lỗi để BE/đối tác bắt theo mã (không parse message).

Mỗi stage có 1 mã `Exxx`. `PipelineStageError` mang theo stage + code + http_status.
Pipeline raise lỗi này; API bắt và dựng response lỗi tương ứng.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StageError:
    """Định nghĩa 1 loại lỗi stage: mã, tên stage (machine), http status."""
    code: str          # vd "E102"
    stage: str         # vd "ocr" — định danh máy đọc
    label: str         # mô tả ngắn tiếng Việt
    http_status: int   # HTTP trả về cho client


class ErrorCodes:
    """Bảng mã lỗi toàn pipeline. Tham chiếu: ErrorCodes.OCR ..."""

    # --- Input / validation ---
    INVALID_INPUT = StageError("E400", "input", "File đầu vào không hợp lệ", 400)
    UNSUPPORTED_TYPE = StageError("E415", "input", "Định dạng file không hỗ trợ", 415)
    EMPTY_FILE = StageError("E422", "input", "File rỗng", 422)

    # --- Pipeline stages (xử lý) ---
    PREPROCESS = StageError("E101", "preprocess", "Lỗi render/tiền xử lý PDF", 500)
    OCR = StageError("E102", "ocr", "Lỗi PaddleOCR (layout/OCR)", 500)
    ANCHOR = StageError("E103", "anchor", "Lỗi trích xuất anchor", 500)
    SNAKE = StageError("E104", "snake_walker", "Lỗi Snake Walker (gom câu/nhóm)", 500)
    CLASSIFY = StageError("E105", "classify", "Lỗi phân loại câu hỏi", 500)
    CROP = StageError("E106", "crop", "Lỗi cắt ảnh / overlay", 500)

    # --- Storage / persistence (phụ thuộc hạ tầng → 502) ---
    MINIO = StageError("E107", "minio_upload", "Lỗi upload MinIO", 502)
    MONGO = StageError("E108", "mongo_save", "Lỗi lưu lịch sử MongoDB", 502)

    # --- Catch-all ---
    UNKNOWN = StageError("E500", "unknown", "Lỗi không xác định", 500)


# Tra cứu nhanh code -> StageError (cho tài liệu / test)
ALL_CODES = {
    se.code: se
    for se in vars(ErrorCodes).values()
    if isinstance(se, StageError)
}


class PipelineStageError(Exception):
    """Lỗi xảy ra ở 1 stage cụ thể của pipeline.

    Attributes:
        spec: StageError (mã + stage + http_status).
        detail: thông điệp chi tiết (nguyên nhân gốc) để log/trace.
        exam_id: id đề (nếu đã cấp) để client tra cứu.
    """

    def __init__(
        self,
        spec: StageError,
        detail: str = "",
        exam_id: str | None = None,
    ):
        self.spec = spec
        self.detail = detail
        self.exam_id = exam_id
        super().__init__(f"[{spec.code}/{spec.stage}] {spec.label}: {detail}")

    @property
    def code(self) -> str:
        return self.spec.code

    @property
    def stage(self) -> str:
        return self.spec.stage

    @property
    def http_status(self) -> int:
        return self.spec.http_status

    def to_response(self) -> dict:
        """Dựng body JSON lỗi cho API."""
        return {
            "status": "failed",
            "exam_id": self.exam_id,
            "stage": self.spec.stage,
            "error_code": self.spec.code,
            "message": self.spec.label,
            "detail": self.detail,
        }
