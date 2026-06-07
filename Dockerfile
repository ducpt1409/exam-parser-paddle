# ============================================================
# exam_parser_paddle — AI Service (PaddleOCR CPU + FastAPI)
# Đóng gói pipeline thành 1 API. Build lại mỗi khi đổi code.
#
# Build:  docker compose build ai-service
# Run:    docker compose up -d
# ============================================================
FROM python:3.11-slim

# --- System deps cho PaddleOCR / OpenCV / PyMuPDF ---
# libgl1, libglib2.0-0: OpenCV runtime; libgomp1: OpenMP (paddle); curl: healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        libsm6 \
        libxext6 \
        libxrender1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Python deps ---
# PaddlePaddle CPU cài RIÊNG trước (khớp host: 3.0.0). torch KHÔNG cần (chỉ verify_setup dùng).
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir paddlepaddle==3.0.0

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Source ---
COPY src/ ./src/
COPY scripts/ ./scripts/

# Thư mục cache model Paddle (~/.paddleocr) → mount volume ở compose để khỏi tải lại
ENV HOME=/root
ENV PYTHONPATH=/app
ENV TEMP_DIR=/tmp/exam_parser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
