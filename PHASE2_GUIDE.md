# Phase 2 Implementation Guide — Snake Walker + Classifier + Cropper

> **Đối tượng đọc:** AI coding agent (Antigravity) thực hiện Phase 2 của project `exam_parser_paddle`.
> **Mục tiêu:** Biến danh sách `Anchor` (đã detect chính xác ở Phase 1) + `Block` (OCR) thành cấu trúc `Exam` hoàn chỉnh, kèm **ảnh crop** từng câu hỏi / đáp án / nhóm.
> **Ngôn ngữ giao tiếp & comment code:** Tiếng Việt. **Ngôn ngữ:** Python 3.10+.

---

## 0. BẮT BUỘC ĐỌC TRƯỚC KHI CODE

1. **KHÔNG sửa** các file đã hoàn thiện ở Phase 1 trừ khi guide này yêu cầu rõ:
   - `src/services/preprocess.py` ✅ DONE
   - `src/services/paddle_parser.py` ✅ DONE
   - `src/services/anchor_extractor.py` ✅ DONE (chỉ đọc tham khảo)
   - `src/schemas/*.py` ✅ DONE — **dùng nguyên các schema này, không đổi field.**
2. **Mỗi khi hoàn thành 1 phần, ghi vào `CHANGELOG.md`** theo format `[Phase 2.x] - YYYY-MM-DD - Title` (xem các entry Phase 1.x có sẵn để bắt chước format: Mục đích / Đã sửa / Kết quả / Trade-off).
3. **Không OCR lại, không gọi VLM** trong Phase 2. Tất cả input đã có sẵn dạng `Block` + `Anchor`. VLM để dành Phase 3.
4. Sau khi code xong, phải **chạy được** trên 2 đề mẫu: `input/demau_toan8.pdf` (2 trang, 7 câu tự luận+TN) và `input/demau_tienganh.pdf` (16 trang, 50 câu TN + passage). Xem mục §7 Acceptance.

---

## 1. Bối cảnh & luồng dữ liệu

Pipeline tổng thể (phương án P4):

```
PDF/ảnh
  └─[1] preprocess  → list[PIL.Image]            (1 ảnh / trang, DPI 300)
       └─[2] paddle_parser → list[list[Block]]    (blocks per page, có bbox + OCR text)
            └─[3] anchor_extractor → list[Anchor]  (Câu N, A./B./C./D., Phần I, ...)
                 └─[4] SNAKE WALKER      ← PHASE 2  (gom anchor → Question/Group)
                      └─[5] CLASSIFIER   ← PHASE 2  (gán QuestionType)
                           └─[6] CROPPER ← PHASE 2  (cắt ảnh từ page images)
                                └─[7] (Phase 3 VLM, Phase 4 MinIO)
```

**Input của Phase 2** (đã chạy xong Phase 1, có sẵn trong memory):
- `images: list[PIL.Image]` — ảnh từng trang (pixel coords của mọi bbox khớp với ảnh này).
- `blocks_per_page: list[list[Block]]` — output `PaddleParser.parse_pages()`.
- `anchors: list[Anchor]` — output `extract_anchors()`. **Chưa sort.**

**Output của Phase 2:** một object `Exam` (xem §2) với:
- `exam.questions` đầy đủ: number, type, page_indices, content_text, bbox vùng câu (chưa upload MinIO).
- `exam.groups` cho passage / "Phần I" / "Mark the letter...".
- Mỗi `Question.full_image` / `content_image` / `answers[].image` là `CroppedImage` nhưng **MinIO key/url để rỗng** (Phase 4 mới upload). Crop ra file PNG/WEBP lưu local `output/{exam_id}/crops/` để review.
- Một ảnh **debug overlay** mỗi trang: vẽ bbox màu theo spec §6.4.

---

## 2. Schemas có sẵn (KHÔNG đổi field — chỉ điền giá trị)

Đọc trực tiếp: [src/schemas/exam.py](src/schemas/exam.py), [src/schemas/anchor.py](src/schemas/anchor.py), [src/schemas/block.py](src/schemas/block.py).

