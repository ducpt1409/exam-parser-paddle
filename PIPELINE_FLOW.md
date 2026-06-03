# Pipeline Flow - Luồng hoạt động chi tiết Phương án P4

**Tài liệu**: Mô tả end-to-end cách pipeline xử lý 1 file đề thi từ input đến output JSON + MinIO URLs.

---

## 1. Tổng quan luồng

```
┌─────────────┐
│  USER       │
│  upload PDF │
└──────┬──────┘
       ▼
┌─────────────────────────────────────────────────────┐
│  FastAPI: POST /api/v1/exams/parse                  │
│  - Lưu PDF tạm vào /tmp/                            │
│  - Sinh exam_id (UUID)                              │
│  - Trả về 202 Accepted ngay (async processing)      │
└──────┬──────────────────────────────────────────────┘
       ▼
┌─────────────────────────────────────────────────────┐
│  ExamPipeline.parse(pdf_path)  (background task)    │
│  ┌─────────────────────────────────────────────┐    │
│  │ [1] Preprocess                              │    │
│  │ [2] PaddleOCR Layout + OCR                  │    │
│  │ [3] Anchor Extraction                       │    │
│  │ [4] VLM Verify (lazy)                       │    │
│  │ [5] Snake Walker                            │    │
│  │ [6] Question Classifier                     │    │
│  │ [7a] Crop images                            │    │
│  │ [7b] Upload MinIO                           │    │
│  └─────────────────────────────────────────────┘    │
└──────┬──────────────────────────────────────────────┘
       ▼
┌─────────────────────────────────────────────────────┐
│  USER: GET /api/v1/exams/{exam_id}                  │
│  Response: Exam JSON với MinIO URLs                 │
└─────────────────────────────────────────────────────┘
```

---

## 2. Data flow giữa các stages

```
input.pdf
    │
    ▼ Stage 1
list[PIL.Image]                        # 1 image per page
    │
    ▼ Stage 2  (PaddleOCR PP-StructureV3)
list[list[Block]]                      # blocks per page
    │  Block = {page_idx, type, bbox, lines[{text, bbox}], confidence}
    │
    ▼ Stage 3  (Regex extract)
list[Anchor]                           # anchors per page (flatten)
    │  Anchor = {page_idx, type, bbox, value, source: "regex"}
    │
    ▼ Stage 4  (VLM Verify - chỉ khi cần)
list[Anchor]                           # bổ sung anchors từ VLM
    │  Một số anchors có source: "vlm"
    │
    ▼ Stage 5  (Snake Walker - global)
list[Question]                         # KHÔNG còn page concept
    │  Question = {number, page_indices[], content_blocks[], answers[]}
    │  spans_pages tự động nếu vắt trang
    │
    ▼ Stage 6  (Classify)
list[Question]                         # mỗi Q có type
    │
    ▼ Stage 7a  (Crop)
list[Question với CroppedImage]
    │  CroppedImage = {bytes, bbox, size}
    │
    ▼ Stage 7b  (Upload MinIO)
Exam                                   # final output
    │  Question có MinIO URLs
    ▼
JSON response
```

---

## 3. Stage 1: Preprocess

### Input
```python
input_path: str  # "/tmp/uploads/abc123.pdf"
```

### Process

```
PDF/Image file
    │
    ├─ Detect file type
    │   ├─ .pdf → PyMuPDF render
    │   └─ .jpg/.png → PIL load
    │
    ├─ Render DPI = 300 (configurable)
    │   PDF page N × 300 DPI = ~2480×3508 pixels (A4)
    │
    ├─ Deskew (chỉ scan)
    │   - Detect skew angle (Hough transform)
    │   - Skip nếu < 0.5° (PDF render thường thẳng)
    │   - Rotate nếu cần
    │
    └─ Output: list[PIL.Image]
```

### Output
```python
images = [
    PIL.Image(size=(2480, 3508)),    # page 0
    PIL.Image(size=(2480, 3508)),    # page 1
    ...
]
```

### Code
```python
# src/services/preprocess.py
def preprocess(input_path: str, dpi: int = 300, do_deskew: bool = True) -> list[Image.Image]:
    ext = Path(input_path).suffix.lower()
    
    if ext == ".pdf":
        images = pdf_to_images(input_path, dpi)
    else:
        images = [Image.open(input_path).convert("RGB")]
    
    if do_deskew:
        images = [deskew(img) for img in images]
    
    return images
```

