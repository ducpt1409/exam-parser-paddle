"""CLI Phase 1+2+4+5: Preprocess + PaddleOCR + Anchor + Snake Walker + Classifier + Cropper + MinIO + JSON.

VLM (Phase 3) TẮT mặc định. Luồng: Paddle + Snake → cắt hình → upload MinIO → exam.json.

Usage:
    python scripts/parse_cli.py input/de.pdf
    python scripts/parse_cli.py input/de.pdf --dpi 400
    python scripts/parse_cli.py input/de.pdf --save-images   # lưu rendered pages
    python scripts/parse_cli.py input/de.pdf --no-crop       # chỉ tới classify (debug)
    python scripts/parse_cli.py input/de.pdf --no-upload     # cắt local, KHÔNG upload MinIO

Output (vào output/{exam_id}/):
    blocks.json    - PaddleOCR output
    anchors.json   - extracted anchors
    exam.json      - Exam structure CUỐI (Phase 5: chứa minio_key + presigned url)
    summary.txt    - tóm tắt
    crops/         - ảnh từng câu/đáp án/passage (Phase 2)
    overlay/       - page_XX.png có bbox màu (Phase 2)
    parse.log      - full pipeline log
    → MinIO: {bucket}/{exam_id}/crops/*.png + {exam_id}/exam.json (Phase 4)
    pages/*.png    - rendered pages (nếu --save-images)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from uuid import uuid4

import click

# Make src importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.config import settings  # noqa: E402
from src.core.logging import logger  # noqa: E402


@click.command()
@click.argument("input_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--output-dir", default=None, help="Output dir (default: ./output/{exam_id}/)")
@click.option("--dpi", default=None, type=int, help="Render DPI (default từ .env)")
@click.option("--no-deskew", is_flag=True, default=False, help="Tắt deskew")
@click.option("--save-images", is_flag=True, default=False, help="Lưu rendered pages")
@click.option("--no-crop", is_flag=True, default=False, help="Chỉ chạy tới classify (bỏ crop+overlay+upload)")
@click.option("--no-vlm", is_flag=True, default=False, help="(deprecated) VLM đã tắt mặc định Phase 4")
@click.option("--no-upload", is_flag=True, default=False, help="Bỏ upload MinIO (chỉ lưu crop local)")
@click.option("--debug", is_flag=True, default=False, help="Verbose logging")
def main(input_path, output_dir, dpi, no_deskew, save_images, no_crop, no_vlm, no_upload, debug):
    """Full pipeline: Preprocess → PaddleOCR → Anchor → Snake Walker → Classifier → Cropper → VLM Verify."""
    if debug:
        import logging
        logger.remove()
        logger.add(sys.stderr, level="DEBUG")

    # Setup output dir
    exam_id = str(uuid4())[:8]
    out = Path(output_dir) if output_dir else Path("output") / exam_id
    out.mkdir(parents=True, exist_ok=True)
    click.echo(f"📂 Output: {out}")

    # Thêm parse.log
    logger.add(str(out / "parse.log"), level="DEBUG",
               format="{time:HH:mm:ss} | {level} | {message}")

    dpi = dpi or settings.default_dpi
    source_file = Path(input_path).name
    total_stages = 8

    # ============================================================
    # Stage 1: Preprocess
    # ============================================================
    click.echo(f"\n[1/{total_stages}] Preprocess (DPI={dpi}, deskew={not no_deskew})...")
    from src.services.preprocess import preprocess
    t0 = time.time()
    images = preprocess(input_path, dpi=dpi, do_deskew=not no_deskew)
    click.echo(f"   ✓ {len(images)} pages ({time.time() - t0:.1f}s)")

    if save_images:
        pages_dir = out / "pages"
        pages_dir.mkdir(exist_ok=True)
        for i, img in enumerate(images):
            img.save(pages_dir / f"page_{i:02d}.png")
        click.echo(f"   ✓ Saved rendered pages to {pages_dir}/")

    # ============================================================
    # Stage 2: PaddleOCR
    # ============================================================
    click.echo(f"\n[2/{total_stages}] PaddleOCR PP-StructureV3 "
                f"(use_gpu={settings.paddle_use_gpu})...")
    from src.services.paddle_parser import PaddleParser
    parser = PaddleParser()
    t0 = time.time()
    blocks_per_page = parser.parse_pages(images)
    click.echo(f"   ✓ Parsed {len(images)} pages in {time.time() - t0:.1f}s "
                f"({(time.time() - t0) / len(images):.1f}s/page)")

    # Stats
    total_blocks = sum(len(b) for b in blocks_per_page)
    total_lines = sum(len(block.lines) for blocks in blocks_per_page for block in blocks)
    click.echo(f"   ✓ {total_blocks} blocks, {total_lines} text lines")

    # Save blocks.json
    blocks_data = []
    for page_blocks in blocks_per_page:
        blocks_data.append([
            {
                "page_index": b.page_index,
                "block_index": b.block_index,
                "type": b.type.value,
                "bbox": b.bbox,
                "confidence": b.confidence,
                "lines": [
                    {"text": l.text, "bbox": l.bbox, "confidence": l.confidence}
                    for l in b.lines
                ],
                "extra": b.extra,
            }
            for b in page_blocks
        ])
    (out / "blocks.json").write_text(
        json.dumps(blocks_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    click.echo(f"   ✓ Saved blocks.json")

    # ============================================================
    # Stage 3: Anchor Extraction
    # ============================================================
    click.echo(f"\n[3/{total_stages}] Anchor Extraction...")
    from src.services.anchor_extractor import extract_anchors
    t0 = time.time()
    anchors = extract_anchors(blocks_per_page)
    click.echo(f"   ✓ {len(anchors)} anchors ({time.time() - t0:.2f}s)")

    # Save anchors.json
    anchors_data = [
        {
            "page_index": a.page_index,
            "type": a.type.value,
            "value": a.value,
            "text": a.text,
            "bbox": a.bbox,
            "confidence": a.confidence,
            "source": a.source,
        }
        for a in anchors
    ]
    (out / "anchors.json").write_text(
        json.dumps(anchors_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    click.echo(f"   ✓ Saved anchors.json")

    # ============================================================
    # Stage 4: Snake Walker (Phase 2)
    # ============================================================
    click.echo(f"\n[4/{total_stages}] Snake Walker...")
    from src.services.snake_walker import snake_walk, parse_exam_metadata
    t0 = time.time()

    page_heights = [float(img.height) for img in images]
    page_widths = [float(img.width) for img in images]

    questions, groups, layouts, group_layouts = snake_walk(
        blocks_per_page, anchors, page_heights, page_widths
    )
    click.echo(f"   ✓ {len(questions)} questions, {len(groups)} groups ({time.time() - t0:.2f}s)")

    # Liệt kê chi tiết
    for q in questions:
        n_ans = len(q.answers)
        pages = q.page_indices
        click.echo(f"     Câu {q.number}: {n_ans} đáp án, pages={pages}")

    # ============================================================
    # Stage 5: Classifier (Phase 2)
    # ============================================================
    click.echo(f"\n[5/{total_stages}] Question Classifier...")
    from src.services.question_classifier import classify_all
    t0 = time.time()
    classify_all(questions, groups)
    click.echo(f"   ✓ Classified ({time.time() - t0:.2f}s)")

    from collections import Counter
    type_counter = Counter(q.type.value for q in questions)
    for t_name, n in type_counter.most_common():
        click.echo(f"     {t_name}: {n}")

    # ============================================================
    # Build Exam object
    # ============================================================
    from src.schemas.exam import Exam, ExamMetadata

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
            "tu_luan_dai", "tu_luan_ngan", "dien_dap_an"
        )),
        n_mcq=sum(1 for q in questions if q.type.value in (
            "trac_nghiem_1_dap_an", "trac_nghiem_nhieu_dap_an",
            "doc_hieu", "dung_sai"
        )),
        avg_confidence=(
            sum(q.confidence for q in questions) / len(questions)
            if questions else 0.0
        ),
    )

    # ============================================================
    # Stage 6: Cropper (Phase 2)
    # ============================================================
    n_crops = 0
    n_overlay = 0
    if not no_crop:
        click.echo(f"\n[6/{total_stages}] Cropper + Debug Overlay...")
        from src.services.cropper import crop_all
        t0 = time.time()
        crop_all(exam, layouts, group_layouts, images, out)
        n_crops = len(list((out / "crops").glob("*.png"))) if (out / "crops").exists() else 0
        n_overlay = len(list((out / "overlay").glob("*.png"))) if (out / "overlay").exists() else 0
        click.echo(f"   ✓ {n_crops} crop images, {n_overlay} overlay pages ({time.time() - t0:.1f}s)")
    else:
        click.echo(f"\n[6/{total_stages}] Cropper — SKIPPED (--no-crop)")

    # ============================================================
    # Stage 7: Upload MinIO (Phase 4) — set minio_key/url cho mọi crop
    # ============================================================
    n_uploaded = 0
    raw_info = None
    minio_stats_text = ""
    do_upload = not no_crop and not no_upload and settings.use_minio_upload

    if do_upload:
        click.echo(f"\n[7/{total_stages}] Upload MinIO "
                    f"(bucket={settings.minio_bucket} @ {settings.minio_endpoint})...")
        from src.services.uploader import upload_exam_assets, upload_raw_file
        from src.services.minio_client import MinIOService
        t0 = time.time()
        try:
            svc = MinIOService()
            # File gốc (PDF) — set source_minio_key/url vào exam TRƯỚC khi ghi exam.json
            if settings.minio_save_raw:
                raw_info = upload_raw_file(input_path, exam, exam_id=exam_id, svc=svc)
            # Crop + exam.json
            n_uploaded = upload_exam_assets(exam, out, exam_id=exam_id, svc=svc)
            up_elapsed = time.time() - t0
            click.echo(f"   ✓ Uploaded {n_uploaded} ảnh"
                        f"{' + file gốc' if raw_info else ''} + exam.json ({up_elapsed:.1f}s)")
        except Exception as e:
            logger.error(f"MinIO upload failed: {e}")
            click.echo(f"   ⚠ MinIO upload failed: {e} — giữ URL local")
        minio_stats_text = f"""
