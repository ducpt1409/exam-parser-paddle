"""Final exam structure - output schema của pipeline."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from src.schemas.block import BBox


class QuestionType(str, Enum):
    """Loại câu hỏi trong đề thi VN."""
    MCQ_SINGLE = "trac_nghiem_1_dap_an"        # Trắc nghiệm 1 đáp án A/B/C/D
    MCQ_MULTI = "trac_nghiem_nhieu_dap_an"     # Trắc nghiệm chọn nhiều
    TRUE_FALSE = "dung_sai"                     # Đúng/sai a)b)c)d)
    FILL_BLANK = "dien_dap_an"                  # Điền số / từ vào chỗ trống
    SHORT_ANSWER = "tu_luan_ngan"               # Tự luận ngắn
    ESSAY = "tu_luan_dai"                       # Tự luận dài
    MATCHING = "ghep_cap"                       # Ghép cặp cột A - cột B
    ORDERING = "sap_xep"                        # Sắp xếp thứ tự
    READING_COMPREHENSION = "doc_hieu"          # Câu hỏi đọc hiểu (thuộc group passage)
    UNKNOWN = "unknown"


class GroupType(str, Enum):
    """Loại nhóm câu hỏi."""
    PASSAGE = "passage"                # Đoạn văn dùng chung
    SECTION_PART = "section_part"      # "Phần I/II"
    INSTRUCTION = "instruction"        # "Mark the letter..."
    UNKNOWN = "unknown"


class CroppedImage(BaseModel):
    """Ảnh đã crop, lưu MinIO."""
    bbox: BBox                       # bbox trên page gốc
    page_indices: list[int]          # pages chứa image (>1 nếu vắt trang)
    minio_key: str                   # key trong MinIO bucket
    url: Optional[str] = None        # presigned URL hoặc public URL
    width: int
    height: int
    size_bytes: int


class Answer(BaseModel):
    """1 đáp án."""
    label: str                       # "A", "B", "C", "D"
    image: Optional[CroppedImage] = None
    text: str = ""                   # OCR text (tham khảo)
    is_correct: Optional[bool] = None    # null nếu chưa có đáp án


class Question(BaseModel):
    """1 câu hỏi."""
    id: str                          # vd: "q1"
    number: int                      # số câu
    type: QuestionType = QuestionType.UNKNOWN
    group_id: Optional[str] = None

    # Cropped images
    full_image: Optional[CroppedImage] = None    # toàn bộ câu hỏi (content + answers)
    content_image: Optional[CroppedImage] = None # chỉ phần đề bài
    answers: list[Answer] = Field(default_factory=list)

    # Text reference (cho debug + search)
    content_text: str = ""
    has_figure: bool = False
    has_formula: bool = False
    has_table: bool = False

    # Meta
    page_indices: list[int] = Field(default_factory=list)
    confidence: float = 1.0
    needs_review: bool = False


class Group(BaseModel):
    """Nhóm câu hỏi (vd: passage + 5 câu)."""
    id: str                          # "g1"
    type: GroupType
    header_image: Optional[CroppedImage] = None
    header_text: str = ""
    passage_image: Optional[CroppedImage] = None
    passage_text: str = ""
    question_ids: list[str] = Field(default_factory=list)


class ExamMetadata(BaseModel):
    """Metadata trang đầu đề thi."""
    ma_de: Optional[str] = None
    mon: Optional[str] = None
    thoi_gian_phut: Optional[int] = None
    truong: Optional[str] = None
    nam_hoc: Optional[str] = None
    tong_so_cau: Optional[int] = None


class Exam(BaseModel):
    """Final exam structure - output JSON."""
    exam_id: str                     # UUID
    source_file: str                 # original filename
    n_pages: int

    metadata: ExamMetadata = Field(default_factory=ExamMetadata)
    groups: list[Group] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)

    # MinIO assets
    preview_pdf_url: Optional[str] = None    # PDF với bbox màu cho review
    source_minio_key: Optional[str] = None   # key file gốc (PDF) trên MinIO
    source_url: Optional[str] = None         # presigned URL file gốc

    # Stats
    n_questions: int = 0
    n_groups: int = 0
    n_essay: int = 0
    n_mcq: int = 0
    avg_confidence: float = 0.0
