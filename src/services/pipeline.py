"""Main pipeline orchestrator (Phase 4/5) — Paddle + Snake → Crop → MinIO → JSON.

Bản đồ hoá toàn bộ stage thành 1 entry lập trình (dùng cho API / batch). VLM (Phase 3)
TẮT mặc định; bật lại bằng settings.use_vlm_verification nếu cần sau này.

Luồng:
    Preprocess → PaddleOCR → Anchor → Snake Walker → Classifier → Cropper
              → (VLM nếu bật) → MinIO upload → Exam (JSON)

API:
    from src.services.pipeline import ExamPipeline
    pipe = ExamPipeline()
    exam = pipe.parse("input/de.pdf")          # Exam object (đã set minio url)
    js   = pipe.parse_to_json("input/de.pdf")  # chuỗi JSON cấu trúc đã cắt
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional
from uuid import uuid4

from src.core.config import settings
from src.core.logging import logger
from src.schemas.exam import Exam


class ExamPipeline:
    """Chạy full pipeline 1 đề thi và trả về Exam (kèm crop đã upload MinIO)."""

    def __init__(
        self,
        dpi: Optional[int] = None,
        do_deskew: bool = True,
        do_crop: bool = True,
        do_upload: Optional[bool] = None,
        do_vlm: Optional[bool] = None,
        do_mongo: Optional[bool] = None,
    ):
        self.dpi = dpi or settings.default_dpi
        self.do_deskew = do_deskew
        self.do_crop = do_crop
        self.do_upload = settings.use_minio_upload if do_upload is None else do_upload
        self.do_vlm = settings.use_vlm_verification if do_vlm is None else do_vlm
        self.do_mongo = settings.use_mongo if do_mongo is None else do_mongo

    # ------------------------------------------------------------
    def parse(
        self,
        input_path: str | Path,
        exam_id: Optional[str] = None,
        out_dir: Optional[str | Path] = None,
    ) -> Exam:
        """Chạy pipeline, trả về Exam đã điền crop + (tuỳ chọn) link MinIO."""
        # Import nội bộ để tránh nạp Paddle khi chỉ cần class
        from src.services.preprocess import preprocess
        from src.services.paddle_parser import PaddleParser
        from src.services.anchor_extractor import extract_anchors
        from src.services.snake_walker import snake_walk, parse_exam_metadata
        from src.services.question_classifier import classify_all
        from src.schemas.exam import Exam, ExamMetadata  # noqa: F401

        input_path = str(input_path)
        exam_id = exam_id or str(uuid4())[:8]
        out = Path(out_dir) if out_dir else Path(settings.local_output_dir) / exam_id
        out.mkdir(parents=True, exist_ok=True)
        source_file = Path(input_path).name
        logger.info(f"[Pipeline] {input_path} → {out} (exam_id={exam_id})")

        # 1. Preprocess
        images = preprocess(input_path, dpi=self.dpi, do_deskew=self.do_deskew)

        # 2. PaddleOCR
        blocks_per_page = PaddleParser().parse_pages(images)

        # 3. Anchor
        anchors = extract_anchors(blocks_per_page)

        # 4. Snake Walker
        page_heights = [float(im.height) for im in images]
        page_widths = [float(im.width) for im in images]
        questions, groups, layouts, group_layouts = snake_walk(
            blocks_per_page, anchors, page_heights, page_widths
        )

        # 5. Classifier
        classify_all(questions, groups)

        # Build Exam
        metadata = parse_exam_metadata(anchors, blocks_per_page, len(questions))
        exam = Exam(
            exam_id=exam_id,
            source_file=source_file,
            n_pages=len(images),
            metadata=metadata,
            groups=groups,
            questions=questions,
            n_questions=len(questions),
            n_groups=len(groups),
            n_essay=sum(1 for q in questions if q.type.value in (
                "tu_luan_dai", "tu_luan_ngan", "dien_dap_an")),
            n_mcq=sum(1 for q in questions if q.type.value in (
                "trac_nghiem_1_dap_an", "trac_nghiem_nhieu_dap_an", "doc_hieu", "dung_sai")),
            avg_confidence=(sum(q.confidence for q in questions) / len(questions)
                            if questions else 0.0),
        )

        # 6. Cropper
        if self.do_crop:
            from src.services.cropper import crop_all
            crop_all(exam, layouts, group_layouts, images, out)

            # (tuỳ chọn) 7. VLM Verify — mặc định TẮT
            if self.do_vlm:
                try:
                    from src.services.vlm_verifier import verify_exam
                    verify_exam(exam, layouts, group_layouts, images, out)
                except Exception as e:
                    logger.error(f"[Pipeline] VLM verify lỗi — {e}")

            # 8. MinIO upload (Phase 4) — file gốc + crop + exam.json
            raw_info = None
            if self.do_upload:
                try:
                    from src.services.minio_client import MinIOService
                    from src.services.uploader import upload_exam_assets, upload_raw_file
                    svc = MinIOService()
                    if settings.minio_save_raw:
                        raw_info = upload_raw_file(input_path, exam, exam_id=exam_id, svc=svc)
                    upload_exam_assets(exam, out, exam_id=exam_id, svc=svc)
                except Exception as e:
                    logger.error(f"[Pipeline] MinIO upload lỗi — {e} (giữ url local)")

            # 9. Lưu lịch sử MongoDB (1 đề = 1 bản ghi)
            if self.do_mongo:
                try:
                    from src.services.mongo_client import MongoService
                    MongoService().save_exam(exam, raw=raw_info)
                except Exception as e:
                    logger.error(f"[Pipeline] Mongo save lỗi — {e}")

        # Ghi exam.json (cấu trúc cuối)
        (out / "exam.json").write_text(exam.model_dump_json(indent=2), encoding="utf-8")
        logger.info(f"[Pipeline] xong: {len(exam.questions)} câu, {len(exam.groups)} nhóm")
        return exam

    # ------------------------------------------------------------
    def parse_to_json(self, input_path: str | Path, **kwargs) -> str:
        """Như parse() nhưng trả thẳng chuỗi JSON (cấu trúc đã cắt + link MinIO)."""
        exam = self.parse(input_path, **kwargs)
        return exam.model_dump_json(indent=2)