[MinIO — Phase 4]
  Endpoint: {settings.minio_endpoint}
  Bucket: {settings.minio_bucket}
  Key prefix: {settings.minio_prefix or '(none)'}{exam_id}/
  File gốc: {raw_info['minio_key'] if raw_info else '(không upload)'}
  Ảnh uploaded: {n_uploaded}
  Presigned TTL: {settings.minio_presign_days} ngày
"""
    else:
        reason = "SKIPPED"
        if no_crop:
            reason += " (--no-crop)"
        if no_upload:
            reason += " (--no-upload)"
        if not settings.use_minio_upload:
            reason += " (use_minio_upload=False)"
        click.echo(f"\n[7/{total_stages}] Upload MinIO — {reason}")

    # ============================================================
    # Stage: Output JSON (Phase 5) — exam.json là cấu trúc cuối (chứa link MinIO nếu có)
    # ============================================================
    exam_json = exam.model_dump_json(indent=2)
    (out / "exam.json").write_text(exam_json, encoding="utf-8")
    click.echo(f"\n✓ Saved exam.json ({len(exam.questions)} câu, {len(exam.groups)} nhóm)")

    # ============================================================
    # Stage 8: Lưu lịch sử MongoDB (1 đề = 1 bản ghi)
    # ============================================================
    mongo_stats_text = ""
    if settings.use_mongo:
        click.echo(f"\n[8/{total_stages}] Lưu lịch sử MongoDB "
                    f"({settings.mongo_db}.{settings.mongo_collection})...")
        try:
            from src.services.mongo_client import MongoService
            mongo = MongoService()
            mongo.save_exam(exam, raw=raw_info)
            click.echo(f"   ✓ Lưu bản ghi exam {exam_id}")
            mongo_stats_text = f"""
