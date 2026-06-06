"""MongoDB client — lưu lịch sử trích xuất đề thi.

Mỗi đề = 1 document trong collection `exams` (`_id` = exam_id). Upsert để chạy lại
cùng exam_id sẽ ghi đè. Dùng bởi pipeline / parse_cli sau khi upload MinIO xong.

Usage:
    from src.services.mongo_client import MongoService
    svc = MongoService()
    svc.save_exam(exam, raw=raw_info)        # raw_info: dict từ upload_raw_file()
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pymongo import MongoClient

from src.core.config import settings
from src.core.logging import logger
from src.schemas.exam import Exam
from src.schemas.record import ExamRecord, RawFile


class MongoService:
    """Wrapper MongoDB: lưu / đọc bản ghi lịch sử đề thi."""

    def __init__(
        self,
        uri: Optional[str] = None,
        db: Optional[str] = None,
        collection: Optional[str] = None,
    ):
        self.uri = uri or settings.mongo_uri
        self.db_name = db or settings.mongo_db
        self.coll_name = collection or settings.mongo_collection
        # serverSelectionTimeoutMS thấp để fail nhanh nếu Mongo chưa chạy
        self.client = MongoClient(self.uri, serverSelectionTimeoutMS=5000)
        self.collection = self.client[self.db_name][self.coll_name]

    # ------------------------------------------------------------
    def save_exam(
        self,
        exam: Exam,
        raw: Optional[dict] = None,
        status: str = "done",
        created_at: Optional[str] = None,
    ) -> str:
        """Upsert 1 bản ghi lịch sử từ Exam. Trả về exam_id (=_id)."""
        created_at = created_at or datetime.now(timezone.utc).isoformat()
        raw_file = RawFile(**raw) if raw else None
        prefix = settings.minio_prefix or ""
        record = ExamRecord.from_exam(
            exam,
            created_at=created_at,
            bucket=settings.minio_bucket,
            minio_prefix=f"{prefix}{exam.exam_id}/",
            raw=raw_file,
            status=status,
        )
        doc = record.to_mongo_doc()
        self.collection.replace_one({"_id": exam.exam_id}, doc, upsert=True)
        logger.info(
            f"Mongo: lưu bản ghi exam {exam.exam_id} "
            f"({self.db_name}.{self.coll_name})"
        )
        return exam.exam_id

    # ------------------------------------------------------------
    def get_exam(self, exam_id: str) -> Optional[dict[str, Any]]:
        """Đọc 1 bản ghi theo exam_id."""
        return self.collection.find_one({"_id": exam_id})

    def list_exams(self, limit: int = 50, skip: int = 0) -> list[dict[str, Any]]:
        """List bản ghi (mới nhất trước), chỉ trả field tóm tắt (không kèm output)."""
        projection = {"output": 0}  # bỏ output cho nhẹ
        cursor = (
            self.collection.find({}, projection)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        return list(cursor)

    def ping(self) -> bool:
        """Kiểm tra kết nối Mongo."""
        try:
            self.client.admin.command("ping")
            return True
        except Exception as e:
            logger.error(f"Mongo ping lỗi — {e}")
            return False
