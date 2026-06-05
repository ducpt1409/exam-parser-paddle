# PHASE 3 — VLM Semantic Layer (Qwen3-VL-32B) — Hướng dẫn cho antigravity

> Đọc kỹ toàn bộ file trước khi code. Tuân thủ triết lý P4: **KHÔNG OCR toàn văn**. PaddleOCR + Snake Walker (Phase 1-2) đã lo phần hình học (bbox, crop). Phase 3 chỉ thêm **tầng ngữ nghĩa**, gọi VLM **lazy** (chỉ khi cần), để **vá lỗi** mà OCR không làm được.

---

## 0. Bối cảnh & nguyên tắc bất di bất dịch

- **Không phá Phase 1-2.** Mọi thay đổi Phase 3 phải tách module riêng. Pipeline cũ (Paddle → anchor → snake_walk → classify → crop) chạy y nguyên. VLM là 1 stage CHÈN THÊM sau crop.
- **Lazy.** Tuyệt đối KHÔNG gọi VLM cho mọi câu. Chỉ gọi cho câu `needs_review == True` (hoặc theo điều kiện ở §3). Đề Toán 8 / Tiếng Anh hầu hết câu đã đúng → gần như không tốn call VLM. Đề azota (math-dense) là nơi VLM gánh phần lớn.
- **VLM bổ sung JSON, KHÔNG thay ảnh.** Ảnh crop vẫn là deliverable chính. VLM điền các trường text/semantic mà OCR đọc sai hoặc thiếu (đáp án mất, công thức nát, loại câu, cờ figure/formula/table).
- **Fail-safe.** Ollama lỗi/timeout → giữ nguyên kết quả Phase 2, set `needs_review=True`, log rõ. KHÔNG để pipeline crash.
- **Mọi cập nhật ghi `CHANGELOG.md`** (Mục đích / Vấn đề / Giải pháp / Kết quả / Còn lại) — bắt buộc như các phase trước.
- **Ghi log ra file** trong folder output. Thêm `vlm.log` riêng cho các call VLM (prompt rút gọn + response + thời gian). Nếu baseline chưa có `parse.log` thì thêm luôn dòng `logger.add(out/"parse.log", ...)` trong `parse_cli.py`.

> **Baseline = Phase 2.8.** Đã chủ ý quay về 2.8 (2.9/2.10 làm sai nhận diện câu đề Tiếng Anh). Các fix hình học của 2.9 (`answer_block_top`) và 2.10 (graph band) **KHÔNG khôi phục ở rule engine** — Phase 3 VLM gánh thay qua cơ chế re-crop ở §6.4. Riêng fix group-regex word-boundary của 2.10 (`r"^\s*(phan)\s+(?:[ivxlcdm]+|\d+)\b"`) có thể cherry-pick lại vì nó độc lập, tránh "group giả" nuốt câu.

### Môi trường chạy (KHÔNG đổi)
- WSL, RTX 5090, conda env `exam_parser_paddle`, Paddle CPU mode.
- Ollama đã chạy model `qwen3-vl:32b-instruct` (mixed GPU). Config sẵn trong `src/core/config.py`:
  - `ollama_host="http://localhost:11434"`, `ollama_vlm_model="qwen3-vl:32b-instruct"`, `ollama_timeout=120`
  - `use_vlm_verification=True`, `use_vlm_type_classify=True`

---

## 1. Vấn đề Phase 3 cần giải (rút từ test thực tế)

Từ 3 đề mẫu sau Phase 2.10, các lỗi còn lại đều là **lỗi OCR/hình học mà rule engine không tự sửa được** — đúng phần việc của VLM:

| # | Triệu chứng (đề) | VLM làm gì |
|---|---|---|
| 1 | azota: câu chỉ detect 1-3 đáp án, mất A/C (OCR đọc công thức ra rác) | Đọc lại ảnh, xác nhận đủ 4 đáp án + text từng đáp án |
| 2 | azota: content cắt thiếu tử số phân số / công thức nát | Trả `content_text` (LaTeX) + cờ `has_formula` |
| 3 | azota q45/q3: dưới đề có đồ thị | Xác nhận `has_figure`, kiểm tra ảnh full có chứa trọn đồ thị không (gợi ý mở rộng vùng) |
| 4 | Mọi đề: loại câu mơ hồ (classifier rule đoán sai) | `classify_question_type` chính xác |
| 5 | azota: đáp án dính nhầm / lệch | Đối chiếu số đáp án VLM thấy vs số đã crop → cờ review nếu lệch |