### Lưu ý
- **DPI 300** là sweet spot: đủ chi tiết cho OCR, không quá tốn memory
- Deskew chỉ cần cho scan, PDF rendered đã thẳng
- Trên RTX 5090 WSL: ~1s cho 10 trang

---

## 4. Stage 2: PaddleOCR PP-StructureV3

### Input
```python
images: list[PIL.Image]
```

### Process

```
Mỗi PIL.Image
    │
    ▼ PaddleOCR PP-StructureV3 inference (CUDA)
    │
    ├─ Layout Detection
    │   → bbox của các region: text/title/figure/table/list/equation
    │
    ├─ OCR (trên text regions)
    │   → text + bbox per line, confidence
    │
    ├─ Table Recognition (trên table regions)
    │   → HTML structure + cell bboxes
    │
    └─ Output: list[block_dict]
```

### Output mẫu (1 page)

```python
blocks = [
    Block(
        page_index=0,
        block_index=0,
        type=BlockType.TITLE,
        bbox=(100, 50, 800, 100),
        lines=[
            TextLine(text="ĐỀ THI THỬ THPT QG 2024", bbox=(100, 55, 800, 95), confidence=0.99),
        ],
        confidence=0.95,
    ),
    Block(
        page_index=0, block_index=1,
        type=BlockType.TEXT,
        bbox=(100, 200, 800, 250),
        lines=[
            TextLine(text="Câu 1: Hàm số y = x² đồng biến trên khoảng nào?", 
                     bbox=(100, 200, 800, 250), confidence=0.98),
        ],
    ),
    Block(
        page_index=0, block_index=2,
        type=BlockType.FIGURE,             # ← ảnh đồ thị
        bbox=(200, 280, 600, 480),
        lines=[],                          # figure không có text
    ),
    Block(
        page_index=0, block_index=3,
        type=BlockType.TEXT,
        bbox=(100, 500, 800, 540),
        lines=[
            TextLine(text="A. (-∞, 0)", bbox=(100, 500, 250, 540), confidence=0.92),
            TextLine(text="B. (0, +∞)", bbox=(280, 500, 430, 540), confidence=0.94),
            TextLine(text="C. R", bbox=(460, 500, 540, 540), confidence=0.96),
            TextLine(text="D. ∅", bbox=(570, 500, 650, 540), confidence=0.93),
        ],
    ),
    # ... more blocks
]
```

### Code
```python
# src/services/paddle_parser.py
class PaddleParser:
    def __init__(self):
        self.engine = PPStructure(
            layout=True, table=True, ocr=True,
            ocr_lang="vi", use_gpu=True, show_log=False,
        )
    
    def parse_page(self, image: Image.Image, page_index: int) -> list[Block]:
        raw_blocks = self.engine(np.array(image))
        return [self._convert_block(b, page_index, i) 
                for i, b in enumerate(raw_blocks)]
```

### Lưu ý
- PaddleOCR cache predictor → chỉ load model 1 lần
- Output bbox **CHÍNH XÁC ĐẾN PIXEL** (key advantage)
- Figure/Table được detect là block riêng → không bị skip
- Trên RTX 5090: ~0.5-1s/page

---

## 5. Stage 3: Anchor Extraction

### Mục đích
Tìm vị trí các marker quan trọng trong text: "Câu N", "A./B./C./D.", "Phần I", "Đọc đoạn"...

### Input
```python
blocks_per_page: list[list[Block]]
```

### Process

```
Mỗi Block có text
    │
    ▼ Loop qua từng line trong block
    │
    ├─ Strip dấu tiếng Việt (OCR-tolerant)
    │   "Câu" → "Cau"
    │   "đ" → "d"
    │
    ├─ Match TỪNG pattern
    │   ├─ ^cau\s+(\d+)\s*[\.\:]      → QUESTION
    │   ├─ ^bai\s+(\d+)                → QUESTION  
    │   ├─ ^question\s+(\d+)           → QUESTION
    │   ├─ ^([A-D])\s*[\.\)]           → ANSWER
    │   ├─ ^([a-d])\s*[\.\)]           → SUB_QUESTION (đúng/sai)
    │   ├─ ^phan\s+[IVX\d]             → GROUP_HEADER
    │   ├─ ^doc\s+(doan|van)           → GROUP_HEADER
    │   └─ ...
    │
    └─ Yield Anchor(page_idx, type, bbox, value, confidence=0.9)
```

### Output mẫu

