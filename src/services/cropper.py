"""Stage 6: Cropper — cắt ảnh từng câu hỏi / đáp án / passage từ page images.

Tạo ảnh crop (PNG) cho mỗi câu (full, content, từng đáp án) và passage group.
Vẽ debug overlay (bbox màu) lên ảnh gốc để review thủ công.

Usage:
    from src.services.cropper import crop_all
    crop_all(exam, layouts, group_layouts, images, out_dir)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from src.core.logging import logger
from src.schemas.block import BBox
from src.schemas.exam import (
    Answer,
    CroppedImage,
    Exam,
    Group,
    Question,
    QuestionType,
)
from src.services.snake_walker import MultiRegion, QuestionLayout, Region


# ============================================================
# Constants — Màu overlay theo §6.4
# ============================================================
COLOR_GROUP = (220, 30, 30)      # 🔴 Đỏ — vùng Group
COLOR_QUESTION = (30, 90, 220)   # 🔵 Xanh dương — vùng Question (full)
COLOR_CONTENT = (150, 30, 200)   # 🟣 Tím — vùng content câu hỏi
COLOR_ANSWER = (30, 160, 60)     # 🟢 Xanh lá — đáp án

OVERLAY_LINE_WIDTH = 3


# ============================================================
# Core crop function
# ============================================================

def _crop_region(region: Region, images: list[Image.Image]) -> Optional[Image.Image]:
    """Crop 1 region từ 1 trang."""
    if region.page_index >= len(images):
        logger.warning(f"Region page_index={region.page_index} vượt quá số trang ({len(images)})")
        return None

    img = images[region.page_index]
    x1, y1, x2, y2 = region.bbox

    # Clamp bbox trong biên ảnh
    w, h = img.size
    x1 = max(0, int(x1))
    y1 = max(0, int(y1))
    x2 = min(w, int(x2))
    y2 = min(h, int(y2))

    if x2 <= x1 or y2 <= y1:
        logger.warning(f"Region bbox rỗng sau clamp: ({x1},{y1},{x2},{y2})")
        return None

    return img.crop((x1, y1, x2, y2))


def _crop_multi_region(mregion: MultiRegion, images: list[Image.Image]) -> Optional[Image.Image]:
    """Crop MultiRegion → 1 ảnh PNG.

    Nếu 1 part: crop thẳng.
    Nếu nhiều part (vắt trang): crop từng part rồi ghép dọc (vertical stack).
    Width = max width các part (canh trái, nền trắng pad).
    """
    if not mregion.parts:
        return None

    if len(mregion.parts) == 1:
        return _crop_region(mregion.parts[0], images)

    # Nhiều parts → crop từng cái rồi ghép dọc
    crops: list[Image.Image] = []
    for part in mregion.parts:
        cropped = _crop_region(part, images)
        if cropped:
            crops.append(cropped)

    if not crops:
        return None

    if len(crops) == 1:
        return crops[0]

    # Ghép dọc
    max_width = max(c.width for c in crops)
    total_height = sum(c.height for c in crops)

    result = Image.new("RGB", (max_width, total_height), (255, 255, 255))
    y_offset = 0
    for c in crops:
        result.paste(c, (0, y_offset))
        y_offset += c.height

    return result


def _save_crop(img: Image.Image, path: Path) -> tuple[int, int, int]:
    """Lưu ảnh crop PNG, trả về (width, height, size_bytes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), "PNG")
    size_bytes = path.stat().st_size
    return img.width, img.height, size_bytes


def _make_cropped_image(
    mregion: MultiRegion,
    images: list[Image.Image],
    save_path: Path,
    rel_path: str,
) -> Optional[CroppedImage]:
    """Crop multi-region, lưu file, tạo CroppedImage object."""
    img = _crop_multi_region(mregion, images)
    if img is None:
        return None

    width, height, size_bytes = _save_crop(img, save_path)

    # bbox = bbox part đầu (hoặc bao nếu cần)
    bbox = mregion.parts[0].bbox if mregion.parts else (0, 0, 0, 0)
    page_indices = sorted(set(p.page_index for p in mregion.parts))

    return CroppedImage(
        bbox=bbox,
        page_indices=page_indices,
        minio_key="",  # Phase 4 mới upload
        url=rel_path,
        width=width,
        height=height,
        size_bytes=size_bytes,
    )


