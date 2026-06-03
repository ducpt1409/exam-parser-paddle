"""FastAPI service entry point.

TODO: Phase 4 - implement endpoints:
- POST /api/v1/exams/parse (upload PDF)
- GET /api/v1/exams/{exam_id}/status
- GET /api/v1/exams/{exam_id} (full JSON)
- GET /api/v1/exams/{exam_id}/preview (preview PDF)
- GET /api/v1/health

Run:
    uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
"""
from fastapi import FastAPI

from src.core.config import settings
from src.core.logging import logger

app = FastAPI(
    title="exam_parser_paddle",
    description="API bóc tách câu hỏi đề thi VN qua PaddleOCR + Qwen3-VL",
    version="0.1.0",
)


@app.get("/api/v1/health")
async def health():
    return {"status": "ok", "vlm_model": settings.ollama_vlm_model}


@app.on_event("startup")
async def startup():
    logger.info(f"Starting exam_parser_paddle API on {settings.api_host}:{settings.api_port}")


# TODO: Phase 4 - mount routers
# from src.api.routers import exams
# app.include_router(exams.router, prefix="/api/v1/exams", tags=["exams"])