```python
anchors = [
    Anchor(page_index=0, type=QUESTION, bbox=(100, 200, 130, 250), value="1"),
    Anchor(page_index=0, type=ANSWER, bbox=(100, 500, 130, 540), value="A"),
    Anchor(page_index=0, type=ANSWER, bbox=(280, 500, 310, 540), value="B"),
    Anchor(page_index=0, type=ANSWER, bbox=(460, 500, 490, 540), value="C"),
    Anchor(page_index=0, type=ANSWER, bbox=(570, 500, 600, 540), value="D"),
    Anchor(page_index=0, type=QUESTION, bbox=(100, 600, 130, 650), value="2"),
    # ... pattern tương tự cho các câu khác
]
```

### Code
```python
# src/services/anchor_extractor.py
ANCHOR_PATTERNS = {
    AnchorType.QUESTION: [
        re.compile(r"^\s*(cau|bai|question)\s+(\d+)\s*[\.\:]", re.IGNORECASE),
    ],
    AnchorType.ANSWER: [
        re.compile(r"^\s*([A-D])\s*[\.\)]"),
    ],
    AnchorType.GROUP_HEADER: [
        re.compile(r"^\s*(phan|doc doan|mark the letter|read the following)", re.IGNORECASE),
    ],
    # ... more
}

def extract_anchors(blocks_per_page: list[list[Block]]) -> list[Anchor]:
    anchors = []
    for blocks in blocks_per_page:
        for block in blocks:
            for line in block.lines:
                text_no_accent = strip_accents(line.text).strip()
                for anchor_type, patterns in ANCHOR_PATTERNS.items():
                    for pattern in patterns:
                        if m := pattern.match(text_no_accent):
                            anchors.append(Anchor(
                                page_index=block.page_index,
                                type=anchor_type,
                                bbox=line.bbox,
                                value=m.group(m.lastindex) if m.groups() else None,
                                text=line.text,
                                confidence=0.9,
                                source="regex",
                            ))
                            break
    return anchors
```

### Lưu ý
- **Strip dấu** trước khi match → robust với OCR sai dấu (Câu/Cau/Cảu)
- 1 line có thể chứa nhiều anchor (vd: "A. ... B. ... C. ... D." trên 1 dòng) → cần `finditer`
- Confidence cao (0.9) cho regex match - sai pattern rất hiếm

---

## 6. Stage 4: VLM Verify (Lazy)

### Khi nào trigger?

```python
def should_verify_with_vlm(page_anchors):
    n_q = count(type=QUESTION)
    n_a = count(type=ANSWER)
    
    # Heuristic: trắc nghiệm phải có 4 đáp án/câu
    if n_q > 0 and n_a / n_q < 3:
        return True   # OCR có thể miss anchors
    
    # Confidence trung bình thấp
    if avg_confidence(page_anchors) < 0.7:
        return True
    
    return False
```

### Khi trigger

```
Trang suspicious
    │
    ▼ Gửi cho Qwen3-VL:
    │
    │  prompt = """
    │  Tìm bounding box của TẤT CẢ marker trên trang:
    │  - "Câu N", "Bài N", "Question N"
    │  - "A.", "B.", "C.", "D."
    │  - "Phần I/II", "Đọc đoạn"...
    │  
    │  Trả JSON list: [{type, value, bbox}, ...]
    │  """
    │
    ├─ VLM response → parse JSON
    │
    ├─ Merge với regex anchors:
    │   - Anchor mới (chưa có) → thêm với source="vlm"
    │   - Anchor trùng vị trí → keep regex (đáng tin hơn)
    │
    └─ Output: anchors enriched
```

### Code
```python
# src/services/vlm_client.py
def verify_anchors(image: Image.Image, existing_anchors: list[Anchor]) -> list[Anchor]:
    response = ollama.chat(
        model="qwen3-vl:32b-instruct",
        messages=[{
            "role": "user",
            "content": ANCHOR_VERIFY_PROMPT,
            "images": [encode_image(image)],
        }],
    )
    vlm_anchors = parse_anchors_from_response(response)
    return merge_anchors(existing_anchors, vlm_anchors)
```

### Lưu ý
- **Lazy** - chỉ gọi ~10-20% trang (suspicious)
- VLM chậm (~5-10s) nên cần kiệm
- Merge thông minh: regex anchor đáng tin hơn (precise position)

---

## 7. Stage 5: Snake Walker ⭐ (Cross-page native)

