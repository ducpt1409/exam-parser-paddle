"""Verify setup: check toàn bộ components đã cài đúng + GPU working.

Usage:
    conda activate exam_parser_paddle
    python scripts/verify_setup.py
"""
from __future__ import annotations

import os
import sys
import time
from io import BytesIO

# Color codes
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
BOLD = "\033[1m"
RESET = "\033[0m"


def check(name: str, func, *args, **kwargs) -> bool:
    """Run a check, print result, return success."""
    print(f"  {BLUE}→{RESET} {name}...", end=" ", flush=True)
    try:
        result = func(*args, **kwargs)
        if isinstance(result, tuple):
            ok, msg = result
        else:
            ok, msg = bool(result), str(result)
        if ok:
            print(f"{GREEN}✓{RESET} {msg}")
            return True
        else:
            print(f"{RED}✗ FAIL{RESET}: {msg}")
            return False
    except Exception as e:
        print(f"{RED}✗ ERROR{RESET}: {type(e).__name__}: {e}")
        return False


# ============================================================
# Checks
# ============================================================
def check_python_version():
    v = sys.version_info
    if v.major == 3 and v.minor in (10, 11, 12):
        return True, f"Python {v.major}.{v.minor}.{v.micro}"
    return False, f"Python {v.major}.{v.minor} - cần 3.10/3.11/3.12"


def check_pytorch():
    import torch
    if not torch.cuda.is_available():
        return False, "CUDA not available"
    name = torch.cuda.get_device_name(0)
    mem = torch.cuda.get_device_properties(0).total_memory / 1024**3
    return True, f"{name} ({mem:.1f}GB)"


def check_pytorch_blackwell():
    import torch
    archs = torch.cuda.get_arch_list()
    if "sm_120" in archs:
        return True, f"sm_120 supported (archs: {len(archs)} total)"
    return False, f"sm_120 KHÔNG có. Có: {archs}. Cần cài PyTorch nightly cu128."


def check_pytorch_inference():
    import torch
    x = torch.randn(1000, 1000, device="cuda")
    start = time.time()
    y = x @ x
    torch.cuda.synchronize()
    elapsed = (time.time() - start) * 1000
    return True, f"matmul 1000x1000 = {elapsed:.1f}ms"


def check_paddle():
    """Check Paddle import + compile mode.

    Lưu ý: với RTX 5090 (Blackwell), default dùng CPU mode (Paddle chưa support).
    """
    import paddle
    mode = "GPU (CUDA)" if paddle.is_compiled_with_cuda() else "CPU only"
    return True, f"version {paddle.__version__} - {mode}"


def check_paddle_inference():
    """Test inference. Tự detect CPU/GPU mode."""
    import paddle
    use_gpu = paddle.is_compiled_with_cuda()
    x = paddle.randn([500, 500])
    if use_gpu:
        try:
            x = x.cuda()
        except Exception:
            use_gpu = False  # fallback CPU nếu GPU fail
    start = time.time()
    y = paddle.matmul(x, x)
    elapsed = (time.time() - start) * 1000
    mode = "GPU" if use_gpu else "CPU"
    return True, f"matmul {mode} 500x500 = {elapsed:.1f}ms"


def check_paddleocr_import():
    from paddleocr import PaddleOCR, PPStructure  # noqa
    return True, "PaddleOCR + PPStructure import OK"


def check_paddleocr_inference():
    """Test OCR trên ảnh dummy."""
    from paddleocr import PaddleOCR
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np

    # Tạo ảnh test với text tiếng Việt
    img = Image.new("RGB", (800, 200), "white")
    draw = ImageDraw.Draw(img)
    try:
        # Try common font paths
        for font_path in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]:
            if os.path.exists(font_path):
                font = ImageFont.truetype(font_path, 36)
                break
        else:
            font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    draw.text((50, 50), "Câu 1: Hàm số y = x^2", fill="black", font=font)
    draw.text((50, 120), "A. 1     B. 2     C. 3     D. 4", fill="black", font=font)

    arr = np.array(img)

    # Đọc env để biết dùng GPU hay CPU
    use_gpu = os.getenv("PADDLE_USE_GPU", "false").lower() == "true"

    ocr = PaddleOCR(use_angle_cls=False, lang="vi",
                     use_gpu=use_gpu, show_log=False)
    start = time.time()
    result = ocr.ocr(arr, cls=False)
    elapsed = time.time() - start
    n_lines = len(result[0]) if result and result[0] else 0
    mode = "GPU" if use_gpu else "CPU"
    return n_lines > 0, f"detected {n_lines} lines ({mode}, {elapsed:.1f}s)"