Tóm tắt các field quan trọng:

```python
# block.py
BBox = tuple[float, float, float, float]   # (x1, y1, x2, y2) pixel
Block: page_index, block_index, type(BlockType), bbox, lines[TextLine], extra
  .text         -> str (concat các line)
  .is_text_block / .is_visual_block
BlockType: TEXT/TITLE/LIST/TABLE/FIGURE/EQUATION/HEADER/FOOTER/REFERENCE/OTHER
TextLine: text, bbox, confidence

# anchor.py
Anchor: page_index, type(AnchorType), bbox, text, value, confidence, source
  .number              -> Optional[int]  (parse value nếu là số)
  .global_position()   -> (page_index, y_top)   # DÙNG ĐỂ SORT
AnchorType: QUESTION/ANSWER/SUB_QUESTION/GROUP_HEADER/PASSAGE/METADATA/FOOTER

# exam.py
QuestionType: MCQ_SINGLE/MCQ_MULTI/TRUE_FALSE/FILL_BLANK/SHORT_ANSWER/
              ESSAY/MATCHING/ORDERING/READING_COMPREHENSION/UNKNOWN
GroupType: PASSAGE/SECTION_PART/INSTRUCTION/UNKNOWN
CroppedImage: bbox, page_indices[int], minio_key, url, width, height, size_bytes
Answer: label, image(CroppedImage), text, is_correct
Question: id, number, type, group_id, full_image, content_image, answers[],
          content_text, has_figure, has_formula, has_table,
          page_indices[int], confidence, needs_review
Group: id, type, header_image, header_text, passage_image, passage_text, question_ids[]
Exam: exam_id, source_file, n_pages, metadata, groups[], questions[],
      preview_pdf_url, n_questions, n_groups, n_essay, n_mcq, avg_confidence
```

> ⚠️ `CroppedImage` đang require `minio_key`, `width`, `height`, `size_bytes`. Phase 2 chưa upload MinIO → set `minio_key=""`, `url=` đường dẫn file local (vd `"crops/q1_full.png"`), điền `width/height/size_bytes` thật từ file PNG đã ghi. Không đổi schema.

---

## 3. COMPONENT 1 — Snake Walker

**File:** `src/services/snake_walker.py` (hiện là placeholder, viết mới hoàn toàn).

### 3.1. Ý tưởng "Snake"
Coi toàn bộ document là **một dải liên tục** nối các trang lại (giống con rắn bò qua các trang). Sort mọi anchor theo `global_position() = (page_index, y_top)`. Câu hỏi N kéo dài từ anchor "Câu N" cho đến ngay trước anchor "Câu N+1" (hoặc hết document) — **kể cả khi vắt qua nhiều trang**.

### 3.2. Hàm chính
```python
def snake_walk(
    blocks_per_page: list[list[Block]],
    anchors: list[Anchor],
    page_heights: list[float],   # chiều cao pixel mỗi trang (img.height)
    page_widths: list[float],
) -> tuple[list[Question], list[Group]]:
    ...
```

### 3.3. Thuật toán

**Bước 1 — Sort & lọc anchor.**
- `q_anchors = sorted([a for a in anchors if a.type == QUESTION], key=lambda a: a.global_position())`
- Tương tự lấy `group_anchors` (GROUP_HEADER), `answer_anchors` (ANSWER), `sub_anchors` (SUB_QUESTION).
- Bỏ FOOTER, METADATA khỏi quá trình gom (METADATA dùng riêng cho `ExamMetadata` ở §3.7).

**Bước 2 — Xác định ranh giới mỗi câu (boundary).**
Với mỗi `q = q_anchors[i]`:
- `start = q.global_position()`
- `end = q_anchors[i+1].global_position()` nếu còn câu sau, ngược lại `end = (last_page_index, +inf)`.
- **Vùng câu** = tất cả nội dung có global position trong nửa khoảng `[start, end)`.