**Không thuộc Phase 3** (để Phase 4+): chấm đáp án đúng (`is_correct`) từ bảng đáp án cuối đề; upload MinIO; API serve.

---

## 2. Kiến trúc & file cần tạo

```
src/services/
  vlm_client.py        # (THAY placeholder) low-level Ollama client
  vlm_verifier.py      # (MỚI) orchestrator lazy: chọn câu → gọi VLM → merge
src/schemas/
  vlm.py               # (MỚI) pydantic schema request/response VLM
scripts/
  parse_cli.py         # (SỬA) chèn Stage 7 sau cropper
```

Stage trong pipeline sau khi thêm:
```
[1] Preprocess  [2] PaddleOCR  [3] Anchor  [4] Snake Walker
[5] Classify    [6] Crop       [7] VLM Verify (MỚI, lazy)
```

---

## 3. Điều kiện gọi VLM (lazy gating)

Trong `vlm_verifier.py`, hàm `select_questions(exam) -> list[Question]` chọn câu cần gọi VLM. Gọi khi BẤT KỲ điều kiện:

1. `q.needs_review == True` (snake_walker đã gắn: đáp án regex_inline, hoặc 1-3 đáp án, hoặc anchor recovered).
2. `q.type == UNKNOWN` (và `use_vlm_type_classify`).
3. MCQ nhưng `len(q.answers) not in (0, 4)` — số đáp án bất thường (2,3,5...).
4. `q.confidence < 0.6`.

Câu KHÔNG match điều kiện → bỏ qua hoàn toàn (không call). Log: `VLM gating: {n_selected}/{n_total} câu sẽ gọi VLM`.

> Cho phép tắt toàn bộ bằng `use_vlm_verification=False` (chạy nhanh, debug Phase 2).

---

## 4. `src/schemas/vlm.py` — Structured output

Dùng pydantic + Ollama `format=<json schema>` để ép model trả JSON đúng cấu trúc (không parse tay).

```python
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

class VLMQuestionType(str, Enum):
    MCQ_SINGLE = "trac_nghiem_1_dap_an"
    MCQ_MULTI  = "trac_nghiem_nhieu_dap_an"
    TRUE_FALSE = "dung_sai"
    FILL_BLANK = "dien_dap_an"
    SHORT_ANSWER = "tu_luan_ngan"
    ESSAY = "tu_luan_dai"
    READING_COMPREHENSION = "doc_hieu"
    UNKNOWN = "unknown"

class VLMAnswer(BaseModel):
    label: str                     # "A".."D"
    text: str = ""                 # nội dung đáp án (LaTeX nếu công thức)

class VLMQuestionResult(BaseModel):
    question_type: VLMQuestionType
    n_answers: int                 # số đáp án VLM nhìn thấy trong ảnh
    answers: list[VLMAnswer] = Field(default_factory=list)
    content_text: str = ""         # đề bài, LaTeX cho công thức
    has_figure: bool = False       # có hình/đồ thị/sơ đồ
    has_formula: bool = False
    has_table: bool = False
    content_complete: bool = True  # ảnh có chứa TRỌN đề (không bị cắt trên/dưới)
    figure_complete: bool = True   # nếu có hình: hình có bị cắt mép không
    notes: str = ""                # ghi chú bất thường (tùy chọn)
```

> `content_complete` / `figure_complete` = tín hiệu để §6 quyết định re-crop. Đây là cách VLM "sửa vùng" gián tiếp mà không cần trả tọa độ pixel.

---

## 5. `src/services/vlm_client.py` — Low-level client

Yêu cầu:
- Gọi Ollama `POST {ollama_host}/api/chat` với `model=ollama_vlm_model`, `stream=False`, `format=<schema VLMQuestionResult>`, `options={"temperature":0}`.
- Ảnh: đọc file PNG crop → base64 → field `images` của message.
- Timeout = `settings.ollama_timeout`. Retry 1 lần khi lỗi mạng/timeout. Lỗi → trả `None` (caller xử lý fail-safe).
- Async: dùng `httpx.AsyncClient`; có hàm `analyze_question_async()` + wrapper sync `analyze_question()`. Verifier chạy batch song song (giới hạn đồng thời, vd `asyncio.Semaphore(2)` vì model 32B nặng GPU).
- Ghi `vlm.log`: question_id, kích thước ảnh, thời gian, n_answers, type, có lỗi không.