### Đây là step quan trọng nhất - giải quyết câu hỏi vắt trang.

### Input
```python
all_anchors: list[Anchor]       # tất cả anchors (đã sort by global position)
all_blocks_per_page: list[list[Block]]
```

### Process

```
Sort tất cả Anchors theo (page_idx, y_top)
    │
    ▼ Filter chỉ question anchors
    │
    │  question_anchors = [Q1, Q2, Q3, ..., Qn]
    │
    ▼ Snake walk
    │
    │  for i, q_anchor in enumerate(question_anchors):
    │
    │      # Start position = q_anchor location
    │      start_pos = (q_anchor.page_index, q_anchor.bbox.y_top)
    │
    │      # End position = next q_anchor (hoặc end of document)
    │      end_pos = (next_q_anchor.page_index, next_q_anchor.bbox.y_top)
    │              hoặc (last_page, infinity)
    │
    │      # Collect ALL blocks giữa start và end
    │      region_blocks = []
    │      for page_idx in range(start_pos.page, end_pos.page + 1):
    │          for block in blocks_per_page[page_idx]:
    │              if global_pos(block) in [start_pos, end_pos):
    │                  region_blocks.append(block)
    │
    │      # ← span pages TỰ ĐỘNG nếu start.page != end.page
    │
    │      # Sub-group answers
    │      answer_anchors_in_region = filter answer anchors trong [start, end)
    │      for j, a_anchor in enumerate(answer_anchors_in_region):
    │          a_end = next_answer_anchor or end_pos
    │          a_blocks = blocks in [a_anchor, a_end)
    │          answers.append(Answer(label=a_anchor.value, blocks=a_blocks))
    │
    │      # Content = blocks TRƯỚC answer đầu tiên
    │      first_answer_pos = global_pos(answers[0]) if answers else end_pos
    │      content_blocks = [b in region_blocks if global_pos(b) < first_answer_pos]
    │
    │      questions.append(Question(
    │          number=q_anchor.value,
    │          page_indices=sorted(set(b.page_index for b in region_blocks)),
    │          content_blocks=content_blocks,
    │          answers=answers,
    │      ))
    │
    └─ Output: list[Question]
```

### Ví dụ cụ thể

**Đề có 3 trang, Câu 5 vắt trang 1→2:**

```
Trang 1:                          Trang 2:
┌─────────────────┐               ┌─────────────────┐
│ Câu 4: ...      │               │  D. y = ...     │ ← đáp án D của Câu 5
│  A. ...         │               │                 │
│  B. ...         │               │ Câu 6: ...      │
│  C. ...         │               │  A. ...         │
│  D. ...         │               │  ...            │
│                 │               │                 │
│ Câu 5: ...      │               │ Câu 7: ...      │
│  A. y = ...     │               │  ...            │
│  B. y = ...     │               │                 │
│  C. y = ...     │               │                 │
└─────────────────┘               └─────────────────┘
```

**Sort anchors global**:
```
(page=0, y=50)   Câu 4
(page=0, y=150)  A
(page=0, y=200)  B  
(page=0, y=250)  C
(page=0, y=300)  D
(page=0, y=400)  Câu 5         ← question anchor
(page=0, y=500)  A             ← answers Câu 5
(page=0, y=550)  B
(page=0, y=600)  C
(page=1, y=50)   D             ← answer D Câu 5 ở trang 2!
(page=1, y=150)  Câu 6         ← NEXT question anchor
...
```

**Snake walk cho Câu 5**:
- start_pos = (page=0, y=400)
- end_pos = (page=1, y=150)   ← Câu 6 ở trang 2
- region_blocks = TẤT CẢ blocks giữa pos này → span 2 trang!
- answers = [A, B, C ở page=0, D ở page=1]
- → Câu 5 có `page_indices = [0, 1]` ✓

