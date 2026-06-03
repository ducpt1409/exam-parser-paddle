"""Main pipeline orchestrator - kết nối tất cả stages.

TODO: implement progressively theo phases:
- Phase 1: preprocess → paddle_parser → anchor_extractor
- Phase 2: + snake_walker + question_classifier + cropper
- Phase 3: + vlm_client (verification)
- Phase 4: + minio_client (upload)

API:
    from src.services.pipeline import ExamPipeline
    pipeline = ExamPipeline()
    result = pipeline.parse(pdf_path) → Exam
"""
# Placeholder - sẽ implement progressively