### Prompt (tiếng Việt, ép súc tích)
System:
```
Bạn là trợ lý phân tích đề thi. Bạn CHỈ nhìn ảnh và mô tả CẤU TRÚC, KHÔNG giải bài.
Trả về JSON đúng schema. Công thức toán viết bằng LaTeX. Nếu không chắc, để giá trị mặc định.
```
User (kèm ảnh full_image của câu + context):
```
Đây là ảnh 1 câu hỏi (số {number}). Phase trước đoán: loại={type}, số đáp án={n_ans}.
Hãy xác định:
- Loại câu hỏi (question_type).
- Số đáp án trắc nghiệm A/B/C/D nhìn thấy (n_answers) + nội dung từng đáp án (answers).
- Nội dung đề bài (content_text), công thức để LaTeX.
- Có hình/đồ thị (has_figure), công thức (has_formula), bảng (has_table) không.
- Ảnh có chứa TRỌN đề không (content_complete) — nếu thấy đề bị cắt cụt ở mép trên/dưới thì false.
- Nếu có hình: hình có bị cắt mép không (figure_complete).
```

> Gửi `full_image` (content+answers) là đủ ngữ cảnh nhất. Nếu chưa có `full_image` thì gửi `content_image`.

---

## 6. `src/services/vlm_verifier.py` — Orchestrator + merge

`verify_exam(exam, layouts, group_layouts, images, out_dir) -> None` (mutate exam tại chỗ):

1. `selected = select_questions(exam)` (§3). Nếu rỗng hoặc `use_vlm_verification=False` → return.
2. Với mỗi câu: lấy đường dẫn ảnh đã crop (`out_dir/crops/q{n}_full.png`, fallback `_content.png`). Gọi `vlm_client.analyze_question(image_path, context)` (chạy batch async).
3. **Merge kết quả về `Question`** (chỉ ghi đè khi VLM tự tin hơn):
   - `q.type`: nếu Phase 2 = UNKNOWN hoặc khác → lấy `result.question_type`. (Map enum VLM → `QuestionType`.)
   - `q.has_figure/has_formula/has_table` = OR với kết quả VLM.
   - `q.content_text`: nếu rỗng/ngắn hơn → lấy `result.content_text`.
   - **Đáp án (quan trọng nhất):** nếu `result.n_answers > len(q.answers)` (OCR đã mất đáp án):
     - Bổ sung `Answer(label=..., text=...)` cho các nhãn còn thiếu. `image=None` (chưa có crop pixel — chấp nhận, vì ảnh `full_image` đã chứa đáp án; Phase 4 có thể tách sau).
     - Set `q.needs_review=True` + `notes` để người duyệt biết đáp án này do VLM thêm.
   - Nếu `result.n_answers == len(q.answers)` và type khớp → có thể **gỡ** `needs_review` (đã xác nhận).
4. **Region repair (re-crop có điều kiện):**
   - Nếu `result.content_complete == False` HOẶC (`result.has_figure and result.figure_complete == False`):
     - Mở rộng vùng câu thành **full-width band** giữa anchor câu này → câu kế (giống nhánh `recovered` đã có trong `snake_walker` 2.8; baseline 2.8 CHƯA có graph band nên đây là nơi VLM bù lại việc crop đồ thị), crop lại `q{n}_full.png`, ghi đè.
     - Set `q.needs_review=True`, log `re-crop q{n}: content/figure incomplete`.
   - Re-crop dùng lại `cropper` (tách hàm crop 1 region nếu cần) — KHÔNG tự vẽ lại logic crop.
5. Cập nhật `exam.avg_confidence`, đếm `n_vlm_calls`, ghi summary.

> **Thứ tự an toàn:** chạy VLM SAU cropper (đã có ảnh để gửi). Nếu re-crop, gọi lại đúng hàm crop của Phase 2.

---

## 7. Sửa `scripts/parse_cli.py`

