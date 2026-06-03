"""Stage 4: Snake Walker — gom Anchor → Question/Group theo luồng "con rắn" liên trang.

Ý tưởng: coi toàn bộ document như 1 dải liên tục nối các trang lại (global position).
Sort anchor theo (page_index, y_top), xác định ranh giới mỗi câu, gom blocks + answers,
tạo Group, phát hiện phần lời giải (Azota) để loại bỏ, parse metadata.

Usage:
    from src.services.snake_walker import snake_walk
    questions, groups, layouts, group_layouts = snake_walk(
        blocks_per_page, anchors, page_heights, page_widths
    )
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from src.core.logging import logger
from src.schemas.anchor import Anchor, AnchorType
from src.schemas.block import BBox, Block, BlockType
from src.schemas.exam import (
    Answer,
    ExamMetadata,
    Group,
    GroupType,
    Question,
    QuestionType,
)

# ============================================================
# Region dataclass — truyền từ Walker → Cropper
# ============================================================

@dataclass
class Region:
    """Vùng bbox trên 1 trang cụ thể."""
    page_index: int
    bbox: BBox  # (x1, y1, x2, y2) pixel


@dataclass
class MultiRegion:
    """1 vùng logic có thể trải nhiều trang."""
    parts: list[Region] = field(default_factory=list)


@dataclass
class QuestionLayout:
    """Layout (pixel regions) của 1 câu hỏi — dùng cho Cropper."""
    question_id: str
    full: MultiRegion = field(default_factory=MultiRegion)       # toàn câu
    content: MultiRegion = field(default_factory=MultiRegion)    # chỉ đề bài
    answers: list[tuple[str, MultiRegion]] = field(default_factory=list)  # (label, region)


# ============================================================
# Constants
# ============================================================

# Padding (px) khi tính bbox vùng
PAD = 8

# Từ khóa phát hiện ranh giới phần lời giải (strip dấu, lowercase)
_SOLUTION_MARKERS = [
    "loi giai chi tiet",
    "giai chi tiet",
    "huong dan giai",
    "dap an va loi giai",
    "dap an chi tiet",
    "phan loi giai",
]

_END_MARKERS_REGEX = re.compile(
    r"^-*\s*het\s*-*$|"                  # ---hết---
    r"^bang\s+dap\s+an|"                 # bảng đáp án
    r"^\d+\s*[\.\)]\s*[A-D]\s+\d+\s*[\.\)]\s*[A-D]",  # 1.A 2.B ...
    re.IGNORECASE,
)


# ============================================================
# Utility functions
# ============================================================

def _strip_accents(text: str) -> str:
    """Bỏ dấu tiếng Việt (dùng để so sánh rule)."""
    nfkd = unicodedata.normalize("NFKD", text)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return no_accents.replace("đ", "d").replace("Đ", "D")


def _gpos(page_index: int, y: float) -> tuple[int, float]:
    """Global position key để sort/so sánh."""
    return (page_index, y)


def _gpos_of_anchor(a: Anchor) -> tuple[int, float]:
    return a.global_position()


def _gpos_of_block(b: Block) -> tuple[int, float]:
    return (b.page_index, b.bbox[1])


def _in_range(pos: tuple[int, float],
              start: tuple[int, float],
              end: tuple[int, float]) -> bool:
    """Check pos nằm trong [start, end)."""
    return start <= pos < end


def _pad_bbox(bbox: BBox, page_w: float, page_h: float, pad: int = PAD) -> BBox:
    """Nới padding bbox, clamp trong biên trang."""
    x1, y1, x2, y2 = bbox
    return (
        max(0, x1 - pad),
        max(0, y1 - pad),
        min(page_w, x2 + pad),
        min(page_h, y2 + pad),
    )


def _merge_bboxes(bboxes: list[BBox]) -> BBox:
    """Merge nhiều bbox thành 1 bbox bao."""
    if not bboxes:
        return (0, 0, 0, 0)
    x1 = min(b[0] for b in bboxes)
    y1 = min(b[1] for b in bboxes)
    x2 = max(b[2] for b in bboxes)
    y2 = max(b[3] for b in bboxes)
    return (x1, y1, x2, y2)


def _classify_group_type(header_text: str) -> GroupType:
    """Phân loại GroupType từ text header (strip dấu, lowercase). Theo §3.5."""
    norm = _strip_accents(header_text).lower().strip()

    # PASSAGE
    for kw in ["read the following", "doc doan", "doc van ban", "dua vao"]:
        if kw in norm:
            return GroupType.PASSAGE

    # SECTION_PART
    if norm.startswith("phan"):
        return GroupType.SECTION_PART

    # INSTRUCTION
    for kw in ["mark the letter", "choose the", "cho doan"]:
        if kw in norm:
            return GroupType.INSTRUCTION

    return GroupType.UNKNOWN


# ============================================================
# Phát hiện & loại phần lời giải (Azota edge case §3.6b)
# ============================================================

def _find_solution_boundary(
    anchors: list[Anchor],
    blocks_per_page: list[list[Block]],
) -> Optional[tuple[int, float]]:
    """Tìm global position bắt đầu phần lời giải.

    Trả về None nếu không phát hiện (đề bình thường).
    """
    # Cách 1: Tìm marker text trong blocks (Hết, bảng đáp án, lời giải chi tiết)
    all_blocks_sorted: list[Block] = []
    for page_blocks in blocks_per_page:
        all_blocks_sorted.extend(page_blocks)
    all_blocks_sorted.sort(key=lambda b: _gpos_of_block(b))

    for block in all_blocks_sorted:
        block_text = block.text.strip()
        if not block_text:
            continue
        norm = _strip_accents(block_text).lower().strip()

        # Check marker lời giải
        for marker in _SOLUTION_MARKERS:
            if marker in norm:
                pos = _gpos_of_block(block)
                logger.info(f"Phát hiện marker lời giải: \"{block_text}\" tại page={pos[0]}, y={pos[1]:.0f}")
                return pos

        # Check "hết" / bảng đáp án
        for line in block.lines:
            line_norm = _strip_accents(line.text.strip()).lower().strip()
            if _END_MARKERS_REGEX.match(line_norm):
                pos = (block.page_index, line.bbox[1])
                logger.info(f"Phát hiện marker kết thúc: \"{line.text.strip()}\" tại page={pos[0]}, y={pos[1]:.0f}")
                return pos

    # Cách 2 (Fallback): Kiểm tra số câu không tăng đơn điệu
    q_anchors = sorted(
        [a for a in anchors if a.type == AnchorType.QUESTION and a.number is not None],
        key=_gpos_of_anchor,
    )
    if len(q_anchors) > 0:
        max_seen = -1
        for a in q_anchors:
            n = a.number
            if n is not None:
                if n <= max_seen and max_seen > 1:
                    # Số câu nhảy lùi → bắt đầu phần lời giải
                    pos = _gpos_of_anchor(a)
                    logger.info(
                        f"Fallback: số câu nhảy lùi (Câu {n} sau max={max_seen}) "
                        f"→ ranh giới lời giải tại page={pos[0]}, y={pos[1]:.0f}"
                    )
                    return pos
                max_seen = max(max_seen, n)

    return None


# ============================================================
# Parse Metadata (§3.7)
# ============================================================

def _parse_metadata(
    anchors: list[Anchor],
    blocks_per_page: list[list[Block]],
    n_questions: int,
) -> ExamMetadata:
    """Parse metadata từ anchor METADATA + blocks đầu trang 1."""
    meta = ExamMetadata(tong_so_cau=n_questions)

    # Thu thập text metadata từ anchors
    meta_texts: list[str] = []
    for a in anchors:
        if a.type == AnchorType.METADATA:
            meta_texts.append(a.text)

    # Thêm text từ vài block đầu trang 1
    if blocks_per_page:
        for block in blocks_per_page[0][:10]:  # 10 blocks đầu
            meta_texts.append(block.text)

    full_text = "\n".join(meta_texts)
    norm = _strip_accents(full_text).lower()

    # Mã đề
    m = re.search(r"ma\s+de[:\s]*(\d+)", norm)
    if m:
        meta.ma_de = m.group(1)

    # Thời gian
    m = re.search(r"thoi\s+gian[:\s]*(\d+)\s*(?:phut)?", norm)
    if m:
        meta.thoi_gian_phut = int(m.group(1))

    # Môn
    m = re.search(r"mon[:\s]+([^\n\r]+)", norm)
    if m:
        # Lấy text gốc (có dấu) tương ứng vị trí match
        raw_m = re.search(r"[Mm][oôơ]n[:\s]+([^\n\r]+)", full_text)
        if raw_m:
            meta.mon = raw_m.group(1).strip()
        else:
            meta.mon = m.group(1).strip()

    # Trường
    for pattern in [r"truong\s+(?:thpt|thcs|th)[:\s]*([^\n\r]+)",
                    r"so\s+gd[:\s]*([^\n\r]+)"]:
        m = re.search(pattern, norm)
        if m:
            raw_m = re.search(pattern.replace("truong", "[Tt]r[ưu][oờơ]ng"), full_text)
            if raw_m:
                meta.truong = raw_m.group(1).strip()
            else:
                meta.truong = m.group(1).strip()
            break

    # Năm học
    m = re.search(r"nam\s+hoc[:\s]*(\d{4}\s*[-–]\s*\d{4})", norm)
    if m:
        meta.nam_hoc = m.group(1).strip()

    return meta


# ============================================================
# MAIN: snake_walk
# ============================================================

def snake_walk(
    blocks_per_page: list[list[Block]],
    anchors: list[Anchor],
    page_heights: list[float],
    page_widths: list[float],
) -> tuple[list[Question], list[Group], dict[str, QuestionLayout], dict[str, MultiRegion]]:
    """Snake Walker — gom anchor thành Question/Group.

    Args:
        blocks_per_page: list[list[Block]] — output PaddleParser.
        anchors: list[Anchor] — output extract_anchors (chưa sort).
        page_heights: chiều cao pixel mỗi trang.
        page_widths: chiều rộng pixel mỗi trang.

    Returns:
        (questions, groups, question_layouts, group_layouts)
        - questions: list[Question] đã gom, type=UNKNOWN (Classifier sẽ gán sau).
        - groups: list[Group] với question_ids.
        - question_layouts: dict[question_id → QuestionLayout] cho Cropper.
        - group_layouts: dict[group_id → MultiRegion] cho passage crop.
    """
    last_page = len(page_heights) - 1
    inf_y = float("inf")

    # ================================================================
    # Bước 0 — Phát hiện & loại phần lời giải (Azota §3.6b)
    # ================================================================
    solution_boundary = _find_solution_boundary(anchors, blocks_per_page)

    if solution_boundary is not None:
        original_count = len([a for a in anchors if a.type == AnchorType.QUESTION])
        anchors = [
            a for a in anchors
            if _gpos_of_anchor(a) < solution_boundary
            or a.type not in (AnchorType.QUESTION, AnchorType.ANSWER,
                              AnchorType.SUB_QUESTION)
        ]
        filtered_count = original_count - len([a for a in anchors if a.type == AnchorType.QUESTION])
        logger.info(f"Đã loại {filtered_count} câu thuộc phần lời giải (giữ {original_count - filtered_count} câu)")

    # ================================================================
    # Bước 1 — Sort & phân loại anchor
    # ================================================================
    q_anchors = sorted(
        [a for a in anchors if a.type == AnchorType.QUESTION],
        key=_gpos_of_anchor,
    )
    answer_anchors = sorted(
        [a for a in anchors if a.type == AnchorType.ANSWER],
        key=_gpos_of_anchor,
    )
    sub_anchors = sorted(
        [a for a in anchors if a.type == AnchorType.SUB_QUESTION],
        key=_gpos_of_anchor,
    )
    group_anchors = sorted(
        [a for a in anchors if a.type == AnchorType.GROUP_HEADER],
        key=_gpos_of_anchor,
    )

    # Flatten tất cả blocks, sort theo global position
    all_blocks: list[Block] = []
    for page_blocks in blocks_per_page:
        all_blocks.extend(page_blocks)
    all_blocks.sort(key=_gpos_of_block)

    logger.info(
        f"Snake Walker input: {len(q_anchors)} câu, {len(answer_anchors)} đáp án, "
        f"{len(sub_anchors)} sub, {len(group_anchors)} group, "
        f"{len(all_blocks)} blocks, {len(page_heights)} trang"
    )

    # ================================================================
    # Bước 2 — Xác định ranh giới mỗi câu
    # ================================================================
    questions: list[Question] = []
    layouts: dict[str, QuestionLayout] = {}

    # Theo dõi tính liên tục số câu
    seen_numbers: set[int] = set()
    prev_number: Optional[int] = None

    for i, q_anchor in enumerate(q_anchors):
        q_num = q_anchor.number
        if q_num is None:
            logger.warning(f"Question anchor không có number: \"{q_anchor.text}\" → bỏ qua")
            continue

        q_id = f"q{q_num}"
        start = _gpos_of_anchor(q_anchor)

        # End = anchor câu kế tiếp hoặc cuối document
        if i + 1 < len(q_anchors):
            end = _gpos_of_anchor(q_anchors[i + 1])
        else:
            end = (last_page, inf_y)

        # --- Kiểm tra bất thường ---
        needs_review = False

        # Trùng số câu
        if q_num in seen_numbers:
            logger.warning(f"Trùng số câu: Câu {q_num}")
            needs_review = True
            # Bỏ qua câu trùng (giữ cái đầu)
            continue
        seen_numbers.add(q_num)

        # Số không liên tục
        if prev_number is not None and q_num != prev_number + 1:
            if q_num > prev_number + 1:
                logger.warning(f"Số câu không liên tục: nhảy từ {prev_number} sang {q_num}")
                needs_review = True
        prev_number = q_num

        # ================================================================
        # Bước 3 — Gom blocks vào câu
        # ================================================================
        q_blocks: list[Block] = []
        for block in all_blocks:
            bpos = _gpos_of_block(block)
            if _in_range(bpos, start, end):
                q_blocks.append(block)

        if not q_blocks:
            logger.warning(f"Câu {q_num}: không có block nào → needs_review")
            needs_review = True

        # Page indices
        page_indices = sorted(set(b.page_index for b in q_blocks)) if q_blocks else [q_anchor.page_index]

        # Content text (concat text các block)
        content_parts: list[str] = []
        has_figure = False
        has_table = False
        has_formula = False

        for b in q_blocks:
            if b.type == BlockType.FIGURE:
                has_figure = True
            elif b.type == BlockType.TABLE:
                has_table = True
            elif b.type == BlockType.EQUATION:
                has_formula = True
            if b.text.strip():
                content_parts.append(b.text.strip())

        # ================================================================
        # Bước 3b — Gom answers vào câu
        # ================================================================
        q_answers_anchors: list[Anchor] = [
            a for a in answer_anchors
            if _in_range(_gpos_of_anchor(a), start, end)
        ]
        q_sub_anchors: list[Anchor] = [
            a for a in sub_anchors
            if _in_range(_gpos_of_anchor(a), start, end)
        ]

        # Tạo Answer objects
        answers: list[Answer] = []
        seen_labels: set[str] = set()
        for a_anchor in q_answers_anchors:
            label = a_anchor.value or ""
            if label in seen_labels:
                logger.warning(f"Câu {q_num}: trùng label đáp án '{label}' → needs_review")
                needs_review = True
                continue
            seen_labels.add(label)

            # Text đáp án = text của anchor line
            a_text = a_anchor.text.strip()
            answers.append(Answer(label=label, text=a_text))

        # ================================================================
        # Bước 4 — Tách content vs answers region (cho crop)
        # ================================================================
        # Content region: từ đầu câu → trước answer/sub đầu tiên
        # Answer region: từng answer anchor → answer kế hoặc hết câu

        first_answer_pos: Optional[tuple[int, float]] = None
        if q_answers_anchors:
            first_answer_pos = _gpos_of_anchor(q_answers_anchors[0])
        elif q_sub_anchors:
            first_answer_pos = _gpos_of_anchor(q_sub_anchors[0])

        # --- Full region ---
        full_region = _compute_multi_region(
            q_blocks, start, end, page_heights, page_widths
        )

        # --- Content region ---
        if first_answer_pos:
            content_blocks = [
                b for b in q_blocks
                if _gpos_of_block(b) < first_answer_pos
            ]
            content_region = _compute_multi_region(
                content_blocks, start, first_answer_pos, page_heights, page_widths
            )
            # Chỉ lấy content text (không bao gồm đáp án)
            content_text = " ".join(
                b.text.strip() for b in content_blocks if b.text.strip()
            )
        else:
            # Không có đáp án → toàn bộ là content (tự luận)
            content_region = full_region
            content_text = " ".join(content_parts)

        # --- Answer regions ---
        answer_regions: list[tuple[str, MultiRegion]] = []
        for j, a_anchor in enumerate(q_answers_anchors):
            a_start = _gpos_of_anchor(a_anchor)
            if j + 1 < len(q_answers_anchors):
                a_end = _gpos_of_anchor(q_answers_anchors[j + 1])
            else:
                a_end = end  # hết câu

            a_blocks = [
                b for b in q_blocks
                if _in_range(_gpos_of_block(b), a_start, a_end)
            ]
            a_region = _compute_multi_region(
                a_blocks, a_start, a_end, page_heights, page_widths
            )
            label = a_anchor.value or f"ans{j}"
            answer_regions.append((label, a_region))

        # --- Tính confidence ---
        related_confidences = [q_anchor.confidence]
        for a in q_answers_anchors:
            related_confidences.append(a.confidence)
        confidence = min(related_confidences)

        # ================================================================
        # Bước 5 — Tạo Question
        # ================================================================
        question = Question(
            id=q_id,
            number=q_num,
            type=QuestionType.UNKNOWN,
            content_text=content_text,
            page_indices=page_indices,
            answers=answers,
            has_figure=has_figure,
            has_table=has_table,
            has_formula=has_formula,
            confidence=confidence,
            needs_review=needs_review,
        )
        questions.append(question)

        layout = QuestionLayout(
            question_id=q_id,
            full=full_region,
            content=content_region,
            answers=answer_regions,
        )
        layouts[q_id] = layout

    logger.info(f"Snake Walker: tạo {len(questions)} câu hỏi")

    # ================================================================
    # Bước 6 — Tạo Groups (§3.4)
    # ================================================================
    groups: list[Group] = []
    group_layouts: dict[str, MultiRegion] = {}

    if group_anchors:
        for k, g_anchor in enumerate(group_anchors):
            g_id = f"g{k + 1}"
            g_start = _gpos_of_anchor(g_anchor)
            if k + 1 < len(group_anchors):
                g_end = _gpos_of_anchor(group_anchors[k + 1])
            else:
                g_end = (last_page, inf_y)

            g_type = _classify_group_type(g_anchor.text)

            # Tìm câu hỏi thuộc group này
            g_question_ids: list[str] = []
            for q in questions:
                # Câu thuộc group nếu anchor câu nằm trong [g_start, g_end)
                q_anchor_match = None
                for qa in q_anchors:
                    if qa.number == q.number:
                        q_anchor_match = qa
                        break
                if q_anchor_match:
                    qpos = _gpos_of_anchor(q_anchor_match)
                    if _in_range(qpos, g_start, g_end):
                        g_question_ids.append(q.id)
                        q.group_id = g_id

            # Passage text: blocks giữa group header và câu đầu tiên
            passage_text = ""
            passage_region = MultiRegion()

            if g_type == GroupType.PASSAGE and g_question_ids:
                # Tìm câu đầu tiên trong group
                first_q_in_group = None
                for q in questions:
                    if q.id == g_question_ids[0]:
                        first_q_in_group = q
                        break

                if first_q_in_group:
                    first_q_anchor = None
                    for qa in q_anchors:
                        if qa.number == first_q_in_group.number:
                            first_q_anchor = qa
                            break

                    if first_q_anchor:
                        passage_start = _gpos(g_anchor.page_index, g_anchor.bbox[3])  # dưới header
                        passage_end = _gpos_of_anchor(first_q_anchor)

                        passage_blocks = [
                            b for b in all_blocks
                            if _in_range(_gpos_of_block(b), passage_start, passage_end)
                        ]
                        passage_text = " ".join(
                            b.text.strip() for b in passage_blocks if b.text.strip()
                        )
                        passage_region = _compute_multi_region(
                            passage_blocks, passage_start, passage_end,
                            page_heights, page_widths
                        )

            group = Group(
                id=g_id,
                type=g_type,
                header_text=g_anchor.text.strip(),
                passage_text=passage_text,
                question_ids=g_question_ids,
            )
            groups.append(group)

            if passage_region.parts:
                group_layouts[g_id] = passage_region

    logger.info(f"Snake Walker: tạo {len(groups)} groups")

    # Kiểm tra số câu không liên tục (đánh dấu review)
    _check_continuity(questions)

    return questions, groups, layouts, group_layouts


# ============================================================
# Helper: Compute MultiRegion từ tập blocks
# ============================================================

def _compute_multi_region(
    blocks: list[Block],
    start: tuple[int, float],
    end: tuple[int, float],
    page_heights: list[float],
    page_widths: list[float],
) -> MultiRegion:
    """Tính MultiRegion từ tập blocks, hỗ trợ vắt trang.

    Nếu vùng trải nhiều trang → tách thành nhiều Region (mỗi trang 1 part).
    Part trang đầu: từ y_top câu xuống đáy trang.
    Part trang cuối: từ đỉnh tới y_bottom.
    Part trang giữa: full chiều cao.
    """
    if not blocks:
        # Fallback: tạo region từ start position
        page_idx = start[0]
        if page_idx < len(page_heights):
            pw = page_widths[page_idx]
            ph = page_heights[page_idx]
            bbox = _pad_bbox(
                (0, start[1], pw, min(start[1] + 50, ph)),
                pw, ph,
            )
            return MultiRegion(parts=[Region(page_index=page_idx, bbox=bbox)])
        return MultiRegion()

    # Nhóm blocks theo trang
    pages_involved = sorted(set(b.page_index for b in blocks))

    parts: list[Region] = []
    for page_idx in pages_involved:
        page_blocks = [b for b in blocks if b.page_index == page_idx]
        if not page_blocks:
            continue

        pw = page_widths[page_idx] if page_idx < len(page_widths) else 2550
        ph = page_heights[page_idx] if page_idx < len(page_heights) else 3300

        # Tính bbox bao các blocks trên trang này
        bboxes = [b.bbox for b in page_blocks]
        # Thêm bbox các lines riêng lẻ để chính xác hơn
        for b in page_blocks:
            for line in b.lines:
                bboxes.append(line.bbox)

        merged = _merge_bboxes(bboxes)

        # Điều chỉnh cho trang đầu/cuối/giữa khi vắt trang
        if len(pages_involved) > 1:
            if page_idx == pages_involved[0]:
                # Trang đầu: từ y_top câu đến đáy trang
                merged = (merged[0], min(merged[1], start[1]), merged[2], ph)
            elif page_idx == pages_involved[-1]:
                # Trang cuối: từ đỉnh trang đến y_bottom
                merged = (merged[0], 0, merged[2], merged[3])
            else:
                # Trang giữa: full chiều cao
                merged = (merged[0], 0, merged[2], ph)

        padded = _pad_bbox(merged, pw, ph)
        parts.append(Region(page_index=page_idx, bbox=padded))

    return MultiRegion(parts=parts)


def _check_continuity(questions: list[Question]) -> None:
    """Kiểm tra tính liên tục số câu, đánh dấu needs_review nếu nhảy."""
    if not questions:
        return

    sorted_q = sorted(questions, key=lambda q: q.number)
    for i in range(len(sorted_q) - 1):
        curr = sorted_q[i].number
        next_num = sorted_q[i + 1].number
        if next_num != curr + 1:
            # Đánh dấu các câu quanh chỗ đứt
            sorted_q[i].needs_review = True
            sorted_q[i + 1].needs_review = True
            logger.warning(
                f"Số câu không liên tục: {curr} → {next_num} "
                f"(thiếu {', '.join(str(n) for n in range(curr + 1, next_num))})"
            )


def parse_exam_metadata(
    anchors: list[Anchor],
    blocks_per_page: list[list[Block]],
    n_questions: int,
) -> ExamMetadata:
    """Public API để parse metadata — gọi từ CLI."""
    return _parse_metadata(anchors, blocks_per_page, n_questions)