[MongoDB — lịch sử]
  URI: {settings.mongo_uri}
  Collection: {settings.mongo_db}.{settings.mongo_collection}
  _id: {exam_id}
  File gốc: {raw_info['minio_key'] if raw_info else '(không lưu raw)'}
"""
        except Exception as e:
            logger.error(f"Mongo save failed: {e}")
            click.echo(f"   ⚠ Mongo save failed: {e}")
    else:
        click.echo(f"\n[8/{total_stages}] MongoDB — SKIPPED (use_mongo=False)")

    # ============================================================
    # Summary
    # ============================================================
    q_count = sum(1 for a in anchors if a.type.value == "question")
    a_count = sum(1 for a in anchors if a.type.value == "answer")
    g_count = sum(1 for a in anchors if a.type.value == "group_header")

    # Recalc type counter (may have changed after VLM)
    type_counter_final = Counter(q.type.value for q in exam.questions)

    summary = f"""Exam ID: {exam_id}
Input: {input_path}
Output: {out}
Source: {source_file}

[Preprocess]
  Pages: {len(images)}
  DPI: {dpi}

[PaddleOCR]
  Blocks: {total_blocks}
  Text lines: {total_lines}

[Anchors]
  Questions: {q_count}
  Answers: {a_count}
  Groups: {g_count}
  Total: {len(anchors)}