# ============================================================
# Crop theo loại câu (§6.3)
# ============================================================

def crop_question(
    layout: QuestionLayout,
    images: list[Image.Image],
    out_dir: Path,
    question: Question,
) -> tuple[Optional[CroppedImage], Optional[CroppedImage], list[Answer]]:
    """Crop ảnh cho 1 câu hỏi.

    Returns:
        (full_image, content_image, answers có .image đã gán)
    """
    crops_dir = out_dir / "crops"
    q_num = question.number

    # Full image (toàn câu: content + answers)
    full_path = crops_dir / f"q{q_num}_full.png"
    full_image = _make_cropped_image(
        layout.full, images, full_path, f"crops/q{q_num}_full.png"
    )

    # Content image (chỉ đề bài)
    content_path = crops_dir / f"q{q_num}_content.png"
    content_image = _make_cropped_image(
        layout.content, images, content_path, f"crops/q{q_num}_content.png"
    )

    # Crop từng đáp án
    updated_answers = list(question.answers)  # copy
    for i, (label, a_region) in enumerate(layout.answers):
        if i < len(updated_answers):
            a_path = crops_dir / f"q{q_num}_{label}.png"
            a_image = _make_cropped_image(
                a_region, images, a_path, f"crops/q{q_num}_{label}.png"
            )
            updated_answers[i].image = a_image

    return full_image, content_image, updated_answers


def crop_group_lead(
    mregion: MultiRegion,
    images: list[Image.Image],
    out_dir: Path,
    group_id: str,
    q_range: str = "",
) -> Optional[CroppedImage]:
    """Crop ảnh đoạn dẫn (header + passage) cho 1 group.

    q_range: hậu tố dải số câu để dễ kiểm tra group, vd "1_3" → g1_header_1_3.png.
    """
    crops_dir = out_dir / "crops"
    suffix = f"_{q_range}" if q_range else ""
    name = f"{group_id}_header{suffix}.png"
    path = crops_dir / name
    return _make_cropped_image(mregion, images, path, f"crops/{name}")


# ============================================================
# Debug overlay (§6.4) — BẮT BUỘC
# ============================================================

def _draw_overlay(
    images: list[Image.Image],
    layouts: dict[str, QuestionLayout],
    group_layouts: dict[str, MultiRegion],
    questions: list[Question],
    groups: list[Group],
    out_dir: Path,
) -> None:
    """Vẽ debug overlay lên ảnh gốc: bbox màu theo spec."""
    overlay_dir = out_dir / "overlay"
    overlay_dir.mkdir(parents=True, exist_ok=True)

    # Load font mặc định
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    # Tạo copy các trang để vẽ
    page_copies = [img.copy() for img in images]
    draws = [ImageDraw.Draw(img) for img in page_copies]

    # Xây map question number → question
    q_map = {q.id: q for q in questions}

    # 1. Vẽ Group regions (🔴 Đỏ)
    for g in groups:
        g_id = g.id
        if g_id in group_layouts:
            mregion = group_layouts[g_id]
            for part in mregion.parts:
                if part.page_index < len(draws):
                    _draw_rect(draws[part.page_index], part.bbox,
                               COLOR_GROUP, f"[{g_id}] {g.type.value}")

    # 2. Vẽ Question regions
    for q_id, layout in layouts.items():
        q = q_map.get(q_id)
        q_label = f"Q{q.number}" if q else q_id

        # 🔵 Xanh dương — vùng Question (full)
        for part in layout.full.parts:
            if part.page_index < len(draws):
                _draw_rect(draws[part.page_index], part.bbox,
                           COLOR_QUESTION, q_label)

        # 🟣 Tím — vùng content
        for part in layout.content.parts:
            if part.page_index < len(draws):
                _draw_rect(draws[part.page_index], part.bbox,
                           COLOR_CONTENT, f"{q_label}_content")

        # 🟢 Xanh lá — đáp án
        for label, a_region in layout.answers:
            for part in a_region.parts:
                if part.page_index < len(draws):
                    _draw_rect(draws[part.page_index], part.bbox,
                               COLOR_ANSWER, f"{q_label}_{label}")

    # Lưu overlay
    for i, img in enumerate(page_copies):
        path = overlay_dir / f"page_{i:02d}.png"
        img.save(str(path), "PNG")

    logger.info(f"Debug overlay: {len(page_copies)} trang lưu tại {overlay_dir}")