Định nghĩa thứ tự global để so sánh:
```python
def gpos(page_index, y):  # khóa sort toàn cục
    return (page_index, y)
# nội dung X thuộc câu nếu: start <= (X.page, X.y_top) < end
```

**Bước 3 — Gom block & answer vào câu.**
Với mỗi câu, duyệt mọi `Block` (mọi trang) có `(block.page_index, block.bbox[1])` nằm trong `[start, end)`:
- Lưu list block thuộc câu → để tính bbox vùng câu + content_text + cờ has_figure/has_table/has_formula.
- `page_indices` = sorted(unique page_index của các block thuộc câu).

Với answer: các `answer_anchors` nằm trong `[start, end)` → tạo `Answer(label=value)`. Sort theo global position. (A trước B trước C trước D — nhưng KHÔNG ép, lấy theo thứ tự xuất hiện thực tế; nếu trùng label thì giữ cái đầu + đánh dấu `needs_review=True`.)

**Bước 4 — Tách content vs answers (cho crop §6).**
Trong vùng câu, "content" (phần đề bài) = từ đầu câu đến trước anchor đáp án **đầu tiên** (answer hoặc sub_question đầu tiên). Phần còn lại là vùng đáp án. Nếu câu không có đáp án nào → toàn bộ là content (tự luận).
Lưu lại các mốc y để Cropper dùng:
- `content_region`: (page bắt đầu câu, y_top câu) → (page, y của đáp án đầu) hoặc hết câu.
- mỗi answer: vùng từ anchor đáp án đó → trước đáp án kế tiếp (hoặc hết câu).

**Bước 5 — Tạo `Question`.**
```python
Question(
  id=f"q{n}", number=n, type=QuestionType.UNKNOWN,  # type điền ở Component 2
  content_text=<concat text các block phần content>,
  page_indices=<list>,
  answers=<list Answer, mỗi cái có .text = OCR text vùng đáp án>,
  has_figure=<có block FIGURE trong vùng câu?>,
  has_table=<có block TABLE?>,
  has_formula=<có block EQUATION?>,
  confidence=<min confidence các anchor liên quan>,
  needs_review=<True nếu phát hiện bất thường, xem §3.6>,
)
```
**LƯU Ý:** Lưu kèm (ngoài schema, trong 1 dict phụ hoặc field `extra` tạm) các **bbox region** đã tính ở Bước 4 để truyền sang Cropper. Gợi ý: trả thêm một map `question_regions: dict[str, QuestionRegions]` song song, hoặc tạo dataclass nội bộ `_QuestionLayout` chứa region rồi Cropper nhận nó. **Không nhồi pixel-region vào schema Exam.**

### 3.4. Gắn câu vào Group
- `group_anchors` chia document thành các "khoảng nhóm": group G áp dụng cho mọi câu có global position `>= G.start` và `< (group kế tiếp).start`.
- Tạo `Group(id=f"g{k}", type=<phân loại §3.5>, header_text=G.text, question_ids=[...])`.
- Gán `question.group_id = g.id` cho các câu trong nhóm.
- **Quan trọng — passage:** Với group là PASSAGE (đề đọc hiểu tiếng Anh "Read the following passage..."), đoạn văn passage thường nằm **giữa group header và câu hỏi đầu tiên** của nhóm. Vùng đó (các block text dài, không phải anchor) → `group.passage_text` + sẽ crop thành `group.passage_image` ở §6.

### 3.5. Phân loại GroupType (rule, từ text header đã strip dấu)
| Điều kiện text header (lowercase, đã strip dấu) | GroupType |
|---|---|
| chứa `read the following`, `doc doan`, `doc van ban`, `dua vao` | PASSAGE |
| bắt đầu bằng `phan` (Phần I/II...) | SECTION_PART |
| chứa `mark the letter`, `choose the`, `cho doan`, hướng dẫn chung | INSTRUCTION |
| còn lại | UNKNOWN |

> Lưu ý đề Anh: có nhiều header "Mark the letter..." (INSTRUCTION) lẫn "Read the following passage" (PASSAGE). Một câu có thể nằm dưới INSTRUCTION mà không có passage — đó là bình thường, group vẫn tạo nhưng `passage_text=""`.

