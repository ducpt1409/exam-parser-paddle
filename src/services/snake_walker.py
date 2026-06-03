"""Stage 4: Snake Walker — gom Anchor → Question/Group theo luồng "con rắn" liên trang.

Ý tưởng: coi toàn bộ document như 1 dải liên tục nối các trang lại (global position).
Sort anchor theo (page_index, y_top), xác định ranh giới mỗi câu, gom LINES + answers,
tạo Group, phát hiện phần lời giải (Azota) để loại bỏ, parse metadata.

[Phase 2.4] Refactor: chuyển từ BLOCK granularity sang LINE granularity.
Nguyên nhân: layout model 'en' gom cả trang thành 1 block figure →
block.bbox vô nghĩa, nhưng line.bbox (từ OCR) luôn chính xác.

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
# PositionedLine — đơn vị làm việc chính (Bug 1 fix)
# ============================================================

@dataclass
class PositionedLine:
    """1 dòng OCR + metadata block cha. Đơn vị cơ bản của snake walker.

    Dùng line.bbox thay vì block.bbox vì layout model 'en' hay gom
    cả trang thành 1 block figure — block.bbox vô nghĩa, nhưng
    line.bbox (từ OCR engine) luôn chính xác.
    """
    page_index: int
    bbox: BBox                 # bbox CỦA LINE (chính xác)
    text: str
    block_type: BlockType      # type của block cha (để biết figure/table/equation)
    confidence: float = 1.0


# ============================================================
# Constants
# ============================================================

PAD = 8  # Padding (px) khi tính bbox vùng

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
    r"^\d+\s*[\.\\)]\s*[A-D]\s+\d+\s*[\.\\)]\s*[A-D]",  # 1.A 2.B ...
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


def _gpos_of_line(ln: PositionedLine) -> tuple[int, float]:
    """Global position của 1 line."""
    return (ln.page_index, ln.bbox[1])


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
# Bug 1 fix: Flatten blocks → lines
# ============================================================

def _flatten_lines(blocks_per_page: list[list[Block]]) -> list[PositionedLine]:
    """Bung mọi block thành list line, mỗi line giữ bbox riêng + type block cha.

    Đây là bước then chốt: layout model hay gom cả trang thành 1 block,
    nhưng OCR line bbox luôn chính xác → dùng line làm đơn vị gom.
    """
    out: list[PositionedLine] = []
    for page_blocks in blocks_per_page:
        for blk in page_blocks:
            if not blk.lines:
                # Block visual thuần ảnh (không có text) — tạo 1 line placeholder
                # để giữ bbox (cho has_figure detection)
                out.append(PositionedLine(
                    page_index=blk.page_index,
                    bbox=blk.bbox,
                    text="",
                    block_type=blk.type,
                    confidence=blk.confidence,
                ))
            else:
                for ln in blk.lines:
                    out.append(PositionedLine(
                        page_index=blk.page_index,
                        bbox=ln.bbox,
                        text=ln.text,
                        block_type=blk.type,
                        confidence=ln.confidence,
                    ))
    out.sort(key=lambda l: (l.page_index, l.bbox[1]))
    return out


# ============================================================
# Phát hiện & loại phần lời giải (Azota edge case §3.6b)
# ============================================================

def _find_solution_boundary(
    anchors: list[Anchor],
    all_lines: list[PositionedLine],
) -> Optional[tuple[int, float]]:
    """Tìm global position bắt đầu phần lời giải.

    Trả về None nếu không phát hiện (đề bình thường).
    """
    # Cách 1: Tìm marker text trong lines
    for ln in all_lines:
        text = ln.text.strip()
        if not text:
            continue
        norm = _strip_accents(text).lower().strip()

        # Check marker lời giải
        for marker in _SOLUTION_MARKERS:
            if marker in norm:
                pos = _gpos_of_line(ln)
                logger.info(f"Phát hiện marker lời giải: \"{text}\" tại page={pos[0]}, y={pos[1]:.0f}")
                return pos

        # Check "hết" / bảng đáp án
        if _END_MARKERS_REGEX.match(norm):
            pos = _gpos_of_line(ln)
            logger.info(f"Phát hiện marker kết thúc: \"{text}\" tại page={pos[0]}, y={pos[1]:.0f}")
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

    meta_texts: list[str] = []
    for a in anchors:
        if a.type == AnchorType.METADATA:
            meta_texts.append(a.text)

    if blocks_per_page:
        for block in blocks_per_page[0][:10]:
            meta_texts.append(block.text)

    full_text = "\n".join(meta_texts)
    norm = _strip_accents(full_text).lower()

    m = re.search(r"ma\s+de[:\s]*(\d+)", norm)
    if m:
        meta.ma_de = m.group(1)

    m = re.search(r"thoi\s+gian[:\s]*(\d+)\s*(?:phut)?", norm)
    if m:
        meta.thoi_gian_phut = int(m.group(1))

    m = re.search(r"mon[:\s]+([^\n\r]+)", norm)
    if m:
        raw_m = re.search(r"[Mm][oôơ]n[:\s]+([^\n\r]+)", full_text)
        if raw_m:
            meta.mon = raw_m.group(1).strip()
        else:
            meta.mon = m.group(1).strip()

    for pattern in [r"truong\s+(?:thpt|thcs|th)[:\s]*([^\n\r]+)",
                    r"so\s+gd[:\s]*([^\n\r]+)"]:
        m = re.search(pattern, norm)
        if m:
            meta.truong = m.group(1).strip()
            break

    m = re.search(r"nam\s+hoc[:\s]*(\d{4}\s*[-–]\s*\d{4})", norm)
    if m:
        meta.nam_hoc = m.group(1).strip()

    return meta


# ============================================================
# Bug 3 fix: Clip vùng đáp án cuối — không kéo tới câu sau
# ============================================================

def _y_overlap_ratio(b1: BBox, b2: BBox) -> float:
    """Tỷ lệ overlap theo trục Y (so với chiều cao box thấp hơn)."""
    lo = max(b1[1], b2[1])
    hi = min(b1[3], b2[3])
    if hi <= lo:
        return 0.0
    shorter = max(1.0, min(b1[3] - b1[1], b2[3] - b2[1]))
    return (hi - lo) / shorter


def _compute_effective_starts(
    q_anchors: list[Anchor],
    all_lines: list[PositionedLine],
) -> list[float]:
    """Tính 'effective start y' cho từng câu — fix reading-order (Issue C).

    Vấn đề: nội dung toán (phân số, lũy thừa) render CAO HƠN dòng chữ "Câu N",
    nên y_top của chúng < y_top anchor → bị gán nhầm sang câu TRƯỚC.

    Heuristic: mở rộng start của câu LÊN TRÊN để bao các line cùng trang mà:
      - y-overlap với dòng anchor >= 40% (cùng hàng thị giác), VÀ
      - nằm bên phải dòng anchor (center x > center x anchor) → là phần đuôi
        của chính dòng "Câu N" (vd phân số sau chữ "phương trình"), VÀ
      - y_top nhỏ hơn y_top anchor (nằm phía trên).

    Trả về list y aligned với q_anchors. Câu thường (không có gì overlap) →
    giữ nguyên anchor.bbox[1] → KHÔNG ảnh hưởng đề text bình thường (Anh văn).
    """
    starts: list[float] = []
    for q in q_anchors:
        a = q.bbox
        anchor_cx = (a[0] + a[2]) / 2.0
        min_y = a[1]
        for ln in all_lines:
            if ln.page_index != q.page_index:
                continue
            if ln.bbox == a:
                continue
            if (
                ln.bbox[1] < a[1]
                and _y_overlap_ratio(ln.bbox, a) >= 0.4
                and ((ln.bbox[0] + ln.bbox[2]) / 2.0) > anchor_cx
            ):
                min_y = min(min_y, ln.bbox[1])
        starts.append(min_y)
    return starts


# Marker KẾT THÚC ĐỀ (phần câu hỏi) — KHÁC footer số trang.
# Dùng để clip câu CUỐI, tránh nuốt "Hết / Chúc làm bài / Đáp án / bảng đáp án".
_CONTENT_END_REGEX = re.compile(
    r"^-*\s*het\s*-*$|"                      # ---hết---
    r"^bang\s+dap\s+an|"                     # bảng đáp án
    r"^dap\s*an\s*$|"                         # "Đáp án" đứng riêng (header)
    r"chuc\s+.*lam\s+bai|"                   # chúc ... làm bài tốt
    r"^\d+\s*[\.\)]\s*[A-D]\b.*\d+\s*[\.\)]\s*[A-D]",  # bảng "1.C 2.B 3.D ..."
    re.IGNORECASE,
)


def _find_content_end(
    all_lines: list[PositionedLine],
    after_pos: tuple[int, float],
) -> Optional[tuple[int, float]]:
    """Tìm vị trí kết thúc phần đề (sau câu cuối) để clip — Issue D.

    Trả về global position của marker kết thúc đầu tiên SAU after_pos,
    hoặc None nếu không có (câu cuối kéo tới hết document).
    """
    for ln in all_lines:
        if (ln.page_index, ln.bbox[1]) <= after_pos:
            continue
        norm = _strip_accents(ln.text.strip()).lower().strip()
        if norm and _CONTENT_END_REGEX.search(norm):
            return (ln.page_index, ln.bbox[1])
    return None


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

    [Phase 2.4] Refactored: dùng LINE granularity thay BLOCK granularity.
    """
    last_page = len(page_heights) - 1
    inf_y = float("inf")

    # ================================================================
    # Bước 0a — Flatten blocks → lines (Bug 1 fix)
    # ================================================================
    all_lines = _flatten_lines(blocks_per_page)
    logger.info(f"Snake Walker: flatten {sum(len(b) for b in blocks_per_page)} blocks → {len(all_lines)} lines")

    # ================================================================
    # Bước 0b — Phát hiện & loại phần lời giải (Azota §3.6b)
    # ================================================================
    solution_boundary = _find_solution_boundary(anchors, all_lines)

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

    logger.info(
        f"Snake Walker input: {len(q_anchors)} câu, {len(answer_anchors)} đáp án, "
        f"{len(sub_anchors)} sub, {len(group_anchors)} group, "
        f"{len(all_lines)} lines, {len(page_heights)} trang"
    )

    # Effective start y cho từng câu (fix reading-order Issue C) — aligned q_anchors
    eff_starts = _compute_effective_starts(q_anchors, all_lines)

    # Vị trí kết thúc phần đề (clip câu cuối — Issue D), tìm sau câu cuối cùng
    content_end: Optional[tuple[int, float]] = None
    if q_anchors:
        content_end = _find_content_end(all_lines, _gpos_of_anchor(q_anchors[-1]))
        if content_end:
            logger.info(f"Content-end (clip câu cuối) tại page={content_end[0]}, y={content_end[1]:.0f}")

    # ================================================================
    # Bước 2+3 — Xác định ranh giới mỗi câu & gom LINES vào câu
    # ================================================================
    questions: list[Question] = []
    layouts: dict[str, QuestionLayout] = {}

    seen_numbers: set[int] = set()
    prev_number: Optional[int] = None

    for i, q_anchor in enumerate(q_anchors):
        q_num = q_anchor.number
        if q_num is None:
            logger.warning(f"Question anchor không có number: \"{q_anchor.text}\" → bỏ qua")
            continue

        q_id = f"q{q_num}"
        # Start = effective start (mở rộng lên trên cho phân số toán — Issue C)
        start = (q_anchor.page_index, eff_starts[i])

        # End = effective start câu kế tiếp; câu cuối → content_end (Issue D) hoặc hết document
        if i + 1 < len(q_anchors):
            end = (q_anchors[i + 1].page_index, eff_starts[i + 1])
        else:
            end = content_end if content_end is not None else (last_page, inf_y)

        needs_review = False

        # Trùng số câu → bỏ qua (giữ cái đầu)
        if q_num in seen_numbers:
            logger.warning(f"Trùng số câu: Câu {q_num}")
            continue
        seen_numbers.add(q_num)

        # Số không liên tục
        if prev_number is not None and q_num > prev_number + 1:
            logger.warning(f"Số câu không liên tục: nhảy từ {prev_number} sang {q_num}")
            needs_review = True
        prev_number = q_num

        # --- Gom LINES vào câu (Bug 1: dùng line bbox thay block bbox) ---
        q_lines: list[PositionedLine] = [
            ln for ln in all_lines
            if _in_range(_gpos_of_line(ln), start, end)
        ]

        if not q_lines:
            logger.warning(f"Câu {q_num}: không có line nào → needs_review")
            needs_review = True

        # Page indices
        page_indices = sorted(set(ln.page_index for ln in q_lines)) if q_lines else [q_anchor.page_index]

        # Content text + flags (dùng line text & block_type)
        content_parts: list[str] = []
        has_figure = False
        has_table = False
        has_formula = False

        for ln in q_lines:
            # has_figure: chỉ set nếu line rỗng text thuộc block figure
            # (figure thật không có text, trang gom figure giả có text)
            if ln.block_type == BlockType.FIGURE and not ln.text.strip():
                has_figure = True
            elif ln.block_type == BlockType.TABLE and not ln.text.strip():
                has_table = True
            elif ln.block_type == BlockType.EQUATION:
                has_formula = True
            if ln.text.strip():
                content_parts.append(ln.text.strip())

        # --- Gom answers vào câu ---
        q_answers_anchors: list[Anchor] = [
            a for a in answer_anchors
            if _in_range(_gpos_of_anchor(a), start, end)
        ]
        q_sub_anchors: list[Anchor] = [
            a for a in sub_anchors
            if _in_range(_gpos_of_anchor(a), start, end)
        ]

        # Tạo Answer objects (loại trùng label)
        answers: list[Answer] = []
        seen_labels: set[str] = set()
        for a_anchor in q_answers_anchors:
            label = a_anchor.value or ""
            if label in seen_labels:
                logger.warning(f"Câu {q_num}: trùng label đáp án '{label}' → needs_review")
                needs_review = True
                continue
            seen_labels.add(label)
            answers.append(Answer(label=label, text=a_anchor.text.strip()))

        # ================================================================
        # Bước 4 — Tách content vs answers region (cho crop)
        # ================================================================
        first_answer_pos: Optional[tuple[int, float]] = None
        if q_answers_anchors:
            first_answer_pos = _gpos_of_anchor(q_answers_anchors[0])
        elif q_sub_anchors:
            first_answer_pos = _gpos_of_anchor(q_sub_anchors[0])

        # --- Full region (từ lines) ---
        full_region = _compute_multi_region_from_lines(
            q_lines, start, end, page_heights, page_widths
        )

        # --- Content region ---
        if first_answer_pos:
            content_lines = [
                ln for ln in q_lines
                if _gpos_of_line(ln) < first_answer_pos
            ]
            content_region = _compute_multi_region_from_lines(
                content_lines, start, first_answer_pos, page_heights, page_widths
            )
            content_text = " ".join(
                ln.text.strip() for ln in content_lines if ln.text.strip()
            )
        else:
            content_region = full_region
            content_text = " ".join(content_parts)

        # --- Answer regions (Issue A) ---
        # FIX: đáp án trắc nghiệm VN thường nằm CÙNG HÀNG (cùng y, khác x cột),
        # nên cách cũ chia "dải y" giữa các anchor bị sai (dải rỗng → crop full-width,
        # hoặc gộp 2 đáp án). Thay bằng: GÁN mỗi line vùng-đáp-án vào anchor GẦN NHẤT
        # (theo khoảng cách tâm). Xử lý đúng mọi layout: cùng hàng, lưới 2x2, xuống dòng.
        uniq_anchors: list[Anchor] = []
        _seen_lbl: set[str] = set()
        for a_anchor in q_answers_anchors:
            lbl = a_anchor.value or ""
            if lbl in _seen_lbl:
                continue
            _seen_lbl.add(lbl)
            uniq_anchors.append(a_anchor)

        # Các line thuộc vùng đáp án (từ đáp án đầu trở đi)
        zone_lines = [
            ln for ln in q_lines
            if first_answer_pos is not None and _gpos_of_line(ln) >= first_answer_pos
        ]
        # Loại line nằm DƯỚI XA hàng đáp án cuối (vd group header "Phần II"
        # chen giữa câu này và câu sau) — tránh kéo dài vùng đáp án xuống.
        if uniq_anchors:
            ans_bottom = max(a.bbox[3] for a in uniq_anchors)
            _hs = sorted(a.bbox[3] - a.bbox[1] for a in uniq_anchors)
            line_h = _hs[len(_hs) // 2] if _hs else 50.0
            zone_lines = [ln for ln in zone_lines if ln.bbox[1] <= ans_bottom + 1.2 * line_h]

        def _center(bb: BBox) -> tuple[float, float]:
            return ((bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0)

        # Bucket bbox cho mỗi anchor (khởi tạo = bbox anchor)
        buckets: dict[int, list[BBox]] = {k: [a.bbox] for k, a in enumerate(uniq_anchors)}
        for ln in zone_lines:
            lcx, lcy = _center(ln.bbox)
            best_k, best_d = None, float("inf")
            for k, a in enumerate(uniq_anchors):
                acx, acy = _center(a.bbox)
                # phạt khác trang để ưu tiên cùng trang
                page_pen = 0.0 if a.page_index == ln.page_index else 1e6
                d = ((lcx - acx) ** 2 + (lcy - acy) ** 2) ** 0.5 + page_pen
                if d < best_d:
                    best_d, best_k = d, k
            if best_k is not None:
                buckets[best_k].append(ln.bbox)

        answer_regions: list[tuple[str, MultiRegion]] = []
        for k, a_anchor in enumerate(uniq_anchors):
            pidx = a_anchor.page_index
            pw = page_widths[pidx] if pidx < len(page_widths) else 2550
            ph = page_heights[pidx] if pidx < len(page_heights) else 3300
            merged = _merge_bboxes(buckets[k])
            padded = _pad_bbox(merged, pw, ph)
            label = a_anchor.value or f"ans{k}"
            answer_regions.append(
                (label, MultiRegion(parts=[Region(page_index=pidx, bbox=padded)]))
            )

        # --- Confidence ---
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

            # Tìm câu hỏi thuộc group
            g_question_ids: list[str] = []
            for q in questions:
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

            # Passage text: lines giữa group header và câu đầu tiên
            passage_text = ""
            passage_region = MultiRegion()

            if g_type == GroupType.PASSAGE and g_question_ids:
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
                        passage_start = _gpos(g_anchor.page_index, g_anchor.bbox[3])
                        passage_end = _gpos_of_anchor(first_q_anchor)

                        passage_lines = [
                            ln for ln in all_lines
                            if _in_range(_gpos_of_line(ln), passage_start, passage_end)
                            and ln.text.strip()  # chỉ line có text
                        ]
                        passage_text = " ".join(ln.text.strip() for ln in passage_lines)
                        passage_region = _compute_multi_region_from_lines(
                            passage_lines, passage_start, passage_end,
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

    _check_continuity(questions)

    return questions, groups, layouts, group_layouts


# ============================================================
# Helper: Compute MultiRegion từ tập LINES (Bug 1 fix)
# ============================================================

def _compute_multi_region_from_lines(
    lines: list[PositionedLine],
    start: tuple[int, float],
    end: tuple[int, float],
    page_heights: list[float],
    page_widths: list[float],
) -> MultiRegion:
    """Tính MultiRegion từ tập PositionedLine, hỗ trợ vắt trang.

    Dùng line.bbox thay block.bbox → bbox chính xác dù layout model
    gom cả trang thành 1 block.
    """
    if not lines:
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

    pages_involved = sorted(set(ln.page_index for ln in lines))

    parts: list[Region] = []
    for page_idx in pages_involved:
        page_lines = [ln for ln in lines if ln.page_index == page_idx]
        if not page_lines:
            continue

        pw = page_widths[page_idx] if page_idx < len(page_widths) else 2550
        ph = page_heights[page_idx] if page_idx < len(page_heights) else 3300

        # Tính bbox bao từ LINE bbox (chính xác)
        bboxes = [ln.bbox for ln in page_lines]
        merged = _merge_bboxes(bboxes)

        # Điều chỉnh cho vắt trang
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