def _draw_rect(
    draw: ImageDraw.ImageDraw,
    bbox: BBox,
    color: tuple[int, int, int],
    label: str = "",
) -> None:
    """Vẽ rectangle + label nhỏ góc trên trái."""
    x1, y1, x2, y2 = [int(v) for v in bbox]
    draw.rectangle([x1, y1, x2, y2], outline=color, width=OVERLAY_LINE_WIDTH)

    if label:
        # Vẽ label text góc trên trái, nền bán trong suốt
        try:
            # Tạo text background
            text_bbox = draw.textbbox((x1 + 2, y1 + 2), label)
            draw.rectangle(
                [text_bbox[0] - 1, text_bbox[1] - 1, text_bbox[2] + 1, text_bbox[3] + 1],
                fill=(255, 255, 255),
            )
            draw.text((x1 + 2, y1 + 2), label, fill=color)
        except Exception:
            pass  # Font không hỗ trợ, bỏ qua label


# ============================================================
# MAIN: crop_all — điền in-place vào Exam
# ============================================================

def crop_all(
    exam: Exam,
    layouts: dict[str, QuestionLayout],
    group_layouts: dict[str, MultiRegion],
    images: list[Image.Image],
    out_dir: Path,
) -> None:
    """Crop tất cả và điền CroppedImage in-place vào exam.questions/groups.

    Tạo:
    - crops/ chứa ảnh từng câu/đáp án/passage.
    - overlay/ chứa debug overlay từng trang.
    """
    crops_dir = out_dir / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    # Crop questions
    for q in exam.questions:
        layout = layouts.get(q.id)
        if not layout:
            logger.warning(f"Câu {q.number}: không có layout → bỏ qua crop")
            continue

        try:
            full_img, content_img, updated_answers = crop_question(
                layout, images, out_dir, q
            )
            q.full_image = full_img
            q.content_image = content_img
            q.answers = updated_answers
        except Exception as e:
            logger.error(f"Câu {q.number}: lỗi crop — {e}")
            q.needs_review = True

    # Crop group lead-in (header + passage)
    # Map q_id → số câu để tính dải [min..max] đưa vào tên file (dễ kiểm tra group).
    qnum_by_id = {q.id: q.number for q in exam.questions}
    for g in exam.groups:
        if g.id in group_layouts:
            nums = sorted(qnum_by_id[qid] for qid in g.question_ids if qid in qnum_by_id)
            q_range = f"{nums[0]}_{nums[-1]}" if nums else ""
            try:
                lead_img = crop_group_lead(
                    group_layouts[g.id], images, out_dir, g.id, q_range
                )
                g.header_image = lead_img
                if g.passage_text:  # PASSAGE → đoạn dẫn cũng là passage
                    g.passage_image = lead_img
            except Exception as e:
                logger.error(f"Group {g.id}: lỗi crop lead-in — {e}")

    # Vẽ debug overlay
    try:
        _draw_overlay(
            images, layouts, group_layouts,
            exam.questions, exam.groups, out_dir
        )
    except Exception as e:
        logger.error(f"Lỗi vẽ overlay: {e}")

    n_crops = len(list(crops_dir.glob("*.png")))
    logger.info(f"Cropper: tổng {n_crops} ảnh crop tại {crops_dir}")
