"""CLI Phase 1: Test pipeline Preprocess + PaddleOCR + Anchor Extract.

Usage:
    python scripts/parse_cli.py input/de.pdf
    python scripts/parse_cli.py input/de.pdf --dpi 400
    python scripts/parse_cli.py input/de.pdf --save-images   # lưu rendered pages

Output (vào output/{exam_id}/):
    blocks.json    - PaddleOCR output
    anchors.json   - extracted anchors
    summary.txt    - tóm tắt
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
@click.option("--debug", is_flag=True, default=False, help="Verbose logging")
def main(input_path, output_dir, dpi, no_deskew, save_images, debug):
    """Phase 1 pipeline: Preprocess → PaddleOCR → Anchor extract → JSON output."""
    if debug:
        import logging
        logger.remove()
        logger.add(sys.stderr, level="DEBUG")

    # Setup output dir
    exam_id = str(uuid4())[:8]
    out = Path(output_dir) if output_dir else Path("output") / exam_id
    out.mkdir(parents=True, exist_ok=True)
    click.echo(f"📂 Output: {out}")

    dpi = dpi or settings.default_dpi

    # ============================================================
    # Stage 1: Preprocess
    # ============================================================
    click.echo(f"\n[1/3] Preprocess (DPI={dpi}, deskew={not no_deskew})...")
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
    click.echo(f"\n[2/3] PaddleOCR PP-StructureV3 "
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
    click.echo(f"\n[3/3] Anchor Extraction...")
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
    # Summary
    # ============================================================
    from collections import Counter
    q_count = sum(1 for a in anchors if a.type.value == "question")
    a_count = sum(1 for a in anchors if a.type.value == "answer")
    g_count = sum(1 for a in anchors if a.type.value == "group_header")

    summary = f"""Exam ID: {exam_id}
Input: {input_path}
Output: {out}

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

    summary += "\nQuestion anchors:\n"
    for a in [x for x in anchors if x.type.value == "question"][:20]:
        summary += f"  - Page {a.page_index + 1}: \"{a.text}\" → Câu {a.value}\n"

    (out / "summary.txt").write_text(summary, encoding="utf-8")
    click.echo(f"\n📋 Summary saved to {out / 'summary.txt'}")
    click.echo("\n" + summary)


if __name__ == "__main__":
    main()
