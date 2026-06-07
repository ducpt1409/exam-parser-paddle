"""FastAPI service — AI service bóc tách đề thi.

1 endpoint xử lý duy nhất:
    POST /api/v1/exams/parse   (upload file đề thi → chạy pipeline → trả trạng thái)

Phụ trợ:
    GET  /api/v1/health

Chạy local:
    uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
Chạy Docker: xem README_DOCKER.md
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routers import exams
from src.api.schemas import HealthResponse
from src.core.config import settings
from src.core.logging import logger

app = FastAPI(
    title="exam_parser_paddle — AI Service",
    description="Bóc tách câu hỏi đề thi VN (PaddleOCR + Snake Walker) → MinIO + MongoDB",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")] or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(exams.router, prefix="/api/v1/exams", tags=["exams"])


@app.get("/api/v1/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        vlm_enabled=settings.use_vlm_verification,
        minio_endpoint=settings.minio_endpoint,
        mongo_enabled=settings.use_mongo,
    )


@app.on_event("startup")
async def startup():
    logger.info(
        f"AI service start @ {settings.api_host}:{settings.api_port} "
        f"| MinIO={settings.minio_endpoint} | Mongo={settings.use_mongo} "
        f"| VLM={settings.use_vlm_verification}"
    )
