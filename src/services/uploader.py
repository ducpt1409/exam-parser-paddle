"""Stage 8 (Phase 4): Upload assets của Exam lên MinIO + set minio_key/url.

Duyệt mọi CroppedImage trong exam (full/content/đáp án/group header/passage),
upload file PNG local lên MinIO, rồi GHI ĐÈ `minio_key` + `url` (presigned) vào object.
Cuối cùng upload luôn exam.json.

Quy ước key: "{prefix}{exam_id}/crops/qN_full.png" (giữ nguyên rel-path local).

Fail-safe: lỗi 1 ảnh → log + bỏ qua ảnh đó (set needs_review câu liên quan), không crash.

Usage:
    from src.services.uploader import upload_exam_assets
    n = upload_exam_assets(exam, out_dir)        # tự tạo MinIOService
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.core.config import settings
from src.core.logging import logger
from src.schemas.exam import CroppedImage, Exam
from src.services.minio_client import MinIOService


def _upload_one(
    ci: Optional[CroppedImage],
    out_dir: Path,
    exam_id: str,
    svc: MinIOService,
    presign: bool,
    seen: set[int],
) -> int:
    """Upload 1 CroppedImage. Trả 1 nếu upload, 0 nếu bỏ qua.

    - `seen`: id() các object đã xử lý (tránh upload trùng khi passage_image is header_image).
    - rel-path local nằm sẵn ở ci.url (do cropper set, vd "crops/q1_full.png").
    """
    if ci is None:
        return 0
    if id(ci) in seen:
        return 0
    seen.add(id(ci))

    # Đã upload rồi (idempotent) → bỏ qua
    if ci.minio_key:
        return 0

    rel = ci.url  # cropper gán rel-path local vào url
    if not rel or rel.startswith("http"):
        return 0

    local = out_dir / rel
    if not local.exists():
        logger.warning(f"Upload: thiếu file local {local} → bỏ qua")
        return 0

    prefix = settings.minio_prefix or ""
    key = f"{prefix}{exam_id}/{rel}"
    try:
        svc.upload_file(local, key, content_type="image/png")
        ci.minio_key = key
        ci.url = svc.presigned_url(key) if presign else svc.public_url(key)
        return 1
    except Exception as e:
        logger.error(f"Upload lỗi {key} — {e}")
        return 0


def upload_raw_file(
    input_path: str | Path,
    exam: Exam,
    exam_id: Optional[str] = None,
    svc: Optional[MinIOService] = None,
    presign: bool = True,
) -> Optional[dict]:
    """Upload file GỐC (PDF) lên MinIO; set source_minio_key/url in-place vào exam.

    Returns: dict {filename, minio_key, url, size_bytes, content_type} hoặc None nếu lỗi.
    Key: "{prefix}{exam_id}/raw/{filename}".
    """
    exam_id = exam_id or exam.exam_id
    if svc is None:
        svc = MinIOService()

    p = Path(input_path)
    if not p.exists():
        logger.warning(f"Upload raw: thiếu file {p}")
        return None

    ext = p.suffix.lower()
    content_type = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    }.get(ext, "application/octet-stream")

    prefix = settings.minio_prefix or ""
    key = f"{prefix}{exam_id}/raw/{p.name}"
    try:
        svc.upload_file(p, key, content_type=content_type)
        url = svc.presigned_url(key) if presign else svc.public_url(key)
        exam.source_minio_key = key
        exam.source_url = url
        info = {
            "filename": p.name,
            "minio_key": key,
            "url": url,
            "size_bytes": p.stat().st_size,
            "content_type": content_type,
        }
        logger.info(f"MinIO: upload file gốc {p.name} → {key}")
        return info
    except Exception as e:
        logger.error(f"Upload raw lỗi {key} — {e}")
        return None


def upload_exam_assets(
    exam: Exam,
    out_dir: Path,
    exam_id: Optional[str] = None,
    svc: Optional[MinIOService] = None,
    presign: bool = True,
    upload_exam_json: bool = True,
) -> int:
    """Upload toàn bộ crop của exam + exam.json lên MinIO; set minio_key/url in-place.

    Returns: số ảnh đã upload.
    """
    exam_id = exam_id or exam.exam_id
    if svc is None:
        svc = MinIOService()

    seen: set[int] = set()
    n = 0

    for q in exam.questions:
        n += _upload_one(q.full_image, out_dir, exam_id, svc, presign, seen)
        n += _upload_one(q.content_image, out_dir, exam_id, svc, presign, seen)
        for a in q.answers:
            n += _upload_one(a.image, out_dir, exam_id, svc, presign, seen)

    for g in exam.groups:
        n += _upload_one(g.header_image, out_dir, exam_id, svc, presign, seen)
        n += _upload_one(g.passage_image, out_dir, exam_id, svc, presign, seen)

    # Upload exam.json (sau khi url đã được set → JSON chứa link MinIO)
    if upload_exam_json:
        prefix = settings.minio_prefix or ""
        try:
            svc.upload_json(
                exam.model_dump_json(indent=2), f"{prefix}{exam_id}/exam.json"
            )
        except Exception as e:
            logger.error(f"Upload exam.json lỗi — {e}")

    logger.info(f"MinIO: đã upload {n} ảnh + exam.json cho exam {exam_id}")
    return n
