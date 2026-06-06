"""Application settings loaded from .env via pydantic-settings."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration via env vars or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- MinIO ---
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "exam-parser"
    minio_secure: bool = False

    # --- Ollama ---
    ollama_host: str = "http://localhost:11434"
    ollama_vlm_model: str = "qwen3-vl:32b-instruct"
    ollama_timeout: int = 120

    # --- PaddleOCR ---
    # Default False vì Paddle chưa support Blackwell sm_120 (RTX 5090)
    paddle_use_gpu: bool = False
    paddle_ocr_lang: str = "vi"
    paddle_det_limit_side_len: int = 2400
    paddle_cpu_threads: int = 8

    # --- Pipeline ---
    default_dpi: int = 300
    use_vlm_verification: bool = False    # Phase 3 VLM tắt mặc định (Phase 4/5: Paddle+Snake+MinIO)
    use_vlm_type_classify: bool = False
    deskew_threshold_degrees: float = 0.5
    webp_quality: int = 85

    # --- MinIO upload (Phase 4) ---
    use_minio_upload: bool = True         # upload crop + exam.json lên MinIO
    minio_presign_days: int = 7           # TTL presigned URL (ngày)
    minio_prefix: str = ""                # prefix key tùy chọn, vd "exams/"
    minio_save_raw: bool = True           # upload luôn file gốc (PDF) lên MinIO

    # --- MongoDB (lịch sử đề thi) ---
    use_mongo: bool = True                # lưu mỗi đề = 1 bản ghi lịch sử
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "exam_parser"
    mongo_collection: str = "exams"       # collection lịch sử đề thi

    # --- API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_workers: int = 1
    cors_origins: str = "*"

    # --- Logging ---
    log_level: str = "INFO"
    log_file: Optional[str] = None

    # --- Storage ---
    local_output_dir: str = "./output"
    temp_dir: str = "/tmp/exam_parser"


# Singleton
settings = Settings()