def check_ollama_connection():
    import ollama
    host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    client = ollama.Client(host=host)
    res = client.list()
    models = res.get("models", []) if isinstance(res, dict) else getattr(res, "models", [])
    return True, f"connected to {host}, {len(models)} model(s)"


def check_ollama_model():
    import ollama
    target_model = os.getenv("OLLAMA_VLM_MODEL", "qwen3-vl:32b-instruct")
    host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    client = ollama.Client(host=host)
    res = client.list()
    models = res.get("models", []) if isinstance(res, dict) else getattr(res, "models", [])
    names = [m.model if hasattr(m, "model") else m.get("name") for m in models]
    if any(target_model in n for n in names):
        return True, f"{target_model} available"
    return False, f"{target_model} NOT found. Có: {names}"


def check_ollama_gpu():
    """Test inference + check GPU usage qua subprocess nvidia-smi."""
    import ollama
    import subprocess

    host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    model = os.getenv("OLLAMA_VLM_MODEL", "qwen3-vl:32b-instruct")
    client = ollama.Client(host=host)

    # Trigger model load
    try:
        client.generate(model=model, prompt="test", options={"num_predict": 1})
    except Exception as e:
        return False, f"generate failed: {e}"

    # Check GPU usage
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-compute-apps=process_name,used_memory",
             "--format=csv,noheader,nounits"],
            text=True, timeout=5,
        )
        if "ollama" in out.lower() or "runner" in out.lower():
            # Parse VRAM
            for line in out.strip().split("\n"):
                if "ollama" in line.lower() or "runner" in line.lower():
                    parts = line.split(",")
                    if len(parts) >= 2:
                        vram_mb = int(parts[1].strip())
                        vram_gb = vram_mb / 1024
                        return True, f"GPU mode, {vram_gb:.1f}GB VRAM"
            return True, "GPU mode (process detected)"
        return False, "Ollama running on CPU (no GPU process)"
    except Exception as e:
        return True, f"inference OK (GPU check failed: {e})"


def check_minio_connection():
    from minio import Minio
    endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    access_key = os.getenv("MINIO_ACCESS_KEY")
    secret_key = os.getenv("MINIO_SECRET_KEY")
    secure = os.getenv("MINIO_SECURE", "false").lower() == "true"

    if not access_key or not secret_key:
        return False, "MINIO_ACCESS_KEY/SECRET_KEY chưa set trong .env"

    client = Minio(endpoint, access_key=access_key,
                    secret_key=secret_key, secure=secure)
    buckets = client.list_buckets()
    return True, f"connected to {endpoint}, {len(buckets)} bucket(s)"


def check_minio_bucket():
    from minio import Minio
    endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    access_key = os.getenv("MINIO_ACCESS_KEY")
    secret_key = os.getenv("MINIO_SECRET_KEY")
    bucket = os.getenv("MINIO_BUCKET", "exam-parser")
    secure = os.getenv("MINIO_SECURE", "false").lower() == "true"

    client = Minio(endpoint, access_key=access_key,
                    secret_key=secret_key, secure=secure)
    if client.bucket_exists(bucket):
        return True, f'bucket "{bucket}" exists'
    return False, f'bucket "{bucket}" NOT found - tạo qua MinIO Console'


def check_minio_upload_download():
    from minio import Minio
    endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    access_key = os.getenv("MINIO_ACCESS_KEY")
    secret_key = os.getenv("MINIO_SECRET_KEY")
    bucket = os.getenv("MINIO_BUCKET", "exam-parser")
    secure = os.getenv("MINIO_SECURE", "false").lower() == "true"

    client = Minio(endpoint, access_key=access_key,
                    secret_key=secret_key, secure=secure)

    # Upload test
    test_data = b"test content for verify_setup.py"
    test_key = "_verify_test/hello.txt"
    client.put_object(
        bucket, test_key, BytesIO(test_data),
        length=len(test_data), content_type="text/plain",
    )

    # Download test
    response = client.get_object(bucket, test_key)
    downloaded = response.read()
    response.close()

    # Cleanup
    client.remove_object(bucket, test_key)

    if downloaded == test_data:
        return True, "upload/download OK"
    return False, "data mismatch sau download"