Sau Stage 6 (cropper), trước khi save `exam.json`, chèn:

```python
# ============================================================
# Stage 7: VLM Verify (Phase 3, lazy)
# ============================================================
if not no_crop and settings.use_vlm_verification:
    click.echo(f"\n[7/7] VLM Verify (Qwen3-VL, lazy)...")
    from src.services.vlm_verifier import verify_exam
    t0 = time.time()
    n_calls = verify_exam(exam, layouts, group_layouts, images, out)
    click.echo(f"   ✓ {n_calls} VLM calls ({time.time() - t0:.1f}s)")
else:
    click.echo(f"\n[7/7] VLM Verify — SKIPPED")
```

Đổi nhãn stage `[6/6]` → `[6/7]`, thêm cờ CLI `--no-vlm` (ép tắt). Cập nhật `summary.txt`: thêm block `[VLM — Phase 3]` (số call, số đáp án bổ sung, số câu re-crop, số câu vẫn needs_review).

---

## 8. Acceptance — chạy lại 3 đề, kiểm tra

Chạy: `python scripts/parse_cli.py input/<de>.pdf`

**Toán 8 / Tiếng Anh (đề dễ — KHÔNG được hồi quy):**
- Số câu / số group / crop KHÔNG đổi so với Phase 2.10.
- Số VLM call thấp (lý tưởng ≈ 0 với Toán 8; vài câu với Tiếng Anh nếu type mơ hồ).
- `exam.json` hợp lệ, không crash.

**azota (đề khó — phải cải thiện đo được):**
- Câu thiếu đáp án (1-3) → sau VLM đa số đủ 4 (đếm `n_answers`).
- `has_figure` đúng cho câu có đồ thị (q3, q45...).
- `content_text` có LaTeX cho công thức (thay vì rác OCR).
- Câu content/figure bị cắt → được re-crop full-width.
- Log `vlm.log` đầy đủ; pipeline không crash dù vài call timeout.

In bảng so sánh trước/sau VLM (số đáp án trung bình, số câu UNKNOWN, số câu needs_review) vào `summary.txt`.

---

## 9. Thứ tự thực thi đề xuất (chia nhỏ, test từng bước)

1. **3.1** `vlm_client.py` + `schemas/vlm.py` — test độc lập: gọi 1 ảnh crop có sẵn (vd `output/<id>/crops/q1_full.png`), in JSON. Xác nhận Ollama trả đúng schema.
2. **3.2** `vlm_verifier.py` — `select_questions` + merge (CHƯA re-crop). Test trên azota, xem số đáp án/type cải thiện.
3. **3.3** Region repair (re-crop) — bật sau khi 3.2 ổn.
4. **3.4** Tích hợp `parse_cli.py` + summary + `--no-vlm`.
5. **3.5** Chạy đủ 3 đề, ghi CHANGELOG (Phase 3.x).

> Mỗi bước commit ý tưởng vào CHANGELOG. Không gộp tất cả rồi mới test — model 32B chậm, cần soi `vlm.log` từng bước.

---

## 10. Bẫy cần tránh

- **Đừng gọi VLM cho mọi câu.** Vi phạm triết lý lazy + cực chậm (32B). Gating §3 là bắt buộc.
- **Đừng để VLM "giải bài".** Prompt cấm giải; chỉ mô tả cấu trúc. Tránh hallucination đáp án đúng.
- **Ảnh quá lớn:** crop full có thể > 2000px (đã gặp q17_B 3023px). Trước khi gửi VLM, nếu chiều cao > ~1600px thì resize giữ tỉ lệ (Qwen3-VL xử lý tốt ảnh vừa; ảnh khổng lồ vừa chậm vừa dễ lỗi).
- **Đừng ghi đè đáp án Phase 2 khi VLM thấy ÍT hơn.** Chỉ bổ sung khi VLM thấy NHIỀU hơn (OCR sót). VLM thấy ít hơn → có thể nó nhìn nhầm → giữ Phase 2, gắn review.
- **Async nhưng giới hạn đồng thời** (Semaphore 1-2) — 32B dễ OOM/chậm nếu bắn song song nhiều.
- **Fail-safe tuyệt đối** — try/except quanh mỗi call, lỗi → giữ Phase 2 + log, không crash.