### 3.6. Cờ `needs_review` — bật True khi:
- Số câu không liên tục (vd nhảy từ Câu 4 sang Câu 7 → thiếu 5,6) — đánh dấu các câu quanh chỗ đứt.
- Trùng số câu.
- Câu không có content block nào (vùng rỗng).
- Câu MCQ nhưng số đáp án ≠ 4 (xem Component 2).

### 3.6b. ⚠️ EDGE CASE QUAN TRỌNG — Đề có phần "Lời giải chi tiết" (Azota)

Một số đề (vd Azota: `de_mau_azota_toan_THPT.pdf`) có cấu trúc **3 phần**, chính trang 1 mô tả:
1. **Phần câu hỏi**: mỗi câu bắt đầu "Câu N:", kết thúc bằng chữ **"Hết"**.
2. **Phần đáp án**: bảng dạng `1.A, 2.B, 3.C...`.
3. **Phần lời giải chi tiết**: **các câu LẠI bắt đầu bằng "Câu N:"** + kết thúc mỗi câu bằng **"Chọn A/B/C/D."**.

→ Hệ quả: anchor extractor (đúng) phát hiện **"Câu N" 2 lần** (94 anchor cho 50 câu thật). Quan sát thực tế: 49 số unique, mỗi số xuất hiện ~2 lần.

**Snake Walker BẮT BUỘC tách và chỉ giữ phần câu hỏi.** Thuật toán phát hiện ranh giới:
- Tìm mốc kết thúc phần hỏi: anchor/line chứa **"Hết"** (`het`, `---hết---`), HOẶC line chứa **"bảng đáp án"**, HOẶC dòng mô tả đáp án dạng bảng `1.A 2.B ...`, HOẶC tiêu đề **"lời giải chi tiết" / "giải chi tiết" / "hướng dẫn giải" / "đáp án và lời giải"**.
- Mọi anchor QUESTION nằm **sau** mốc đó → thuộc phần lời giải → **loại khỏi `questions`** (hoặc gom vào `exam.solutions` nếu muốn giữ — nhưng schema hiện chưa có field này, nên Phase 2 chỉ cần **DROP**, log số lượng đã loại).
- **Fallback khi không tìm thấy mốc rõ ràng:** nếu dãy số câu **không tăng đơn điệu** — tức gặp "Câu k" mà k <= max number đã thấy trước đó (số câu nhảy lùi/lặp lại từ đầu) → coi đó là điểm bắt đầu phần lời giải, drop từ đó trở đi. Đây là tín hiệu mạnh & tổng quát (đề Anh/Toán8 số luôn tăng nên không bị ảnh hưởng).
- Sau khi tách: với Azota phải còn đúng **50 câu** (1..50), không dup.

> Bonus (KHÔNG làm ở Phase 2, ghi chú cho Phase sau): phần lời giải chứa đáp án đúng ("Chọn C.") → sau này map vào `Answer.is_correct`. Hiện tại scope = chưa cần liên kết đáp án → bỏ qua.

### 3.7. Metadata (tiện làm luôn)
Từ `anchors` type METADATA + vài block đầu trang 1, parse nhẹ bằng regex:
- `ma_de` ← "Mã đề 123"; `thoi_gian_phut` ← "Thời gian: 60 phút"; `mon` ← "Môn: ..."; `truong`; `nam_hoc`.
- `tong_so_cau` = len(questions). Điền vào `ExamMetadata`. Không có thì để None.

---

## 4. COMPONENT 2 — Question Type Classifier

**File:** `src/services/question_classifier.py` (viết mới).

```python
def classify(question: Question, group: Optional[Group]) -> QuestionType:
    ...
def classify_all(questions: list[Question], groups: list[Group]) -> None:
    # gán in-place question.type
```

### 4.1. Cây quyết định (rule-based, KHÔNG dùng VLM)