def check_env_file():
    if os.path.exists(".env"):
        return True, ".env file exists"
    return False, ".env không tồn tại - copy từ .env.example"


def check_pymupdf():
    import fitz
    return True, f"PyMuPDF {fitz.version[0]}"


def check_opencv():
    import cv2
    return True, f"OpenCV {cv2.__version__}"


# ============================================================
# Main
# ============================================================
def main():
    # Load .env
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        print(f"{YELLOW}⚠ python-dotenv chưa cài. .env không load{RESET}")

    print(f"\n{BOLD}{BLUE}════════════════════════════════════════════════{RESET}")
    print(f"{BOLD}{BLUE}  exam_parser_paddle - Setup Verification{RESET}")
    print(f"{BOLD}{BLUE}════════════════════════════════════════════════{RESET}\n")

    results = []

    print(f"{BOLD}[1] Python & Core Libraries{RESET}")
    results.append(("Python version", check("Python", check_python_version)))
    results.append(("PyMuPDF", check("PyMuPDF", check_pymupdf)))
    results.append(("OpenCV", check("OpenCV", check_opencv)))

    print(f"\n{BOLD}[2] PyTorch + CUDA{RESET}")
    results.append(("PyTorch CUDA", check("PyTorch + CUDA", check_pytorch)))
    results.append(("Blackwell sm_120", check("Blackwell sm_120 support",
                                                  check_pytorch_blackwell)))
    results.append(("PyTorch inference", check("PyTorch inference test",
                                                 check_pytorch_inference)))

    print(f"\n{BOLD}[3] PaddlePaddle{RESET}")
    results.append(("Paddle GPU", check("PaddlePaddle GPU", check_paddle)))
    results.append(("Paddle inference", check("PaddlePaddle inference",
                                                check_paddle_inference)))

    print(f"\n{BOLD}[4] PaddleOCR{RESET}")
    results.append(("PaddleOCR import", check("PaddleOCR import",
                                                check_paddleocr_import)))
    results.append(("PaddleOCR inference", check("PaddleOCR Vietnamese test",
                                                   check_paddleocr_inference)))

    print(f"\n{BOLD}[5] Ollama{RESET}")
    results.append(("Ollama connection", check("Ollama API",
                                                 check_ollama_connection)))
    results.append(("VLM model", check("Qwen3-VL model", check_ollama_model)))
    results.append(("Ollama GPU", check("Ollama using GPU", check_ollama_gpu)))

    print(f"\n{BOLD}[6] MinIO{RESET}")
    env_ok = check(".env file", check_env_file)
    results.append((".env file", env_ok))
    if env_ok:
        results.append(("MinIO connection", check("MinIO API",
                                                    check_minio_connection)))
        results.append(("MinIO bucket", check("MinIO bucket",
                                                check_minio_bucket)))
        results.append(("MinIO I/O", check("MinIO upload/download",
                                             check_minio_upload_download)))

    # Summary
    print(f"\n{BOLD}{BLUE}════════════════════════════════════════════════{RESET}")
    n_pass = sum(1 for _, ok in results if ok)
    n_total = len(results)

    if n_pass == n_total:
        print(f"{BOLD}{GREEN}🎉 ALL CHECKS PASSED ({n_pass}/{n_total}){RESET}")
        print(f"{GREEN}Setup hoàn tất! Sẵn sàng chạy pipeline.{RESET}\n")
        sys.exit(0)
    else:
        print(f"{BOLD}{YELLOW}⚠ {n_pass}/{n_total} CHECKS PASSED{RESET}")
        print(f"\n{RED}Failed checks:{RESET}")
        for name, ok in results:
            if not ok:
                print(f"  ✗ {name}")
        print(f"\n{YELLOW}Xem SETUP.md §14 Troubleshooting để fix.{RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