Anchors by type:
"""
    for t, n in Counter(a.type.value for a in anchors).most_common():
        summary += f"  {t}: {n}\n"

    summary += f"""
[Snake Walker — Phase 2]
  Questions extracted: {len(questions)}
  Groups: {len(groups)}
  Numbers: {', '.join(str(q.number) for q in sorted(questions, key=lambda q: q.number))}

[Classifier — Phase 2]
  Question types:
"""
    for t_name, n in type_counter.most_common():
        summary += f"    {t_name}: {n}\n"

    summary += f"""
  n_mcq: {exam.n_mcq}
  n_essay: {exam.n_essay}
  avg_confidence: {exam.avg_confidence:.3f}

[Groups]
"""
    for g in groups:
        summary += f"  {g.id} ({g.type.value}): \"{g.header_text[:60]}\" — {len(g.question_ids)} câu\n"

    if not no_crop:
        summary += f"""
[Cropper — Phase 2]
  Crop images: {n_crops}
  Overlay pages: {n_overlay}
"""

    # MinIO upload stats (Phase 4)
    if minio_stats_text:
        summary += minio_stats_text

    # MongoDB stats
    if mongo_stats_text:
        summary += mongo_stats_text

    # Metadata
    summary += f"""
[Metadata]
  Mã đề: {metadata.ma_de or 'N/A'}
  Môn: {metadata.mon or 'N/A'}
  Thời gian: {metadata.thoi_gian_phut or 'N/A'} phút
  Trường: {metadata.truong or 'N/A'}
  Năm học: {metadata.nam_hoc or 'N/A'}
  Tổng số câu: {metadata.tong_so_cau}
"""

    # Câu cần review
    review_questions = [q for q in questions if q.needs_review]
    if review_questions:
        summary += f"\n[Needs Review] ({len(review_questions)} câu)\n"
        for q in review_questions:
            summary += f"  Câu {q.number}: type={q.type.value}, answers={len(q.answers)}\n"

    (out / "summary.txt").write_text(summary, encoding="utf-8")
    click.echo(f"\n📋 Summary saved to {out / 'summary.txt'}")
    click.echo("\n" + summary)


if __name__ == "__main__":
    main()