### Code
```python
# src/services/snake_walker.py
def snake_walk(all_blocks_per_page, all_anchors) -> list[Question]:
    # Flatten + sort
    sorted_anchors = sorted(all_anchors, key=lambda a: a.global_position())
    q_anchors = [a for a in sorted_anchors if a.type == AnchorType.QUESTION]
    
    questions = []
    for i, q_anchor in enumerate(q_anchors):
        start_pos = q_anchor.global_position()
        end_pos = (q_anchors[i + 1].global_position() 
                   if i + 1 < len(q_anchors) 
                   else (len(all_blocks_per_page) - 1, float("inf")))
        
        # Collect blocks
        region_blocks = collect_blocks_in_range(all_blocks_per_page, start_pos, end_pos)
        
        # Get answers in region
        answer_anchors = [a for a in sorted_anchors 
                          if a.type == AnchorType.ANSWER 
                          and start_pos <= a.global_position() < end_pos]
        answers = group_answers(answer_anchors, region_blocks)
        
        # Content = blocks before first answer
        first_ans_pos = answers[0].global_position() if answers else end_pos
        content_blocks = [b for b in region_blocks if global_pos(b) < first_ans_pos]
        
        questions.append(Question(
            number=int(q_anchor.value),
            page_indices=sorted(set(b.page_index for b in region_blocks)),
            content_blocks=content_blocks,
            answers=answers,
        ))
    
    return questions
```

### Lưu ý
- **Cross-page = ZERO special handling** - snake walk tự nhiên
- Block types ALL được include (text, figure, table, formula) - không miss
- Output `page_indices` cho biết câu vắt mấy trang

---

## 8. Stage 6: Question Classifier

### Mục đích
Gán `QuestionType` cho mỗi Question dựa vào pattern.

### Decision tree

```
Question
    │
    ▼ Có answers (A/B/C/D) không?
    │
    ├─ KHÔNG → Check sub-question pattern
    │   ├─ Có "1.", "2." trong content → ESSAY (tự luận có ý nhỏ)
    │   ├─ Có "________" hoặc "..." → FILL_BLANK
    │   └─ Else → ESSAY (tự luận thông thường)
    │
    └─ CÓ answers
        ├─ Có sub_question pattern a)b)c)d) → TRUE_FALSE (đúng/sai)
        ├─ Content có "Chọn ... đáp án sai/đúng" + có cụm "trong các đáp án sau" 
        │   → có thể MCQ_MULTI (chọn nhiều)
        ├─ Group có "matching" → MATCHING
        └─ Default → MCQ_SINGLE
```

### Code
```python
# src/services/question_classifier.py
def classify(question: Question) -> QuestionType:
    n_answers = len(question.answers)
    content = question.content_text.lower()
    
    if n_answers == 0:
        if re.search(r"\b[1-9]\s*[\.\)]", content):
            return QuestionType.ESSAY
        if "________" in content or "..." in content:
            return QuestionType.FILL_BLANK
        return QuestionType.ESSAY
    
    # Multi-choice keyword
    if any(kw in content for kw in ["chọn nhiều", "tất cả đáp án đúng"]):
        return QuestionType.MCQ_MULTI
    
    # True/false detection (đúng/sai)
    if any(re.match(rf"^{l}\s*[\.\)]", a.text.lower()) 
           for l in "abcd" for a in question.answers):
        return QuestionType.TRUE_FALSE
    
    return QuestionType.MCQ_SINGLE
```

### VLM fallback
Khi rule-based không chắc (vd content ambiguous), gọi VLM:
```python
if confidence < 0.6:
    type_str = vlm_classify_type(question.content_image)
    return QuestionType(type_str)
```

---

## 9. Stage 7a: Crop Images

### Input
```python
question: Question                 # với content_blocks + answers
page_images: list[PIL.Image]       # original page images
```

### Process

```
Question
    │
    ▼ Trường hợp 1: 1 trang (page_indices = [0])
    │
    ├─ Compute bbox tổng = union(content_blocks + answers)
    ├─ Crop page_images[0] theo bbox
    ├─ Save WEBP quality 85
    └─ Return CroppedImage
    │
    ▼ Trường hợp 2: nhiều trang (page_indices = [0, 1])
    │
    ├─ Chia blocks theo page
    │   page_0_blocks = blocks where page_index == 0
    │   page_1_blocks = blocks where page_index == 1
    │
    ├─ Crop từng phần:
    │   crop_p0 = crop(page_images[0], union_bbox(page_0_blocks))
    │   crop_p1 = crop(page_images[1], union_bbox(page_1_blocks))
    │
    ├─ Strategy A: Stitch vertical
    │   stitched = vertical_concat([crop_p0, crop_p1])
    │
    ├─ Strategy B: Save từng phần riêng
    │   crops = [crop_p0, crop_p1]
    │   metadata: page_indices, page_crops_urls
    │
    └─ Return CroppedImage (hoặc list)
```

