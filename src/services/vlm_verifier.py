"""Stage 7: VLM Verifier — orchestrator lazy: chọn câu → gọi VLM → merge kết quả.

Triết lý: VLM là tầng ngữ nghĩa bổ sung, KHÔNG thay thế Phase 2.
Chỉ gọi cho câu cần review / mơ hồ. Fail-safe: lỗi → giữ Phase 2.

Usage:
    from src.services.vlm_verifier import verify_exam
    n_calls = verify_exam(exam, layouts, group_layouts, images, out_dir)
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional

from PIL import Image

from src.core.config import settings
from src.core.logging import logger
from src.schemas.exam import (
    Answer,
    CroppedImage,
    Exam,
    Question,
    QuestionType,
)
from src.schemas.vlm import VLMQuestionResult, VLMQuestionType
from src.services.vlm_client import analyze_question_async, _get_vlm_logger
from src.services.snake_walker import MultiRegion, QuestionLayout, Region


# ============================================================
# VLM → QuestionType mapping
# ============================================================

_VLM_TYPE_MAP: dict[VLMQuestionType, QuestionType] = {
    VLMQuestionType.MCQ_SINGLE: QuestionType.MCQ_SINGLE,
    VLMQuestionType.MCQ_MULTI: QuestionType.MCQ_MULTI,
    VLMQuestionType.TRUE_FALSE: QuestionType.TRUE_FALSE,
    VLMQuestionType.FILL_BLANK: QuestionType.FILL_BLANK,
    VLMQuestionType.SHORT_ANSWER: QuestionType.SHORT_ANSWER,
    VLMQuestionType.ESSAY: QuestionType.ESSAY,
    VLMQuestionType.READING_COMPREHENSION: QuestionType.READING_COMPREHENSION,
    VLMQuestionType.UNKNOWN: QuestionType.UNKNOWN,
}


# ============================================================
# Lazy gating (§3) — chọn câu cần gọi VLM
# ============================================================

def select_questions(exam: Exam) -> list[Question]:
    """Chọn câu cần gọi VLM theo điều kiện lazy gating.

    Gọi khi BẤT KỲ:
    1. needs_review == True
    2. type == UNKNOWN (và use_vlm_type_classify)
    3. MCQ nhưng len(answers) not in (0, 4) — số đáp án bất thường
    4. confidence < 0.6
    """
    selected: list[Question] = []

    for q in exam.questions:
        reasons: list[str] = []

        if q.needs_review:
            reasons.append("needs_review")

        if q.type == QuestionType.UNKNOWN and settings.use_vlm_type_classify:
            reasons.append("type=UNKNOWN")

        if q.type in (QuestionType.MCQ_SINGLE, QuestionType.MCQ_MULTI,
                       QuestionType.READING_COMPREHENSION):
            n_ans = len(q.answers)
            if n_ans not in (0, 4):
                reasons.append(f"n_answers={n_ans}≠4")

        if q.confidence < 0.6:
            reasons.append(f"confidence={q.confidence:.2f}")

        if reasons:
            selected.append(q)
            logger.debug(f"VLM gating: q{q.number} → selected ({', '.join(reasons)})")

    logger.info(f"VLM gating: {len(selected)}/{len(exam.questions)} câu sẽ gọi VLM")
    return selected


# ============================================================
# Merge VLM result → Question
# ============================================================

def _merge_result(q: Question, result: VLMQuestionResult) -> dict:
    """Merge kết quả VLM vào Question (mutate tại chỗ).

    Nguyên tắc: chỉ ghi đè khi VLM tự tin hơn / bổ sung thiếu.
    KHÔNG ghi đè khi VLM thấy ÍT hơn Phase 2.

    Returns:
        dict thống kê: {answers_added, type_changed, recrop_needed, review_cleared}
    """
    stats = {
        "answers_added": 0,
        "type_changed": False,
        "recrop_needed": False,
        "review_cleared": False,
    }

    # --- Type ---
    vlm_type = _VLM_TYPE_MAP.get(result.question_type, QuestionType.UNKNOWN)
    if vlm_type != QuestionType.UNKNOWN:
        if q.type == QuestionType.UNKNOWN or q.type != vlm_type:
            old_type = q.type.value
            q.type = vlm_type
            stats["type_changed"] = True
            logger.debug(f"q{q.number}: type {old_type} → {vlm_type.value} (VLM)")

    # --- Flags (OR logic: bật nếu VLM thấy) ---
    if result.has_figure:
        q.has_figure = True
    if result.has_formula:
        q.has_formula = True
    if result.has_table:
        q.has_table = True

    # --- Content text (bổ sung nếu Phase 2 rỗng/ngắn hơn) ---
    if result.content_text:
        if not q.content_text or len(result.content_text) > len(q.content_text) * 1.2:
            q.content_text = result.content_text

    # --- Đáp án (quan trọng nhất) ---
    existing_labels = {a.label.upper() for a in q.answers}
    if result.n_answers > len(q.answers):
        # VLM thấy nhiều hơn → bổ sung thiếu
        for vlm_ans in result.answers:
            label = vlm_ans.label.upper()
            if label not in existing_labels:
                q.answers.append(Answer(
                    label=label,
                    text=vlm_ans.text,
                    image=None,  # chưa có crop pixel riêng — ảnh full_image đã chứa
                ))
                existing_labels.add(label)
                stats["answers_added"] += 1
                logger.debug(f"q{q.number}: thêm đáp án {label} từ VLM (text='{vlm_ans.text[:40]}')")

        if stats["answers_added"] > 0:
            q.needs_review = True  # đánh dấu để người duyệt biết
            # VLM bổ sung được đáp án ⇒ crop hiện tại gần như CHẮC CHẮN thiếu đáp án đó
            # (OCR/Phase 2 cắt sót). Tín hiệu này ĐÁNG TIN hơn content_complete của VLM
            # (vốn mù với phần đã bị cắt khỏi ảnh) → ép re-crop band rộng để ảnh mới chứa đủ.
            stats["recrop_needed"] = True

        # Sort lại answers theo label
        q.answers.sort(key=lambda a: a.label.upper())

    elif result.n_answers < len(q.answers):
        # VLM thấy ÍT hơn → có thể nhìn nhầm, giữ Phase 2, gắn review
        q.needs_review = True
        logger.debug(
            f"q{q.number}: VLM thấy {result.n_answers} đáp án < Phase 2 ({len(q.answers)}) → giữ Phase 2"
        )

    # --- Xác nhận / gỡ review ---
    if (result.n_answers == len(q.answers)
        and vlm_type != QuestionType.UNKNOWN
        and result.content_complete
        and result.figure_complete):
        # VLM xác nhận đúng → có thể gỡ needs_review
        if q.needs_review and stats["answers_added"] == 0:
            q.needs_review = False
            stats["review_cleared"] = True
            logger.debug(f"q{q.number}: VLM xác nhận OK → gỡ needs_review")

    # --- Region repair signal ---
    if not result.content_complete or (result.has_figure and not result.figure_complete):
        stats["recrop_needed"] = True
        q.needs_review = True
        logger.info(
            f"q{q.number}: VLM báo content/figure bị cắt → cần re-crop"
        )

    return stats


# ============================================================
# Re-crop (§6.4 — mở rộng vùng full-width band)
# ============================================================

def _recrop_fullwidth(
    q: Question,
    layouts: dict[str, QuestionLayout],
    regions_by_page: dict[int, list[tuple[float, float]]],
    images: list[Image.Image],
    out_dir: Path,
    page_widths: list[float],
    page_heights: list[float],
) -> None:
    """Re-crop câu hỏi bằng band full-width nới CHIỀU DỌC tới câu trước/sau.

    Lý do nới dọc: triệu chứng "thiếu đáp án / đồ thị / tử số bị cắt" hầu hết là vết
    cắt THEO CHIỀU DỌC. Chỉ mở chiều ngang (bản cũ) không cứu được. Ở đây ta nới:
      - mép trên LÊN tới đáy câu liền trước trên cùng trang (max bottom của vùng nằm trên).
      - mép dưới XUỐNG tới đỉnh câu liền sau trên cùng trang (min top của vùng nằm dưới).
    KHÔNG có hàng xóm theo hướng nào → GIỮ NGUYÊN biên hướng đó (không fallback nới mù):
    fallback cũ (12% chiều cao trang) nuốt phải header/chỉ dẫn nằm trên câu đầu trang.

    Dùng lại hàm crop của Phase 2 (cropper module).
    """
    from src.services.cropper import _make_cropped_image

    layout = layouts.get(q.id)
    if not layout or not layout.full.parts:
        return

    GAP = 4.0           # chừa vài px để không nuốt chữ của câu hàng xóm

    new_parts: list[Region] = []
    for part in layout.full.parts:
        pw = page_widths[part.page_index] if part.page_index < len(page_widths) else 2550.0
        ph = page_heights[part.page_index] if part.page_index < len(page_heights) else 3300.0
        x1, y1, x2, y2 = part.bbox

        others = regions_by_page.get(part.page_index, [])
        # Hàng xóm xác định theo VỊ TRÍ TƯƠNG ĐỐI của ĐỈNH (y_top), KHÔNG theo đáy —
        # vì Phase 2 hay cho vùng các câu CHỒNG LẤN nhau (đáy câu trên thò qua đỉnh câu
        # dưới). Nếu lọc theo đáy thì câu kề bị loại, thuật toán nhảy lên câu xa → nuốt câu.
        #
        # above = đáy của mọi vùng có ĐỈNH nằm trên đỉnh câu này (loại trừ chính nó).
        #   Lấy đáy THẤP NHẤT làm sàn. Nếu câu trên chồng xuống quá y1 → sàn > y1
        #   ⇒ min(...,y1) ép new_y1=y1 ⇒ KHÔNG nới lên (an toàn, không nuốt câu trên).
        above = [b for (a, b) in others if a < y1 - 1.0]
        # below = đỉnh của mọi vùng có ĐÁY nằm dưới đáy câu này → trần để nới xuống.
        below = [a for (a, b) in others if b > y2 + 1.0]

        new_y1 = (max(above) + GAP) if above else y1
        new_y2 = (min(below) - GAP) if below else y2

        # Chỉ NỚI RỘNG, không bao giờ thu hẹp; kẹp trong trang.
        new_y1 = max(0.0, min(new_y1, y1))
        new_y2 = min(ph, max(new_y2, y2))

        new_bbox = (0.0, new_y1, pw, new_y2)
        new_parts.append(Region(page_index=part.page_index, bbox=new_bbox))

    new_region = MultiRegion(parts=new_parts)

    # Crop lại
    crops_dir = out_dir / "crops"
    full_path = crops_dir / f"q{q.number}_full.png"
    new_image = _make_cropped_image(
        new_region, images, full_path, f"crops/q{q.number}_full.png"
    )

    if new_image:
        q.full_image = new_image
        # Cập nhật layout
        layout.full = new_region
        logger.info(f"q{q.number}: re-crop full-width → {new_image.width}x{new_image.height}")


# ============================================================
# Re-slice đáp án MCQ 1 hàng ngang (gap-detection)
# ============================================================

def _reslice_row_answers(
    q: Question,
    layout: QuestionLayout,
    images: list[Image.Image],
    out_dir: Path,
    page_widths: list[float],
) -> bool:
    """Cắt lại ảnh từng đáp án cho MCQ xếp 1 HÀNG NGANG bằng chia cột theo KHOẢNG TRẮNG.

    Vì sao cần: OCR đề toán tách rời marker ("B.") khỏi công thức → crop đáp án Phase 2
    chỉ còn mỗi chữ "B." (55px), hoặc marker mất hẳn (A/C) → không có ảnh. Dựa vào MARKER
    để cắt là không đáng tin. Thay vào đó:
      1. Xác định BAND đáp án (y từ hàng marker → đáy vùng câu) và trang chứa.
      2. Chiếu band lên trục x, tìm các CỘT TRẮNG (ink == 0) = khe giữa các đáp án.
      3. Lấy N-1 khe RỘNG NHẤT (N = số đáp án VLM xác nhận) làm ranh giới cột.
      4. Crop mỗi cột → 1 đáp án (đầy đủ công thức), kể cả đáp án OCR bỏ sót.

    Chỉ áp dụng cho layout 1 HÀNG (marker cùng mức y). Lưới 2x2 → trả False (giữ nguyên).

    Returns: True nếu đã cắt lại, False nếu bỏ qua (không đủ điều kiện / không tách được).
    """
    try:
        import numpy as np
    except ImportError:
        logger.warning("numpy không có → bỏ qua re-slice đáp án")
        return False
    from collections import Counter

    from src.services.cropper import _make_cropped_image

    if not layout or not layout.answers:
        return False

    # Marker đã detect (kể cả bare marker) — đáng tin về VỊ TRÍ, không tin về NỘI DUNG.
    marks = [
        (lbl, mreg.parts[0].page_index, mreg.parts[0].bbox)
        for lbl, mreg in layout.answers if mreg.parts
    ]
    if len(marks) < 2:
        return False

    page = Counter(pi for _, pi, _ in marks).most_common(1)[0][0]
    pm = [b for _, pi, b in marks if pi == page]
    ytops = [b[1] for b in pm]
    ybots = [b[3] for b in pm]
    heights = sorted(b[3] - b[1] for b in pm)
    med_h = heights[len(heights) // 2] or 30.0

    # Chỉ xử lý 1 HÀNG: các marker phải nằm cùng mức y (chênh ≤ 2 line-height).
    if (max(ybots) - min(ytops)) > 2.0 * med_h:
        return False

    N = len(q.answers)
    if N < 2 or page >= len(images):
        return False

    # Band đáp án bị kẹp trong VÙNG FULL của câu (đã re-crop nới dọc) để không lấn câu kế;
    # mép trên chừa 1.5 line phía trên marker để KHÔNG cắt tử số phân số / mũ nằm cao hơn.
    full_pg = [p.bbox for p in layout.full.parts if p.page_index == page]
    full_top = min(b[1] for b in full_pg) if full_pg else 0.0
    full_bot = max(b[3] for b in full_pg) if full_pg else float(images[page].height)
    band_y0 = max(full_top, min(ytops) - 1.5 * med_h)
    band_y1 = full_bot

    arr = np.asarray(images[page].convert("L"))
    H, W = arr.shape
    y0i, y1i = int(band_y0), int(min(band_y1, H))
    if y1i - y0i < 5:
        return False

    ink = (arr[y0i:y1i, :] < 128).sum(axis=0)  # số pixel mực mỗi cột
    empty = ink == 0

    # Gom các run cột trắng (gap)
    gaps: list[tuple[int, int]] = []
    i = 0
    while i < W:
        if empty[i]:
            j = i
            while j < W and empty[j]:
                j += 1
            gaps.append((i, j))
            i = j
        else:
            i += 1

    internal = [(a, b) for (a, b) in gaps if a > 0 and b < W]
    if len(internal) < N - 1:
        return False  # không tách đủ N cột → giữ nguyên

    # N-1 khe rộng nhất → ranh giới cột (giữa khe)
    internal.sort(key=lambda g: -(g[1] - g[0]))
    chosen = sorted(internal[: N - 1])
    bounds = [0] + [(a + b) // 2 for (a, b) in chosen] + [W]
    cols = [(bounds[k], bounds[k + 1]) for k in range(N)]

    # Crop từng cột → gán cho đáp án theo thứ tự nhãn (đã sort A,B,C,D ↔ trái→phải)
    q.answers.sort(key=lambda a: a.label.upper())
    crops_dir = out_dir / "crops"
    pw = page_widths[page] if page < len(page_widths) else float(W)
    for k, ans in enumerate(q.answers):
        cx0, cx1 = cols[k]
        cx1 = min(float(cx1), pw)
        region = MultiRegion(parts=[Region(
            page_index=page, bbox=(float(cx0), band_y0, float(cx1), band_y1)
        )])
        path = crops_dir / f"q{q.number}_{ans.label}.png"
        ci = _make_cropped_image(
            region, images, path, f"crops/q{q.number}_{ans.label}.png"
        )
        if ci:
            ans.image = ci

    logger.info(f"q{q.number}: re-slice {N} đáp án theo cột (band y={y0i}-{y1i})")
    return True


# ============================================================
# MAIN: verify_exam
# ============================================================

def verify_exam(
    exam: Exam,
    layouts: dict[str, QuestionLayout],
    group_layouts: dict[str, MultiRegion],
    images: list[Image.Image],
    out_dir: Path,
) -> int:
    """VLM Verifier — chọn câu, gọi VLM, merge kết quả, re-crop nếu cần.

    Mutate exam tại chỗ. Fail-safe: lỗi VLM → giữ Phase 2.

    Args:
        exam: Exam object (sẽ mutate).
        layouts: question layouts từ snake_walker.
        group_layouts: group layouts.
        images: page images (cho re-crop).
        out_dir: thư mục output.

    Returns:
        Số lượng VLM calls thực hiện.
    """
    vlm_log = _get_vlm_logger(out_dir)

    # Check kill switch
    if not settings.use_vlm_verification:
        logger.info("VLM verification disabled (use_vlm_verification=False)")
        return 0

    # Lazy gating
    selected = select_questions(exam)
    if not selected:
        logger.info("VLM: không có câu nào cần verify")
        return 0

    # Page dimensions cho re-crop
    page_widths = [float(img.width) for img in images]
    page_heights = [float(img.height) for img in images]

    # Map page_index → list (y_top, y_bottom) của TẤT CẢ vùng câu (full region),
    # để re-crop biết biên câu trước/sau mà nới chiều dọc cho đúng.
    regions_by_page: dict[int, list[tuple[float, float]]] = {}
    for ql in layouts.values():
        for part in ql.full.parts:
            regions_by_page.setdefault(part.page_index, []).append(
                (part.bbox[1], part.bbox[3])
            )

    # Batch async với semaphore (giới hạn 2 calls đồng thời)
    n_calls = 0
    total_stats = {
        "answers_added": 0,
        "type_changed": 0,
        "recrop": 0,
        "reslice": 0,
        "review_cleared": 0,
        "errors": 0,
    }

    async def _process_batch():
        nonlocal n_calls
        sem = asyncio.Semaphore(2)  # 32B model nặng GPU

        async def _process_one(q: Question):
            nonlocal n_calls
            async with sem:
                # Tìm ảnh crop
                crops_dir = out_dir / "crops"
                full_path = crops_dir / f"q{q.number}_full.png"
                content_path = crops_dir / f"q{q.number}_content.png"

                image_path = full_path if full_path.exists() else content_path
                if not image_path.exists():
                    vlm_log.warning(f"q{q.number}: không có ảnh crop → bỏ qua VLM")
                    return

                try:
                    result = await analyze_question_async(
                        image_path=image_path,
                        q_number=q.number,
                        q_type=q.type.value,
                        n_ans=len(q.answers),
                        out_dir=out_dir,
                    )
                    n_calls += 1

                    if result is None:
                        total_stats["errors"] += 1
                        q.needs_review = True
                        return

                    # Merge
                    stats = _merge_result(q, result)
                    total_stats["answers_added"] += stats["answers_added"]
                    if stats["type_changed"]:
                        total_stats["type_changed"] += 1
                    if stats["review_cleared"]:
                        total_stats["review_cleared"] += 1

                    # Re-crop full nếu cần (mở band dọc)
                    if stats["recrop_needed"]:
                        try:
                            _recrop_fullwidth(q, layouts, regions_by_page, images,
                                             out_dir, page_widths, page_heights)
                            total_stats["recrop"] += 1
                        except Exception as e:
                            logger.error(f"q{q.number}: lỗi re-crop — {e}")

                    # Re-slice đáp án cho MCQ 1 hàng (sau re-crop để dùng band full đã nới).
                    # Chạy khi crop đáp án Phase 2 không đáng tin: có đáp án thiếu ảnh
                    # (VLM thêm text) HOẶC vừa bổ sung đáp án. Layout 2x2 sẽ tự bỏ qua.
                    if q.type in (QuestionType.MCQ_SINGLE, QuestionType.MCQ_MULTI,
                                   QuestionType.READING_COMPREHENSION):
                        need_reslice = (
                            stats["answers_added"] > 0
                            or any(a.image is None for a in q.answers)
                        )
                        if need_reslice and len(q.answers) >= 2:
                            try:
                                layout = layouts.get(q.id)
                                if layout and _reslice_row_answers(
                                    q, layout, images, out_dir, page_widths
                                ):
                                    total_stats["reslice"] += 1
                            except Exception as e:
                                logger.error(f"q{q.number}: lỗi re-slice đáp án — {e}")

                except Exception as e:
                    logger.error(f"q{q.number}: VLM exception — {e}")
                    total_stats["errors"] += 1
                    q.needs_review = True

        tasks = [_process_one(q) for q in selected]
        await asyncio.gather(*tasks)

    # Run batch
    try:
        asyncio.run(_process_batch())
    except RuntimeError:
        # Đã có event loop chạy → dùng thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            pool.submit(asyncio.run, _process_batch()).result()

    # Cập nhật exam stats
    exam.avg_confidence = (
        sum(q.confidence for q in exam.questions) / len(exam.questions)
        if exam.questions else 0.0
    )
    exam.n_mcq = sum(1 for q in exam.questions if q.type.value in (
        "trac_nghiem_1_dap_an", "trac_nghiem_nhieu_dap_an", "doc_hieu", "dung_sai"
    ))
    exam.n_essay = sum(1 for q in exam.questions if q.type.value in (
        "tu_luan_dai", "tu_luan_ngan", "dien_dap_an"
    ))

    # Log summary
    logger.info(
        f"VLM Verify done: {n_calls} calls, "
        f"+{total_stats['answers_added']} answers, "
        f"{total_stats['type_changed']} type changes, "
        f"{total_stats['recrop']} re-crops, "
        f"{total_stats['reslice']} re-slice đáp án, "
        f"{total_stats['review_cleared']} reviews cleared, "
        f"{total_stats['errors']} errors"
    )
    vlm_log.info(
        f"SUMMARY: {n_calls} calls, +{total_stats['answers_added']} answers, "
        f"{total_stats['type_changed']} types, {total_stats['recrop']} re-crops, "
        f"{total_stats['reslice']} re-slice, {total_stats['errors']} errors"
    )

    return n_calls
