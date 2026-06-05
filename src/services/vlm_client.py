"""Stage 7a: Qwen3-VL client — gọi Ollama phân tích ảnh câu hỏi.

Low-level client: encode ảnh base64, gọi Ollama API, parse structured JSON.
Async (httpx) + wrapper sync. Retry 1 lần khi lỗi. Fail-safe: trả None.

Usage:
    from src.services.vlm_client import analyze_question
    result = analyze_question(image_path, q_number=1, q_type="unknown", n_ans=2)
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image

from src.core.config import settings
from src.core.logging import logger
from src.schemas.vlm import VLMQuestionResult


# ============================================================
# Constants
# ============================================================

# Chiều cao tối đa ảnh gửi VLM (px). Ảnh lớn hơn sẽ resize giữ tỉ lệ.
MAX_IMAGE_HEIGHT = 1600

# Prompt hệ thống — ép VLM mô tả cấu trúc, KHÔNG giải bài.
SYSTEM_PROMPT = (
    "Bạn là trợ lý phân tích đề thi. Bạn CHỈ nhìn ảnh và mô tả CẤU TRÚC, KHÔNG giải bài. "
    "Trả về JSON đúng schema. Công thức toán viết bằng LaTeX. Nếu không chắc, để giá trị mặc định."
)

# Template prompt user — kèm context từ Phase 2.
USER_PROMPT_TEMPLATE = (
    "Đây là ảnh 1 câu hỏi (số {number}). Phase trước đoán: loại={type}, số đáp án={n_ans}.\n"
    "Hãy xác định:\n"
    "- Loại câu hỏi (question_type).\n"
    "- Số đáp án trắc nghiệm A/B/C/D nhìn thấy (n_answers) + nội dung từng đáp án (answers).\n"
    "- Nội dung đề bài (content_text), công thức để LaTeX.\n"
    "- Có hình/đồ thị (has_figure), công thức (has_formula), bảng (has_table) không.\n"
    "- Ảnh có chứa TRỌN đề không (content_complete) — nếu thấy đề bị cắt cụt ở mép trên/dưới thì false.\n"
    "- Nếu có hình: hình có bị cắt mép không (figure_complete)."
)


# ============================================================
# VLM Logger — ghi riêng vlm.log
# ============================================================

_vlm_logger = None


def _get_vlm_logger(out_dir: Optional[Path] = None):
    """Lazy-init VLM logger ghi file vlm.log."""
    global _vlm_logger
    if _vlm_logger is not None:
        return _vlm_logger

    from loguru import logger as _loguru
    _vlm_logger = _loguru.bind(vlm=True)

    if out_dir:
        log_path = out_dir / "vlm.log"
        _loguru.add(
            str(log_path),
            filter=lambda record: record["extra"].get("vlm", False),
            format="{time:HH:mm:ss} | {message}",
            level="DEBUG",
        )

    return _vlm_logger


# ============================================================
# Image helpers
# ============================================================

def _load_and_encode_image(image_path: Path) -> tuple[str, int, int]:
    """Load ảnh, resize nếu quá lớn, encode base64.

    Returns:
        (base64_string, width, height)
    """
    img = Image.open(image_path)

    # Resize nếu quá cao (Qwen3-VL xử lý tốt ảnh vừa)
    if img.height > MAX_IMAGE_HEIGHT:
        ratio = MAX_IMAGE_HEIGHT / img.height
        new_w = int(img.width * ratio)
        img = img.resize((new_w, MAX_IMAGE_HEIGHT), Image.LANCZOS)

    # Convert sang RGB nếu cần (PNG có thể là RGBA)
    if img.mode != "RGB":
        img = img.convert("RGB")

    # Encode base64 (JPEG để giảm size gửi qua API)
    import io
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    return b64, img.width, img.height


# ============================================================
# Async VLM call
# ============================================================

async def analyze_question_async(
    image_path: Path,
    q_number: int,
    q_type: str = "unknown",
    n_ans: int = 0,
    out_dir: Optional[Path] = None,
) -> Optional[VLMQuestionResult]:
    """Gọi Ollama VLM phân tích 1 câu hỏi (async).

    Args:
        image_path: đường dẫn ảnh full_image hoặc content_image.
        q_number: số câu (để log + prompt context).
        q_type: loại câu Phase 2 đoán (context).
        n_ans: số đáp án Phase 2 tìm được (context).
        out_dir: thư mục output (cho vlm.log).

    Returns:
        VLMQuestionResult hoặc None nếu lỗi.
    """
    vlm_log = _get_vlm_logger(out_dir)

    if not image_path.exists():
        vlm_log.warning(f"q{q_number}: ảnh không tồn tại {image_path}")
        return None

    # Encode ảnh
    try:
        b64_img, img_w, img_h = _load_and_encode_image(image_path)
    except Exception as e:
        vlm_log.error(f"q{q_number}: lỗi đọc ảnh — {e}")
        return None

    # Build prompt
    user_text = USER_PROMPT_TEMPLATE.format(
        number=q_number, type=q_type, n_ans=n_ans
    )

    # JSON schema cho structured output
    json_schema = VLMQuestionResult.model_json_schema()

    # Ollama API payload
    payload = {
        "model": settings.ollama_vlm_model,
        "stream": False,
        "format": json_schema,
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": user_text,
                "images": [b64_img],
            },
        ],
    }

    url = f"{settings.ollama_host}/api/chat"
    timeout = settings.ollama_timeout

    # Gọi API (retry 1 lần)
    for attempt in range(2):
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException:
            elapsed = time.time() - t0
            vlm_log.warning(f"q{q_number}: timeout ({elapsed:.1f}s), attempt {attempt + 1}/2")
            if attempt == 0:
                continue
            return None
        except Exception as e:
            elapsed = time.time() - t0
            vlm_log.error(f"q{q_number}: lỗi API ({elapsed:.1f}s) — {e}, attempt {attempt + 1}/2")
            if attempt == 0:
                continue
            return None
        break  # thành công

    elapsed = time.time() - t0

    # Parse response
    try:
        content = data.get("message", {}).get("content", "")
        result = VLMQuestionResult.model_validate_json(content)
    except Exception as e:
        vlm_log.error(
            f"q{q_number}: parse lỗi ({elapsed:.1f}s) — {e}\n"
            f"  raw content: {content[:200] if content else 'EMPTY'}"
        )
        return None

    vlm_log.info(
        f"q{q_number}: OK ({elapsed:.1f}s) img={img_w}x{img_h} "
        f"type={result.question_type.value} n_ans={result.n_answers} "
        f"has_fig={result.has_figure} has_formula={result.has_formula} "
        f"complete={result.content_complete} fig_complete={result.figure_complete}"
    )

    return result


# ============================================================
# Sync wrapper
# ============================================================

def analyze_question(
    image_path: Path,
    q_number: int,
    q_type: str = "unknown",
    n_ans: int = 0,
    out_dir: Optional[Path] = None,
) -> Optional[VLMQuestionResult]:
    """Sync wrapper cho analyze_question_async.

    Tạo event loop mới nếu chưa có (safe cho CLI context).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Đang trong async context → không thể dùng asyncio.run
        # Tạo task trong loop hiện tại
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(
                asyncio.run,
                analyze_question_async(image_path, q_number, q_type, n_ans, out_dir)
            )
            return future.result()
    else:
        return asyncio.run(
            analyze_question_async(image_path, q_number, q_type, n_ans, out_dir)
        )
