"""Test Phase 2 logic — chạy trong conda env exam_parser_paddle.

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
)
from src.services.question_classifier import classify, classify_all


def test_strip_accents():
    """Test bỏ dấu tiếng Việt."""
    assert _strip_accents("Câu") == "Cau"
    assert _strip_accents("Đọc đoạn") == "Doc doan"
    assert _strip_accents("Phần") == "Phan"
    assert _strip_accents("Trắc nghiệm") == "Trac nghiem"
    print("✅ test_strip_accents PASSED")


def test_classify_group_type():
    """Test phân loại GroupType từ header text."""
    assert _classify_group_type("Read the following passage") == GroupType.PASSAGE
    assert _classify_group_type("Đọc đoạn văn sau") == GroupType.PASSAGE
    assert _classify_group_type("Phần I") == GroupType.SECTION_PART
    assert _classify_group_type("Phần II. Tự luận") == GroupType.SECTION_PART
    assert _classify_group_type("Mark the letter A, B, C or D") == GroupType.INSTRUCTION
    assert _classify_group_type("Choose the correct answer") == GroupType.INSTRUCTION
    assert _classify_group_type("Something else") == GroupType.UNKNOWN
    print("✅ test_classify_group_type PASSED")


def test_in_range():
    """Test kiểm tra position nằm trong [start, end)."""
    assert _in_range((0, 100), (0, 50), (0, 200)) is True
    assert _in_range((0, 50), (0, 50), (0, 200)) is True   # inclusive start
    assert _in_range((0, 200), (0, 50), (0, 200)) is False  # exclusive end
    assert _in_range((1, 100), (0, 50), (0, 200)) is True   # next page > same page
    assert _in_range((0, 30), (0, 50), (0, 200)) is False   # before start
    print("✅ test_in_range PASSED")


def test_classify_mcq():
    """Test phân loại MCQ (4 đáp án)."""
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
    """Test phân loại Essay (không có đáp án, keyword tự luận)."""
    q = Question(id="q5", number=5, content_text="Giải các phương trình sau", answers=[])
    assert classify(q) == QuestionType.ESSAY

    q2 = Question(id="q6", number=6, content_text="Chứng minh rằng x > 0", answers=[])
    assert classify(q2) == QuestionType.ESSAY
    print("✅ test_classify_essay PASSED")


def test_classify_reading_comprehension():
    """Test phân loại Reading Comprehension (trong group PASSAGE)."""
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
    """Test phân loại Fill Blank (chỗ trống)."""
    q = Question(id="q6", number=6, content_text="Điền vào chỗ trống: 2 + ___ = 5", answers=[])
    assert classify(q) == QuestionType.FILL_BLANK

    q2 = Question(id="q7", number=7, content_text="The answer is (...) years old", answers=[])
    assert classify(q2) == QuestionType.FILL_BLANK
    print("✅ test_classify_fill_blank PASSED")


def test_classify_all():
    """Test gán type in-place cho danh sách câu hỏi."""
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


def test_snake_walk_basic():
    """Test Snake Walker với 2 câu: 1 MCQ + 1 tự luận."""
    blocks_page0 = [
        Block(page_index=0, block_index=0, type=BlockType.TEXT, bbox=(50, 100, 700, 150),
              lines=[TextLine(text="Câu 1: Tìm giá trị x", bbox=(50, 100, 700, 150))]),
        Block(page_index=0, block_index=1, type=BlockType.TEXT, bbox=(50, 160, 350, 200),
              lines=[TextLine(text="A. 1", bbox=(50, 160, 350, 200))]),
        Block(page_index=0, block_index=2, type=BlockType.TEXT, bbox=(400, 160, 700, 200),
              lines=[TextLine(text="B. 2", bbox=(400, 160, 700, 200))]),
        Block(page_index=0, block_index=3, type=BlockType.TEXT, bbox=(50, 210, 350, 250),
              lines=[TextLine(text="C. 3", bbox=(50, 210, 350, 250))]),
        Block(page_index=0, block_index=4, type=BlockType.TEXT, bbox=(400, 210, 700, 250),
              lines=[TextLine(text="D. 4", bbox=(400, 210, 700, 250))]),
        Block(page_index=0, block_index=5, type=BlockType.TEXT, bbox=(50, 300, 700, 350),
              lines=[TextLine(text="Câu 2: Giải phương trình sau", bbox=(50, 300, 700, 350))]),
    ]

    anchors = [
        Anchor(page_index=0, type=AnchorType.QUESTION, bbox=(50, 100, 700, 150),
               text="Câu 1", value="1"),
        Anchor(page_index=0, type=AnchorType.ANSWER, bbox=(50, 160, 350, 200),
               text="A. 1", value="A"),
        Anchor(page_index=0, type=AnchorType.ANSWER, bbox=(400, 160, 700, 200),
               text="B. 2", value="B"),
        Anchor(page_index=0, type=AnchorType.ANSWER, bbox=(50, 210, 350, 250),
               text="C. 3", value="C"),
        Anchor(page_index=0, type=AnchorType.ANSWER, bbox=(400, 210, 700, 250),
               text="D. 4", value="D"),
        Anchor(page_index=0, type=AnchorType.QUESTION, bbox=(50, 300, 700, 350),
               text="Câu 2", value="2"),
    ]

    questions, groups, layouts, group_layouts = snake_walk(
        [blocks_page0], anchors,
        page_heights=[3300.0], page_widths=[2550.0]
    )

    assert len(questions) == 2, f"Expected 2 questions, got {len(questions)}"
    assert questions[0].number == 1
    assert questions[1].number == 2
    assert len(questions[0].answers) == 4
    assert len(questions[1].answers) == 0
    assert "q1" in layouts
    assert "q2" in layouts
    assert layouts["q1"].full.parts, "Q1 should have full region"
    assert layouts["q1"].answers, "Q1 should have answer regions"
    print("✅ test_snake_walk_basic PASSED")


def test_snake_walk_with_group():
    """Test Snake Walker với group header."""
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
    assert groups[0].header_text == "Phần I. Trắc nghiệm"
    assert questions[0].group_id == "g1"
    print("✅ test_snake_walk_with_group PASSED")


def test_solution_boundary_monotonic():
    """Test phát hiện ranh giới lời giải bằng fallback monotonic."""
    # Simulate: Câu 1, 2, 3 rồi lại Câu 1, 2, 3 (phần lời giải)
    anchors = [
        Anchor(page_index=0, type=AnchorType.QUESTION, bbox=(50, 100, 700, 150),
               text="Câu 1", value="1"),
        Anchor(page_index=0, type=AnchorType.QUESTION, bbox=(50, 300, 700, 350),
               text="Câu 2", value="2"),
        Anchor(page_index=0, type=AnchorType.QUESTION, bbox=(50, 500, 700, 550),
               text="Câu 3", value="3"),
        # Lời giải: bắt đầu lại từ Câu 1
        Anchor(page_index=1, type=AnchorType.QUESTION, bbox=(50, 100, 700, 150),
               text="Câu 1", value="1"),
        Anchor(page_index=1, type=AnchorType.QUESTION, bbox=(50, 300, 700, 350),
               text="Câu 2", value="2"),
    ]

    boundary = _find_solution_boundary(anchors, [[]])
    assert boundary is not None, "Should detect solution boundary"
    assert boundary[0] == 1, f"Boundary should be on page 1, got {boundary[0]}"
    print("✅ test_solution_boundary_monotonic PASSED")


def test_metadata_parse():
    """Test parse metadata."""
    blocks = [
        Block(page_index=0, block_index=0, type=BlockType.TITLE, bbox=(50, 10, 700, 40),
              lines=[TextLine(text="SỞ GD&ĐT HÀ NỘI", bbox=(50, 10, 700, 40))]),
        Block(page_index=0, block_index=1, type=BlockType.TEXT, bbox=(50, 50, 700, 80),
              lines=[TextLine(text="Mã đề: 132", bbox=(50, 50, 700, 80))]),
        Block(page_index=0, block_index=2, type=BlockType.TEXT, bbox=(50, 90, 700, 120),
              lines=[TextLine(text="Thời gian: 60 phút", bbox=(50, 90, 700, 120))]),
        Block(page_index=0, block_index=3, type=BlockType.TEXT, bbox=(50, 130, 700, 160),
              lines=[TextLine(text="Môn: Toán", bbox=(50, 130, 700, 160))]),
    ]

    anchors = [
        Anchor(page_index=0, type=AnchorType.METADATA, bbox=(50, 50, 700, 80),
               text="Mã đề: 132"),
        Anchor(page_index=0, type=AnchorType.METADATA, bbox=(50, 90, 700, 120),
               text="Thời gian: 60 phút"),
    ]

    meta = parse_exam_metadata(anchors, [blocks], 50)
    assert meta.tong_so_cau == 50
    print(f"   Metadata: ma_de={meta.ma_de}, thoi_gian={meta.thoi_gian_phut}, mon={meta.mon}")
    print("✅ test_metadata_parse PASSED")


if __name__ == "__main__":
    print("=" * 60)
    print("PHASE 2 UNIT TESTS")
    print("=" * 60)

    test_strip_accents()
    test_classify_group_type()
    test_in_range()
    test_classify_mcq()
    test_classify_essay()
    test_classify_reading_comprehension()
    test_classify_fill_blank()
    test_classify_all()
    test_snake_walk_basic()
    test_snake_walk_with_group()
    test_solution_boundary_monotonic()
    test_metadata_parse()

    print("\n" + "=" * 60)
    print("🎉 ALL 12 TESTS PASSED!")
    print("=" * 60)