```
1. Nếu question.group_id trỏ tới group.type == PASSAGE:
      → READING_COMPREHENSION   (và thường vẫn có A/B/C/D)
      (nếu cần phân biệt: vẫn có 4 đáp án thì là đọc hiểu trắc nghiệm → giữ READING_COMPREHENSION)

2. Đếm n_answers = len(question.answers) với label in {A,B,C,D} (đáp án in hoa).
   - n_answers >= 3 (thường =4):
        → MCQ_SINGLE   (mặc định trắc nghiệm 1 đáp án)
        → MCQ_MULTI nếu content_text chứa dấu hiệu "chọn nhiều", "chọn tất cả", "(chọn 2)" ...
   - n_answers == 0:
        Xét sub_question (a) b) c) d) đếm được trong câu (truyền kèm từ snake_walker):
          - có >=2 sub-question dạng a)/b)/c)/d):
                → TRUE_FALSE  (dạng đúng/sai 4 ý — phổ biến đề THPT mới)
          - không có sub:
                → phân biệt ESSAY vs FILL_BLANK vs SHORT_ANSWER:
                    * content có chỗ trống "____", "…", "(...)" → FILL_BLANK
                    * content ngắn (< ~15 từ) và yêu cầu tính/điền số → SHORT_ANSWER
                    * còn lại (giải, chứng minh, trình bày, đoạn dài) → ESSAY
   - n_answers in {1,2}:
        → bất thường, set UNKNOWN + question.needs_review = True
```

### 4.2. Từ khóa nhận diện (strip dấu, lowercase) — gợi ý, mở rộng được
- MCQ_MULTI: `chon nhieu`, `chon tat ca`, `chon 2`, `select all`, `more than one`.
- FILL_BLANK: regex chỗ trống `_{2,}`, `\.{3,}`, `\(\s*\.\.\.\s*\)`, `…`.
- ESSAY: `giai`, `chung minh`, `trinh bay`, `tinh`, `viet doan`, `phan tich`, `giai thich`, `giai cac phuong trinh`.
- Đề Toán mẫu: "Câu 5 (4đ). Giải các phương trình sau", "Câu 6 (3đ)", "Câu 7 (1đ). Giải phương trình" → **ESSAY**. "Câu 1-4" có A/B/C/D → **MCQ_SINGLE**.

### 4.3. Cập nhật stats Exam sau classify
- `n_mcq` = số câu type ∈ {MCQ_SINGLE, MCQ_MULTI, READING_COMPREHENSION, TRUE_FALSE}.
- `n_essay` = số câu type ∈ {ESSAY, SHORT_ANSWER, FILL_BLANK}.
- `avg_confidence` = mean(question.confidence).

---

## 5. (tham khảo) Dữ liệu region truyền giữa Walker → Cropper

Tạo dataclass nội bộ (không phải Pydantic schema chính):
```python
@dataclass
class Region:
    page_index: int
    bbox: BBox            # vùng trên 1 trang cụ thể
@dataclass
class MultiRegion:        # 1 vùng logic có thể trải nhiều trang
    parts: list[Region]   # mỗi part nằm trọn trong 1 trang
@dataclass
class QuestionLayout:
    question_id: str
    full: MultiRegion             # toàn câu (content + answers)
    content: MultiRegion          # chỉ đề bài
    answers: list[tuple[str, MultiRegion]]   # (label, region)
```
`snake_walk()` trả về `(questions, groups, layouts: dict[str, QuestionLayout], group_layouts: dict[str, MultiRegion])`.

**Tính bbox 1 trang:** với tập block thuộc 1 trang trong 1 vùng, bbox = (min x1, min y1, max x2, max y2), **nới padding** `PAD=8px` mỗi cạnh (clamp trong [0, page_w/h]). Nếu vùng trải nhiều trang → tách thành nhiều `Region` (mỗi trang 1 part); part ở trang đầu kéo từ y_top câu xuống đáy trang, part trang cuối từ đỉnh tới y_bottom, part trang giữa là full chiều cao.

---

## 6. COMPONENT 3 — Cropper