### Code
```python
# src/services/cropper.py
def crop_question(question: Question, page_images: list[Image.Image]) -> CroppedImage:
    blocks_by_page = group_by_page(question.all_blocks)
    
    if len(blocks_by_page) == 1:
        # Single page
        page_idx = list(blocks_by_page.keys())[0]
        bbox = union_bbox([b.bbox for b in blocks_by_page[page_idx]])
        bbox_padded = pad_bbox(bbox, padding=15)
        cropped = page_images[page_idx].crop(bbox_padded)
    else:
        # Multi page - stitch vertical
        crops = []
        for page_idx in sorted(blocks_by_page.keys()):
            bbox = union_bbox([b.bbox for b in blocks_by_page[page_idx]])
            crops.append(page_images[page_idx].crop(pad_bbox(bbox, 15)))
        cropped = stitch_vertical(crops, gap=10)
    
    # Save WEBP
    buf = BytesIO()
    cropped.save(buf, format="WEBP", quality=85)
    
    return CroppedImage(
        bbox=bbox,
        page_indices=sorted(blocks_by_page.keys()),
        width=cropped.width,
        height=cropped.height,
        size_bytes=buf.tell(),
        _image_bytes=buf.getvalue(),   # to be uploaded
    )
```

### Crop từng answer

Tương tự, mỗi Answer có bbox riêng → crop ra ảnh đáp án A/B/C/D độc lập:
```python
for ans in question.answers:
    ans.image = crop_answer(ans, page_images)
```

---

## 10. Stage 7b: Upload MinIO

### Process

```
CroppedImage (raw bytes)
    │
    ▼ Sinh key cho MinIO
    │
    │  Pattern: exams/{exam_id}/{type}/{file}
    │  
    │  Examples:
    │  - exams/abc-123/questions/q1_full.webp
    │  - exams/abc-123/questions/q1_content.webp
    │  - exams/abc-123/answers/q1_A.webp
    │  - exams/abc-123/answers/q1_B.webp
    │  - exams/abc-123/groups/g1_passage.webp
    │  - exams/abc-123/preview.pdf
    │  - exams/abc-123/exam.json
    │
    ▼ Upload qua MinIO Python SDK
    │
    │  client.put_object(
    │    bucket="exam-parser",
    │    object_name=key,
    │    data=BytesIO(image_bytes),
    │    length=len(image_bytes),
    │    content_type="image/webp",
    │  )
    │
    ▼ Generate URL
    │
    │  Option 1 (public bucket):
    │  url = f"http://minio:9000/exam-parser/{key}"
    │
    │  Option 2 (private):
    │  url = client.presigned_get_object(bucket, key, expires=7 days)
    │
    └─ CroppedImage.url = url
```

### Code
```python
# src/services/minio_client.py
class MinIOService:
    def __init__(self, settings):
        self.client = Minio(...)
        self.bucket = settings.minio_bucket
    
    def upload_image(self, image_bytes: bytes, key: str) -> str:
        self.client.put_object(
            self.bucket,
            key,
            BytesIO(image_bytes),
            length=len(image_bytes),
            content_type="image/webp",
        )
        return self.get_url(key)
    
    def upload_json(self, data: dict, key: str) -> str:
        json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode()
        self.client.put_object(
            self.bucket, key, BytesIO(json_bytes),
            length=len(json_bytes),
            content_type="application/json",
        )
        return self.get_url(key)
    
    def get_url(self, key: str) -> str:
        if self.public_bucket:
            return f"http://{self.endpoint}/{self.bucket}/{key}"
        return self.client.presigned_get_object(
            self.bucket, key, expires=timedelta(days=7),
        )
```

---

## 11. Output JSON cuối cùng

### Schema

