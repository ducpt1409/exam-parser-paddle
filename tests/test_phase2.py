"""Test Phase 2 logic + Bug fixes — chạy trong conda env exam_parser_paddle.

Usage:
    conda activate exam_parser_paddle
    python tests/test_phase2.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make src importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.schemas.block import Block, BlockType, TextLine, BBox
from src.schemas.anchor import Anchor, AnchorType
from src.schemas.exam import Question, QuestionType, Group, GroupType, Answer

from src.services.snake_walker import (
    snake_walk, parse_exam_metadata, _classify_group_type, _strip_accents,
    _find_solution_boundary, _in_range, _gpos, Region, MultiRegion, QuestionLayout,
    PositionedLine, _flatten_lines, _clip_last_answer_lines,
)
from src.services.question_classifier import classify, classify_all


def test_strip_accents():
    assert _strip_accents("Câu") == "Cau"
    assert _strip_accents("Đọc đoạn") == "Doc doan"
    assert _strip_accents("Phần") == "Phan"
    print("✅ test_strip_accents PASSED")


def test_classify_group_type():
    assert _classify_group_type("Read the following passage") == GroupType.PASSAGE
    assert _classify_group_type("Đọc đoạn văn sau") == GroupType.PASSAGE
    assert _classify_group_type("Phần I") == GroupType.SECTION_PART
    assert _classify_group_type("Mark the letter A, B, C or D") == GroupType.INSTRUCTION
    assert _classify_group_type("Something else") == GroupType.UNKNOWN
    print("✅ test_classify_group_type PASSED")


def test_in_range():
    assert _in_range((0, 100), (0, 50), (0, 200)) is True
    assert _in_range((0, 50), (0, 50), (0, 200)) is True
    assert _in_range((0, 200), (0, 50), (0, 200)) is False
    assert _in_range((1, 100), (0, 50), (0, 200)) is True
    assert _in_range((0, 30), (0, 50), (0, 200)) is False
    print("✅ test_in_range PASSED")


def test_classify_mcq():
    q = Question(
        id="q1", number=1, content_text="Tìm giá trị lớn nhất",
        answers=[
            Answer(label="A", text="A. 1"), Answer(label="B", text="B. 2"),
            Answer(label="C", text="C. 3"), Answer(label="D", text="D. 4"),
        ]
    )
    assert classify(q) == QuestionType.MCQ_SINGLE
    print("✅ test_classify_mcq PASSED")


def test_classify_essay():
    q = Question(id="q5", number=5, content_text="Giải các phương trình sau", answers=[])
    assert classify(q) == QuestionType.ESSAY
    print("✅ test_classify_essay PASSED")


def test_classify_reading_comprehension():
    g = Group(id="g1", type=GroupType.PASSAGE, header_text="Read the following passage")
    q = Question(
        id="q10", number=10, group_id="g1", content_text="What is the main idea?",
        answers=[
            Answer(label="A", text="A. idea 1"), Answer(label="B", text="B. idea 2"),
            Answer(label="C", text="C. idea 3"), Answer(label="D", text="D. idea 4"),
        ]
    )
    assert classify(q, g) == QuestionType.READING_COMPREHENSION
    print("✅ test_classify_reading_comprehension PASSED")


def test_classify_fill_blank():
    q = Question(id="q6", number=6, content_text="Điền vào chỗ trống: 2 + ___ = 5", answers=[])
    assert classify(q) == QuestionType.FILL_BLANK
    print("✅ test_classify_fill_blank PASSED")


def test_classify_all():
    g = Group(id="g1", type=GroupType.PASSAGE, header_text="Read the following passage")
    questions = [
        Question(id="q1", number=1, answers=[
            Answer(label="A"), Answer(label="B"), Answer(label="C"), Answer(label="D"),
        ]),
        Question(id="q2", number=2, content_text="Giải phương trình", answers=[]),
        Question(id="q3", number=3, group_id="g1", answers=[
            Answer(label="A"), Answer(label="B"), Answer(label="C"), Answer(label="D"),
        ]),
    ]
    classify_all(questions, [g])
    assert questions[0].type == QuestionType.MCQ_SINGLE
    assert questions[1].type == QuestionType.ESSAY
    assert questions[2].type == QuestionType.READING_COMPREHENSION
    print("✅ test_classify_all PASSED")


def test_flatten_lines():
    """Bug 1: Test flatten — block figure cả trang → tách từng line."""
    blocks = [
        # Block figure gom cả trang — nhưng có nhiều lines OCR bên trong
        Block(page_index=0, block_index=0, type=BlockType.FIGURE,
              bbox=(50, 46, 2500, 3273),
              lines=[
                  TextLine(text="Câu 5: Giải PT", bbox=(100, 100, 700, 140)),
                  TextLine(text="x + 1 = 0", bbox=(100, 160, 400, 200)),
                  TextLine(text="Câu 6: Trình bày", bbox=(100, 968, 700, 1008)),
              ]),
    ]
    lines = _flatten_lines([blocks])
    assert len(lines) == 3, f"Expected 3 lines, got {len(lines)}"
    # Line bbox phải là từng line, KHÔNG phải block bbox
    assert lines[0].bbox[1] == 100, f"Line 0 y_top should be 100, got {lines[0].bbox[1]}"
    assert lines[2].bbox[1] == 968, f"Line 2 y_top should be 968, got {lines[2].bbox[1]}"
    # Block type được truyền qua
    assert lines[0].block_type == BlockType.FIGURE
    print("✅ test_flatten_lines PASSED")


def test_snake_walk_line_granularity():
    """Bug 1: Test snake_walk với block figure cả trang — phải tách đúng câu."""
    # Mô phỏng: trang 2 chỉ có 1 block figure gom cả trang, chứa Câu 5,6,7
    blocks_page0 = [
        Block(page_index=0, block_index=0, type=BlockType.TEXT,
              bbox=(50, 100, 700, 150),
              lines=[TextLine(text="Câu 1: Câu hỏi TN", bbox=(50, 100, 700, 150))]),
        Block(page_index=0, block_index=1, type=BlockType.TEXT,
              bbox=(50, 160, 350, 200),
              lines=[TextLine(text="A. đáp án A", bbox=(50, 160, 350, 200))]),
        Block(page_index=0, block_index=2, type=BlockType.TEXT,
              bbox=(400, 160, 700, 200),
              lines=[TextLine(text="B. đáp án B", bbox=(400, 160, 700, 200))]),
        Block(page_index=0, block_index=3, type=BlockType.TEXT,
              bbox=(50, 210, 350, 250),
              lines=[TextLine(text="C. đáp án C", bbox=(50, 210, 350, 250))]),
        Block(page_index=0, block_index=4, type=BlockType.TEXT,
              bbox=(400, 210, 700, 250),
              lines=[TextLine(text="D. đáp án D", bbox=(400, 210, 700, 250))]),
    ]
    # Page 1: 1 block figure gom cả trang
    blocks_page1 = [
        Block(page_index=1, block_index=0, type=BlockType.FIGURE,
              bbox=(50, 46, 2500, 3273),
              lines=[
                  TextLine(text="Câu 2: Giải PT", bbox=(100, 100, 700, 140)),
                  TextLine(text="x^2 - 1 = 0", bbox=(100, 160, 400, 200)),
                  TextLine(text="Câu 3: Chứng minh", bbox=(100, 500, 700, 540)),
                  TextLine(text="Cho tam giác ABC...", bbox=(100, 560, 700, 600)),
              ]),
    ]

    anchors = [
        Anchor(page_index=0, type=AnchorType.QUESTION, bbox=(50, 100, 700, 150),
               text="Câu 1", value="1"),
        Anchor(page_index=0, type=AnchorType.ANSWER, bbox=(50, 160, 350, 200),
               text="A. đáp án A", value="A"),
        Anchor(page_index=0, type=AnchorType.ANSWER, bbox=(400, 160, 700, 200),
               text="B. đáp án B", value="B"),
        Anchor(page_index=0, type=AnchorType.ANSWER, bbox=(50, 210, 350, 250),
               text="C. đáp án C", value="C"),
        Anchor(page_index=0, type=AnchorType.ANSWER, bbox=(400, 210, 700, 250),
               text="D. đáp án D", value="D"),
        Anchor(page_index=1, type=AnchorType.QUESTION, bbox=(100, 100, 700, 140),
               text="Câu 2", value="2"),
        Anchor(page_index=1, type=AnchorType.QUESTION, bbox=(100, 500, 700, 540),
               text="Câu 3", value="3"),
    ]

    questions, groups, layouts, group_layouts = snake_walk(
        [blocks_page0, blocks_page1], anchors,
        page_heights=[3300.0, 3300.0], page_widths=[2550.0, 2550.0]
    )

    assert len(questions) == 3, f"Expected 3 questions, got {len(questions)}"
    assert questions[0].number == 1
    assert questions[1].number == 2
    assert questions[2].number == 3

    # Bug 1 key check: Câu 2 KHÔNG nuốt cả trang
    q2_layout = layouts["q2"]
    for part in q2_layout.full.parts:
        if part.page_index == 1:
            # Bbox phải ôm đúng lines 100-200, KHÔNG phải 46-3273
            assert part.bbox[1] < 150, f"Q2 y_top should be near 100, got {part.bbox[1]}"
            assert part.bbox[3] < 550, f"Q2 y_bottom should be < 550, got {part.bbox[3]}"

    # Câu 3 cũng phải có content
    assert questions[2].content_text, "Q3 should have content text"

    print("✅ test_snake_walk_line_granularity PASSED")
    print(f"   Q1: {len(questions[0].answers)} answers, pages={questions[0].page_indices}")
    print(f"   Q2: pages={questions[1].page_indices}, content='{questions[1].content_text[:40]}'")
    print(f"   Q3: pages={questions[2].page_indices}, content='{questions[2].content_text[:40]}'")


def test_clip_last_answer():
    """Bug 3: Đáp án cuối không kéo tới câu sau."""
    # Anchor D ở y=210, dòng phân số câu sau ở y=250 (lẹm vào D)
    a_anchor = Anchor(
        page_index=0, type=AnchorType.ANSWER,
        bbox=(400, 210, 700, 250), text="D. m=3", value="D"
    )
    a_lines = [
        PositionedLine(page_index=0, bbox=(400, 210, 700, 250), text="D. m=3",
                       block_type=BlockType.TEXT),
        # Dòng ngoại lai (phân số câu sau, render trước dòng "Câu N")
        PositionedLine(page_index=0, bbox=(100, 330, 400, 370), text="x+1",
                       block_type=BlockType.TEXT),
        PositionedLine(page_index=0, bbox=(100, 380, 400, 420), text="= 0 là:",
                       block_type=BlockType.TEXT),
    ]
    clipped = _clip_last_answer_lines(a_lines, a_anchor)
    assert len(clipped) == 1, f"Expected 1 line (only D), got {len(clipped)}"
    assert clipped[0].text == "D. m=3"
    print("✅ test_clip_last_answer PASSED")


def test_solution_boundary():
    """Test phát hiện ranh giới lời giải bằng fallback monotonic."""
    anchors = [
        Anchor(page_index=0, type=AnchorType.QUESTION, bbox=(50, 100, 700, 150),
               text="Câu 1", value="1"),
        Anchor(page_index=0, type=AnchorType.QUESTION, bbox=(50, 300, 700, 350),
               text="Câu 2", value="2"),
        Anchor(page_index=0, type=AnchorType.QUESTION, bbox=(50, 500, 700, 550),
               text="Câu 3", value="3"),
        Anchor(page_index=1, type=AnchorType.QUESTION, bbox=(50, 100, 700, 150),
               text="Câu 1", value="1"),
    ]
    lines = [
        PositionedLine(page_index=0, bbox=(50, 100, 700, 150), text="Câu 1",
                       block_type=BlockType.TEXT),
        PositionedLine(page_index=1, bbox=(50, 100, 700, 150), text="Câu 1",
                       block_type=BlockType.TEXT),
    ]
    boundary = _find_solution_boundary(anchors, lines)
    assert boundary is not None
    assert boundary[0] == 1
    print("✅ test_solution_boundary PASSED")


def test_metadata_parse():
    blocks = [
        Block(page_index=0, block_index=0, type=BlockType.TITLE, bbox=(50, 10, 700, 40),
              lines=[TextLine(text="SỞ GD&ĐT HÀ NỘI", bbox=(50, 10, 700, 40))]),
        Block(page_index=0, block_index=1, type=BlockType.TEXT, bbox=(50, 50, 700, 80),
              lines=[TextLine(text="Mã đề: 132", bbox=(50, 50, 700, 80))]),
        Block(page_index=0, block_index=2, type=BlockType.TEXT, bbox=(50, 90, 700, 120),
              lines=[TextLine(text="Thời gian: 60 phút", bbox=(50, 90, 700, 120))]),
    ]
    anchors = [
        Anchor(page_index=0, type=AnchorType.METADATA, bbox=(50, 50, 700, 80),
               text="Mã đề: 132"),
    ]
    meta = parse_exam_metadata(anchors, [blocks], 50)
    assert meta.tong_so_cau == 50
    print(f"   Metadata: ma_de={meta.ma_de}, thoi_gian={meta.thoi_gian_phut}")
    print("✅ test_metadata_parse PASSED")


def test_snake_walk_with_group():
    blocks = [
        Block(page_index=0, block_index=0, type=BlockType.TITLE, bbox=(50, 50, 700, 80),
              lines=[TextLine(text="Phần I. Trắc nghiệm", bbox=(50, 50, 700, 80))]),
        Block(page_index=0, block_index=1, type=BlockType.TEXT, bbox=(50, 100, 700, 150),
              lines=[TextLine(text="Câu 1: Câu hỏi TN", bbox=(50, 100, 700, 150))]),
        Block(page_index=0, block_index=2, type=BlockType.TEXT, bbox=(50, 160, 350, 200),
              lines=[TextLine(text="A. đáp án A", bbox=(50, 160, 350, 200))]),
        Block(page_index=0, block_index=3, type=BlockType.TEXT, bbox=(400, 160, 700, 200),
              lines=[TextLine(text="B. đáp án B", bbox=(400, 160, 700, 200))]),
        Block(page_index=0, block_index=4, type=BlockType.TEXT, bbox=(50, 210, 350, 250),
              lines=[TextLine(text="C. đáp án C", bbox=(50, 210, 350, 250))]),
        Block(page_index=0, block_index=5, type=BlockType.TEXT, bbox=(400, 210, 700, 250),
              lines=[TextLine(text="D. đáp án D", bbox=(400, 210, 700, 250))]),
    ]
    anchors = [
        Anchor(page_index=0, type=AnchorType.GROUP_HEADER, bbox=(50, 50, 700, 80),
               text="Phần I. Trắc nghiệm"),
        Anchor(page_index=0, type=AnchorType.QUESTION, bbox=(50, 100, 700, 150),
               text="Câu 1", value="1"),
        Anchor(page_index=0, type=AnchorType.ANSWER, bbox=(50, 160, 350, 200),
               text="A. đáp án A", value="A"),
        Anchor(page_index=0, type=AnchorType.ANSWER, bbox=(400, 160, 700, 200),
               text="B. đáp án B", value="B"),
        Anchor(page_index=0, type=AnchorType.ANSWER, bbox=(50, 210, 350, 250),
               text="C. đáp án C", value="C"),
        Anchor(page_index=0, type=AnchorType.ANSWER, bbox=(400, 210, 700, 250),
               text="D. đáp án D", value="D"),
    ]

    questions, groups, layouts, group_layouts = snake_walk(
        [blocks], anchors,
        page_heights=[3300.0], page_widths=[2550.0]
    )

    assert len(questions) == 1
    assert len(groups) == 1
    assert groups[0].type == GroupType.SECTION_PART
    assert questions[0].group_id == "g1"
    print("✅ test_snake_walk_with_group PASSED")


if __name__ == "__main__":
    print("=" * 60)
    print("PHASE 2 UNIT TESTS (with Bug 1-4 fixes)")
    print("=" * 60)

    test_strip_accents()
    test_classify_group_type()
    test_in_range()
    test_classify_mcq()
    test_classify_essay()
    test_classify_reading_comprehension()
    test_classify_fill_blank()
    test_classify_all()
    test_flatten_lines()
    test_snake_walk_line_granularity()
    test_clip_last_answer()
    test_solution_boundary()
    test_metadata_parse()
    test_snake_walk_with_group()

    print("\n" + "=" * 60)
    print("🎉 ALL 14 TESTS PASSED!")
    print("=" * 60)