**File:** `src/services/cropper.py` (viết mới). Dùng PIL.

```python
def crop_question(layout: QuestionLayout, images: list[PIL.Image], out_dir: Path)
    -> tuple[CroppedImage, CroppedImage, list[Answer]]:
    # trả (full_image, content_image, answers có .image)
def crop_group_passage(mregion: MultiRegion, images, out_dir) -> CroppedImage
def crop_all(exam, layouts, group_layouts, images, out_dir) -> None  # điền in-place
```

### 6.1. Crop 1 vùng (MultiRegion) → 1 ảnh PNG
- Nếu `len(parts)==1`: crop thẳng `images[part.page_index].crop(bbox)`.
- Nếu nhiều part (vắt trang): crop từng part rồi **ghép dọc** (vertical stack). Width = max width các part (canh trái, nền trắng pad). Trả 1 ảnh ghép.
- Lưu PNG vào `out_dir / "crops" / f"{name}.png"`. (PNG để không mất nét công thức; WEBP để Phase 4.)
- Tạo `CroppedImage(bbox=<bbox part đầu hoặc bao>, page_indices=<list page của parts>, minio_key="", url=<relative path>, width, height, size_bytes)`.

### 6.2. Đặt tên file
- `q{number}_full.png`, `q{number}_content.png`, `q{number}_{label}.png` (vd `q1_A.png`), `g{k}_passage.png`.

### 6.3. Quy tắc crop theo loại câu
- MCQ: crop `full` (cả đề + 4 đáp án), `content` (chỉ đề), và **mỗi đáp án 1 ảnh** (theo spec màu 🟢).
- ESSAY/SHORT/FILL: chỉ `full` = `content` (không có đáp án). `answers=[]`.
- READING_COMPREHENSION: crop `full`+`content`+đáp án như MCQ; passage crop riêng ở `group.passage_image` (dùng chung cho cả nhóm, không lặp lại trong từng câu).

### 6.4. Debug overlay (BẮT BUỘC — để review thủ công)
Tạo `output/{exam_id}/overlay/page_XX.png`: vẽ bbox màu lên ảnh gốc theo spec người dùng:
- 🔴 **Đỏ** = vùng Group (group header + passage).
- 🔵 **Xanh dương** = vùng Question (full: content + answers).
- 🟣 **Tím** = vùng content câu hỏi (chỉ đề bài).
- 🟢 **Xanh lá** = mỗi đáp án.

Dùng `PIL.ImageDraw.rectangle(bbox, outline=color, width=3)` + ghi nhãn nhỏ (số câu / label) góc trên trái mỗi box. Màu RGB: đỏ `(220,30,30)`, xanh dương `(30,90,220)`, tím `(150,30,200)`, xanh lá `(30,160,60)`.

---

## 7. Tích hợp CLI + Acceptance

### 7.1. Cập nhật `scripts/parse_cli.py`
Thêm Stage 4-6 **sau** Stage 3 (anchor). Giữ nguyên Stage 1-3. Sau anchor:
```
[4/6] Snake Walker  → questions, groups, layouts
[5/6] Classifier    → gán type
[6/6] Cropper       → crop ảnh + overlay
```
Ghi thêm output:
```
output/{exam_id}/
  ├── blocks.json     (Phase 1)
  ├── anchors.json    (Phase 1)
  ├── exam.json       ← MỚI: Exam.model_dump_json(indent=2, ensure_ascii=False)
  ├── summary.txt     (cập nhật: + breakdown question type, group)
  ├── crops/          ← MỚI: ảnh từng câu/đáp án/passage
  ├── overlay/        ← MỚI: page_XX.png có bbox màu
  └── pages/          (nếu --save-images)
```
Thêm flag `--no-crop` để chạy nhanh chỉ tới classify (debug).

### 7.2. Tiêu chí PASS (chạy thật trên 2 đề)