```json
{
  "exam_id": "abc-123-uuid",
  "source_file": "de_thi_toan_thpt_2024.pdf",
  "n_pages": 4,
  "metadata": {
    "ma_de": "132",
    "mon": "Toán",
    "thoi_gian_phut": 90,
    "truong": "THPT Đoàn Thượng",
    "nam_hoc": "2020-2021",
    "tong_so_cau": 50
  },
  "groups": [
    {
      "id": "g1",
      "type": "passage",
      "header_image": {
        "url": "http://minio:9000/exam-parser/exams/abc-123/groups/g1_header.webp",
        "bbox": [...],
        "page_indices": [2]
      },
      "passage_image": {
        "url": "http://minio:9000/exam-parser/exams/abc-123/groups/g1_passage.webp",
        ...
      },
      "header_text": "Read the following passage...",
      "question_ids": ["q15", "q16", "q17", "q18", "q19"]
    }
  ],
  "questions": [
    {
      "id": "q1",
      "number": 1,
      "type": "trac_nghiem_1_dap_an",
      "group_id": null,
      "full_image": {
        "url": "http://minio:9000/exam-parser/exams/abc-123/questions/q1_full.webp",
        "bbox": [100, 200, 800, 600],
        "page_indices": [0],
        "width": 700,
        "height": 400,
        "size_bytes": 85432
      },
      "content_image": {
        "url": "http://minio:9000/exam-parser/exams/abc-123/questions/q1_content.webp",
        "bbox": [100, 200, 800, 450],
        ...
      },
      "answers": [
        {
          "label": "A",
          "image": {"url": "...q1_A.webp", "bbox": [...], ...},
          "text": "y = (√3+√2)/4)^x",
          "is_correct": null
        },
        {"label": "B", "image": {...}, "text": "...", "is_correct": null},
        {"label": "C", "image": {...}, "text": "...", "is_correct": null},
        {"label": "D", "image": {...}, "text": "...", "is_correct": null}
      ],
      "content_text": "Hàm số nào sau đây đồng biến...",
      "has_figure": false,
      "has_formula": true,
      "has_table": false,
      "page_indices": [0],
      "confidence": 0.95,
      "needs_review": false
    },
    {
      "id": "q5",
      "number": 5,
      "type": "trac_nghiem_1_dap_an",
      "full_image": {
        "url": "...q5_full.webp",
        "bbox": [100, 1500, 800, 3500],   // vertical stitched of 2 pages
        "page_indices": [0, 1],            // ← vắt 2 trang
        "width": 700,
        "height": 2000,
        "size_bytes": 245678
      },
      "page_indices": [0, 1],
      "needs_review": true,                // flag vì vắt trang
      ...
    }
  ],
  "preview_pdf_url": "http://minio:9000/exam-parser/exams/abc-123/preview.pdf",
  "n_questions": 50,
  "n_groups": 1,
  "n_essay": 0,
  "n_mcq": 50,
  "avg_confidence": 0.91
}
```

---

## 12. API Flow

### Endpoint

```
POST /api/v1/exams/parse
Content-Type: multipart/form-data
Body: file=<exam.pdf>
```

### Sequence diagram

```
User                FastAPI               BackgroundTask              Storage
 │                     │                        │                       │
 │── POST /parse ─────>│                        │                       │
 │  (file.pdf)         │                        │                       │
 │                     │                        │                       │
 │                     │── Save tmp file ──────>│                       │
 │                     │── Generate exam_id     │                       │
 │                     │── Enqueue background ─>│                       │
 │                     │                        │                       │
 │<── 202 Accepted ────│                        │                       │
 │   {exam_id, status_url}                      │                       │
 │                     │                        │                       │
 │                     │                        │── Pipeline.parse() ───┤
 │                     │                        │   - Preprocess        │
 │                     │                        │   - PaddleOCR         │
 │                     │                        │   - Anchor extract    │
 │                     │                        │   - VLM verify        │
 │                     │                        │   - Snake walk        │
 │                     │                        │   - Classify          │
 │                     │                        │   - Crop              │
 │                     │                        │── Upload MinIO ──────>│ (images + JSON)
 │                     │                        │                       │
 │                     │                        │── Save status DB      │
 │                     │                        │                       │
 │── GET /status ─────>│                        │                       │
 │                     │── Check DB             │                       │
 │<── completed ───────│                        │                       │
 │                     │                        │                       │
 │── GET /{exam_id} ──>│                        │                       │
 │                     │── Fetch JSON ────────────────────────────────>│
 │<── Full exam JSON ──│                        │                       │
```

### Code (skeleton)

```python
# src/api/routers/exams.py
from fastapi import APIRouter, BackgroundTasks, UploadFile
from uuid import uuid4

router = APIRouter()

@router.post("/parse", status_code=202)
async def parse_exam(file: UploadFile, background: BackgroundTasks):
    exam_id = str(uuid4())
    
    # Save tmp
    tmp_path = f"/tmp/{exam_id}.pdf"
    with open(tmp_path, "wb") as f:
        f.write(await file.read())
    
    # Enqueue background task
    background.add_task(run_pipeline, exam_id, tmp_path)
    
    # Store initial status
    set_status(exam_id, "processing")
    
    return {
        "exam_id": exam_id,
        "status": "processing",
        "status_url": f"/api/v1/exams/{exam_id}/status",
    }

@router.get("/{exam_id}/status")
async def get_status(exam_id: str):
    return get_status_from_store(exam_id)

@router.get("/{exam_id}")
async def get_exam(exam_id: str):
    return get_exam_json(exam_id)
```

