"""Router: 1 endpoint duy nhất nhận file đề thi → chạy pipeline → trả trạng thái.

POST /api/v1/exams/parse  (multipart/form-data, field "file")
  - Lưu file upload vào thư mục tạm.
  - Chạy ExamPipeline().run(): Paddle + Snake → cắt ảnh + overlay → upload MinIO →
    lưu Mongo. KHÔNG giữ file local (xóa thư mục tạm sau khi xong).
  - Trả 200 + {status: done, exam_id, ...} nếu thành công.
  - Trả mã lỗi HTTP + body {status: failed, stage, error_code, ...} nếu lỗi stage nào.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse

from src.api.schemas import ParseError, ParseSuccess
from src.core.config import settings
from src.core.errors import ErrorCodes, PipelineStageError
from src.core.logging import logger

router = APIRouter()

# Định dạng cho phép
ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg"}
ALLOWED_CT = {
    "application/pdf", "image/png", "image/jpeg", "image/jpg",
    "application/octet-stream",  # 1 số client gửi PDF dạng này
}


def _error_response(err: PipelineStageError) -> JSONResponse:
    body = ParseError(**err.to_response()).model_dump()
    return JSONResponse(status_code=err.http_status, content=body)


@router.post("/parse", summary="Nhận file đề thi, xử lý và lưu lên MinIO/Mongo")
async def parse_exam(file: UploadFile = File(...)):
    exam_id = str(uuid4())[:8]

    # --- Validate input ---
    filename = file.filename or "upload"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        return _error_response(PipelineStageError(
            ErrorCodes.UNSUPPORTED_TYPE,
            detail=f"Đuôi file '{ext}' không hỗ trợ. Chỉ nhận: {sorted(ALLOWED_EXT)}",
            exam_id=exam_id,
        ))

    # --- Lưu file tạm ---
    tmp_root = Path(settings.temp_dir)
    tmp_root.mkdir(parents=True, exist_ok=True)
    upload_dir = Path(tempfile.mkdtemp(prefix=f"upload_{exam_id}_", dir=str(tmp_root)))
    saved = upload_dir / filename
    try:
        with saved.open("wb") as f:
            shutil.copyfileobj(file.file, f)
    except Exception as e:
        shutil.rmtree(upload_dir, ignore_errors=True)
        return _error_response(PipelineStageError(
            ErrorCodes.INVALID_INPUT, detail=str(e), exam_id=exam_id))
    finally:
        await file.close()

    if saved.stat().st_size == 0:
        shutil.rmtree(upload_dir, ignore_errors=True)
        return _error_response(PipelineStageError(
            ErrorCodes.EMPTY_FILE, detail="File rỗng (0 byte)", exam_id=exam_id))

    # --- Chạy pipeline ---
    try:
        from src.services.pipeline import ExamPipeline
        exam = ExamPipeline().run(saved, exam_id=exam_id)
    except PipelineStageError as err:
        logger.error(f"[API] exam {exam_id} lỗi: {err}")
        return _error_response(err)
    except Exception as e:
        logger.exception(f"[API] exam {exam_id} lỗi không xác định")
        return _error_response(PipelineStageError(
            ErrorCodes.UNKNOWN, detail=str(e), exam_id=exam_id))
    finally:
        shutil.rmtree(upload_dir, ignore_errors=True)

    prefix = settings.minio_prefix or ""
    resp = ParseSuccess(
        exam_id=exam.exam_id,
        message="Đã xử lý xong và lưu lên MinIO/Mongo",
        n_pages=exam.n_pages,
        n_questions=exam.n_questions,
        n_groups=exam.n_groups,
        bucket=settings.minio_bucket,
        minio_prefix=f"{prefix}{exam.exam_id}/",
    )
    return JSONResponse(status_code=200, content=resp.model_dump())