**`input/demau_toan8.pdf`:**
- `n_questions == 7`, numbers = 1..7 liên tục.
- Câu 1-4 → `MCQ_SINGLE` (mỗi câu 4 đáp án A/B/C/D).
- Câu 5,6,7 → `ESSAY` (hoặc SHORT_ANSWER cho câu ngắn) — KHÔNG được UNKNOWN.
- Có ít nhất 1 group `SECTION_PART` ("Phần I", "Phần II").
- Crop: tồn tại `q1_full.png`..`q7_full.png`, các `q1_A..D.png`. Overlay 2 trang vẽ đúng màu.

**`input/demau_tienganh.pdf`:**
- `n_questions == 50`, numbers = 1..50 liên tục.
- Câu thuộc đoạn "Read the following passage..." → `READING_COMPREHENSION`, có `group.passage_image`.
- Đa số câu → `MCQ_SINGLE` 4 đáp án.
- Crop full/content/answers cho các câu, không crash trên câu **vắt trang** (passage dài).
- Overlay 16 trang.

**`input/de_mau_azota_toan_THPT.pdf` (23 trang, có phần lời giải):**
- Anchor detect ra ~94 "Câu" (50 hỏi + ~44 lời giải) → sau Snake Walker (§3.6b) **`n_questions == 50`**, numbers 1..50, **không dup**.
- Phần lời giải chi tiết (trang ~13-23) bị **DROP**, log rõ "đã loại N câu thuộc phần lời giải".
- Câu 1-50 → đa số `MCQ_SINGLE` (4 đáp án A/B/C/D), một số câu có hình (đồ thị) → `has_figure=True`.
- Crop không crash trên câu có hình/công thức.

### 7.3. Robustness (không được crash)
- Câu cuối document (end = +inf): vẫn crop tới đáy trang cuối.
- Đáp án xếp 2 cột (A,B trái; C,D phải) như đề Anh: bbox mỗi đáp án vẫn ôm đúng từng đáp án (dựa anchor + block cùng dòng). Nếu khó tách cột → chấp nhận crop theo dòng, set `needs_review=True`.
- Trang trắng / câu rỗng → bỏ qua an toàn, log cảnh báo.
- Bbox vượt biên ảnh → clamp.

---

## 8. Thứ tự thực hiện đề xuất & CHANGELOG

1. **Phase 2.1** — `snake_walker.py` + region dataclass. Test: in ra số câu, group, page_indices (chưa crop). → ghi CHANGELOG.
2. **Phase 2.2** — `question_classifier.py`. Test: in breakdown type cho 2 đề. → CHANGELOG.
3. **Phase 2.3** — `cropper.py` + overlay + tích hợp CLI + `exam.json`. Test full 2 đề theo §7.2. → CHANGELOG.

Mỗi entry CHANGELOG ghi: **Mục đích / File đã thêm-sửa / Kết quả test (số liệu thật) / Trade-off**.

---

## 9. Ràng buộc kỹ thuật
- Chạy trên WSL2, conda env `exam_parser_paddle`, Paddle **CPU** (Blackwell sm_120 chưa support GPU). Phase 2 không động tới GPU.
- Chỉ dùng lib đã có trong `requirements.txt`: `Pillow`, `numpy`, `pydantic`, `click`, `loguru`, `PyMuPDF`, `opencv-python`. **Không thêm dependency mới** cho Phase 2 (PIL đủ để crop + vẽ overlay; dùng font mặc định `ImageFont.load_default()` nếu cần label).
- Toàn bộ pixel bbox đang ở hệ toạ độ ảnh DPI 300 — Cropper crop trực tiếp trên `images` (cùng list đã đưa vào paddle_parser). KHÔNG render lại PDF.
- Không gọi MinIO, không gọi Ollama/VLM ở Phase 2.

---

## 10. Định nghĩa "xong Phase 2"
✅ `python scripts/parse_cli.py input/demau_toan8.pdf` và `... demau_tienganh.pdf` chạy không lỗi, sinh `exam.json` + `crops/` + `overlay/`, đạt toàn bộ tiêu chí §7.2, và 3 entry CHANGELOG Phase 2.1–2.3 đã ghi.