---

## 13. Error handling & Edge cases

### 13.1 PaddleOCR fail trên 1 trang
- Log warning + skip page
- Continue với pages còn lại
- Mark questions có `page_index` này = `needs_review`

### 13.2 VLM timeout / fail
- Skip VLM verification
- Continue với regex anchors
- Mark questions affected = `needs_review`

### 13.3 Câu hỏi không có anchor
- Có content nhưng không match "Câu N:" 
- Có thể là note/instruction → skip
- Hoặc orphan content → log + mark `needs_review`

### 13.4 Đáp án thiếu
- Câu có anchor "Câu N:" nhưng không có A/B/C/D nào
- → Có thể là tự luận → classify as ESSAY
- Hoặc OCR miss → trigger VLM verify

### 13.5 Câu hỏi vắt > 2 trang
- Hiếm nhưng có thể (tự luận dài)
- Snake walker xử lý native bằng `page_indices = [N, N+1, N+2]`
- Crop strategy: stitch nhiều images

### 13.6 Đề thi nhiều môn (Listening + Reading + Writing)
- Mỗi môn có thể có structure khác
- VLM hiểu context để classify đúng type
- Group passage chỉ ra phạm vi câu hỏi liên quan

---

## 14. Performance breakdown (RTX 5090)

| Stage | Tốc độ/trang | Thời gian 10 trang |
|---|---|---|
| Preprocess | 0.1s | 1s |
| PaddleOCR | 0.5-1s | 5-10s |
| Anchor Extract | <0.05s | <0.5s |
| VLM Verify (lazy ~15% pages) | 5-8s | 8-12s |
| Snake Walk | <0.1s | <1s |
| Classify | <0.1s | <1s |
| Crop | 0.5s | 5s |
| MinIO Upload | 0.5s | 5s |
| **Total** | **~2-5s** | **25-35s** |

**Throughput**: ~100-150 đề (10-page)/giờ trên RTX 5090.

---

## 15. Debugging tools

### 15.1 Preview PDF với bbox màu
Generate PDF với rectangles vẽ chồng:
- 🔴 Group bbox
- 🔵 Question full bbox  
- 🟣 Content bbox
- 🟢 Answer bbox

Lưu MinIO → admin xem qua URL `preview_pdf_url`.

### 15.2 Verbose logging
```python
# .env
LOG_LEVEL=DEBUG
```

→ Log per page:
```
[INFO] Page 1: 12 blocks, 8 anchors (5 question, 20 answer)
[DEBUG] Anchor: Q1 at (100, 200), Q2 at (100, 400), Q3 at (100, 800)
[INFO] Snake walk: created 50 questions, 0 cross-page
```

### 15.3 Step-by-step output
Mỗi stage có thể dump intermediate:
```bash
python scripts/parse_cli.py input/de.pdf --dump-stages output/debug/
# Tạo:
# output/debug/01_pages/                page images
# output/debug/02_blocks.json           PaddleOCR output
# output/debug/03_anchors.json          extracted anchors
# output/debug/04_questions.json        snake walk output
# output/debug/05_classified.json       với types
# output/debug/06_cropped/              các ảnh crop
```

---

## 16. Tóm tắt

| Stage | Tool | Vai trò |
|---|---|---|
| 1. Preprocess | PyMuPDF + OpenCV | PDF→images, deskew |
| 2. Parse | PaddleOCR PP-StructureV3 | Layout + OCR + bbox |
| 3. Anchor | Python regex | Tìm vị trí marker |
| 4. Verify | Qwen3-VL (lazy) | Bổ sung anchor bị miss |
| 5. Snake Walk | Pure Python | Group blocks, cross-page native |
| 6. Classify | Rule + VLM | Loại câu hỏi |
| 7a. Crop | PIL | Cắt ảnh question/answer |
| 7b. Upload | MinIO SDK | Lưu + URL |

**Strengths của P4**:
- ✅ Bbox pixel-perfect (PaddleOCR)
- ✅ Include ALL visual content (figure, formula, table)
- ✅ Cross-page native (Snake)
- ✅ Robust với edge case (VLM fallback)
- ✅ Production-ready (FastAPI + MinIO)
