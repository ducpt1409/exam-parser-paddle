"""Stage 7b: MinIO client (Phase 4) — upload ảnh crop + JSON, sinh presigned URL.

Bọc MinIO SDK với fail-safe + log. Dùng bởi `uploader.upload_exam_assets`.

Usage:
    from src.services.minio_client import MinIOService
    svc = MinIOService()                      # đọc settings từ .env
    key = svc.upload_file("crops/q1_full.png", "exam123/crops/q1_full.png")
    url = svc.presigned_url(key)              # link tải 7 ngày
"""
from __future__ import annotations

import io
import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

from minio import Minio
from minio.error import S3Error

from src.core.config import settings
from src.core.logging import logger


class MinIOService:
    """Wrapper MinIO: ensure bucket, upload file/bytes/json, presigned URL."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        bucket: Optional[str] = None,
        secure: Optional[bool] = None,
    ):
        self.endpoint = endpoint or settings.minio_endpoint
        self.bucket = bucket or settings.minio_bucket
        self.secure = settings.minio_secure if secure is None else secure
        self.client = Minio(
            self.endpoint,
            access_key=access_key or settings.minio_access_key,
            secret_key=secret_key or settings.minio_secret_key,
            secure=self.secure,
        )
        self.ensure_bucket()

    # ------------------------------------------------------------
    def ensure_bucket(self) -> None:
        """Tạo bucket nếu chưa có."""
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
                logger.info(f"MinIO: tạo bucket '{self.bucket}'")
        except S3Error as e:
            logger.error(f"MinIO: lỗi ensure_bucket '{self.bucket}' — {e}")
            raise

    # ------------------------------------------------------------
    def upload_file(
        self, local_path: str | Path, key: str, content_type: str = "image/png"
    ) -> str:
        """Upload file từ đĩa lên MinIO. Trả về object key."""
        self.client.fput_object(
            self.bucket, key, str(local_path), content_type=content_type
        )
        return key

    def upload_bytes(
        self, data: bytes, key: str, content_type: str = "application/octet-stream"
    ) -> str:
        """Upload bytes lên MinIO. Trả về object key."""
        self.client.put_object(
            self.bucket, key, io.BytesIO(data), length=len(data),
            content_type=content_type,
        )
        return key

    def upload_json(self, obj: Any, key: str) -> str:
        """Upload object JSON (dict/list) hoặc chuỗi JSON sẵn lên MinIO."""
        if isinstance(obj, (str, bytes)):
            data = obj.encode("utf-8") if isinstance(obj, str) else obj
        else:
            data = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        return self.upload_bytes(data, key, content_type="application/json")

    # ------------------------------------------------------------
    def presigned_url(self, key: str, expires_days: Optional[int] = None) -> str:
        """Sinh presigned GET URL (mặc định TTL = settings.minio_presign_days)."""
        days = expires_days if expires_days is not None else settings.minio_presign_days
        return self.client.presigned_get_object(
            self.bucket, key, expires=timedelta(days=days)
        )

    def public_url(self, key: str) -> str:
        """URL tĩnh (chỉ dùng được nếu bucket/policy công khai)."""
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.endpoint}/{self.bucket}/{key}"
