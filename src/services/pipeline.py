"""Main pipeline orchestrator — Paddle + Snake → Crop → MinIO → Mongo.

Hai chế độ:
  * CLI (scripts/parse_cli.py): giữ nguyên, ghi mọi file ra ./output/{exam_id} để debug.
  * API (AI service): gọi `ExamPipeline(...).run()` — KHÔNG giữ file local, cắt ảnh +
    overlay vào thư mục tạm rồi đẩy hết lên MinIO, lưu lịch sử Mongo, cuối cùng XÓA
    thư mục tạm. Lỗi ở stage nào → raise PipelineStageError(mã lỗi của stage đó).

API dùng:
    from src.services.pipeline import ExamPipeline
    exam = ExamPipeline().run("de.pdf", exam_id="abc123")   # raise PipelineStageError nếu lỗi
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Optional
from uuid import uuid4

from src.core.config import settings
from src.core.errors import ErrorCodes, PipelineStageError
from src.core.logging import logger
from src.schemas.exam import Exam


class ExamPipeline:
    """Chạy full pipeline 1 đề thi và trả về Exam (kèm crop/overlay đã upload MinIO)."""

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

    # ============================================================
    # API entry — không giữ file local, raise PipelineStageError theo stage
    # ============================================================
    def run(
        self,
        input_path: str | Path,
        exam_id: Optional[str] = None,
    ) -> Exam:
        """Chạy pipeline cho AI service.

        - Cắt ảnh + overlay vào thư mục TẠM, upload hết lên MinIO (raw + crops +
          overlay + exam.json), lưu lịch sử Mongo, rồi XÓA thư mục tạm.
        - Lỗi ở stage nào → raise PipelineStageError(spec của stage đó).

        Trả về Exam (đã set minio_key/url cho mọi asset).
        """
        exam_id = exam_id or str(uuid4())[:8]
        tmp_root = Path(settings.temp_dir)
        tmp_root.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=f"{exam_id}_", dir=str(tmp_root)))
        logger.info(f"[Pipeline.run] {input_path} (exam_id={exam_id}) work={work}")
        try:
            exam = self._run_stages(
                input_path, exam_id, work,
                cleanup_local=True, raise_on_stage_error=True,
            )
            return exam
        finally:
            shutil.rmtree(work, ignore_errors=True)

    # ============================================================
    # CLI-compatible entry — giữ file local (debug). Không raise theo stage.
    # ============================================================
    def parse(
        self,
        input_path: str | Path,
        exam_id: Optional[str] = None,
        out_dir: Optional[str | Path] = None,
    ) -> Exam:
        """Chạy pipeline, GIỮ file ở ./output/{exam_id} (hành vi cũ, cho debug)."""
        exam_id = exam_id or str(uuid4())[:8]
        out = Path(out_dir) if out_dir else Path(settings.local_output_dir) / exam_id
        out.mkdir(parents=True, exist_ok=True)
        return self._run_stages(
            input_path, exam_id, out,
            cleanup_local=False, raise_on_stage_error=False,
        )

    def parse_to_json(self, input_path: str | Path, **kwargs) -> str:
        """Như parse() nhưng trả thẳng chuỗi JSON."""
        return self.parse(input_path, **kwargs).model_dump_json(indent=2)

    # ============================================================
    # Lõi dùng chung cho cả 2 chế độ
    # ============================================================
    def _run_stages(
        self,
        input_path: str | Path,
        exam_id: str,
        out: Path,
        cleanup_local: bool,
        raise_on_stage_error: bool,
    ) -> Exam:
        from src.schemas.exam import Exam

        input_path = str(input_path)
        source_file = Path(input_path).name

        def stage(spec, fn):
            """Chạy 1 stage; bọc lỗi thành PipelineStageError nếu bật raise."""
            try:
                return fn()
            except PipelineStageError:
                raise
            except Exception as e:
                logger.error(f"[Pipeline] stage {spec.stage} lỗi — {e}")
                if raise_on_stage_error:
                    raise PipelineStageError(spec, detail=str(e), exam_id=exam_id) from e
                return None

        # 1. Preprocess
        from src.services.preprocess import preprocess
        images = stage(ErrorCodes.PREPROCESS,
                       lambda: preprocess(input_path, dpi=self.dpi, do_deskew=self.do_deskew))

        # 2. PaddleOCR
        from src.services.paddle_parser import PaddleParser
        blocks_per_page = stage(ErrorCodes.OCR,
                                lambda: PaddleParser().parse_pages(images))

        # 3. Anchor
        from src.services.anchor_extractor import extract_anchors
        anchors = stage(ErrorCodes.ANCHOR, lambda: extract_anchors(blocks_per_page))

        # 4. Snake Walker
        from src.services.snake_walker import snake_walk, parse_exam_metadata
        page_heights = [float(im.height) for im in images]
        page_widths = [float(im.width) for im in images]
        sw = stage(ErrorCodes.SNAKE, lambda: snake_walk(
            blocks_per_page, anchors, page_heights, page_widths))
        questions, groups, layouts, group_layouts = sw

        # 5. Classifier
        from src.services.question_classifier import classify_all
        stage(ErrorCodes.CLASSIFY, lambda: classify_all(questions, groups))

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

        # 6. Cropper + overlay
        if self.do_crop:
            from src.services.cropper import crop_all
            stage(ErrorCodes.CROP, lambda: crop_all(exam, layouts, group_layouts, images, out))

            # (tuỳ chọn) VLM Verify — mặc định TẮT
            if self.do_vlm:
                try:
                    from src.services.vlm_verifier import verify_exam
                    verify_exam(exam, layouts, group_layouts, images, out)
                except Exception as e:
                    logger.error(f"[Pipeline] VLM verify lỗi — {e}")

            # 7. MinIO upload — raw + overlay + crops + exam.json
            raw_info = None
            if self.do_upload:
                def _upload():
                    nonlocal raw_info
                    from src.services.minio_client import MinIOService
                    from src.services.uploader import (
                        upload_exam_assets, upload_overlay, upload_raw_file,
                    )
                    svc = MinIOService()
                    if settings.minio_save_raw:
                        raw_info = upload_raw_file(input_path, exam, exam_id=exam_id, svc=svc)
                    upload_overlay(exam, out, exam_id=exam_id, svc=svc)
                    upload_exam_assets(exam, out, exam_id=exam_id, svc=svc)
                stage(ErrorCodes.MINIO, _upload)

            # 8. Lưu lịch sử MongoDB
            if self.do_mongo:
                def _mongo():
                    from src.services.mongo_client import MongoService
                    MongoService().save_exam(exam, raw=raw_info)
                stage(ErrorCodes.MONGO, _mongo)

        # Ghi exam.json (local — CLI debug giữ lại; API sẽ xóa cùng thư mục tạm)
        if not cleanup_local:
            (out / "exam.json").write_text(exam.model_dump_json(indent=2), encoding="utf-8")
        logger.info(f"[Pipeline] xong: {len(exam.questions)} câu, {len(exam.groups)} nhóm")
        return exam
