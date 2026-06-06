# Changelog

Lịch sử thay đổi của `exam_parser_paddle`. Mỗi phase ghi rõ mục đích + file đã sửa.

Format: `[Phase X.Y] - YYYY-MM-DD - Title`

---

## [Phase 3.4] - 2026-06-05 - Re-slice đáp án MCQ theo khoảng trắng + bỏ fallback nới mù

### Mục đích
Đề azota: per-answer crop hỏng — q1_B chỉ có chữ "B." (55px), q1_A/C/q2_B mất hẳn ảnh
(VLM chỉ thêm text). Chất lượng ảnh đáp án chưa đảm bảo.

### Vấn đề (phân tích output c939d5ba)
1. **Crop đáp án dựa vào MARKER không đáng tin**: OCR toán tách "B." khỏi công thức →
   region đáp án = mỗi chữ "B."; marker mất hẳn (A/C) → không có ảnh; VLM thêm text nhưng `image=None`.
2. **Fallback nới dọc 12% (3.3) nuốt chỉ dẫn trang 1**: q1 là câu ĐẦU trang, không có
   hàng xóm trên → fallback kéo lên 396px, ôm trọn 3 dòng đỏ "Phần đáp án/lời giải/Kéo đề thi...".

### Giải pháp (`src/services/vlm_verifier.py`)
1. **`_reslice_row_answers()` — chia cột theo KHOẢNG TRẮNG** (không dựa marker):
   - Band đáp án = vùng full (đã nới dọc) dưới stem, mép trên chừa 1.5 line cho tử số/mũ.
   - Chiếu band lên trục x → tìm cột trắng (ink==0) = khe giữa đáp án.
   - Lấy N-1 khe RỘNG NHẤT (N = số đáp án VLM xác nhận) làm ranh giới → crop N cột.
   - Gán mỗi cột cho 1 đáp án (nhãn A→D ↔ trái→phải). Bắt được cả đáp án OCR bỏ sót.
   - Chỉ áp dụng layout 1 HÀNG (marker cùng mức y); lưới 2x2 → bỏ qua, giữ nguyên.
   - Gọi cho MCQ khi `answers_added>0` hoặc có đáp án `image=None`. 2x2/không tách được → no-op.
2. **Bỏ fallback nới dọc 12%**: không có hàng xóm hướng nào → GIỮ NGUYÊN biên hướng đó
   (chỉ nới tới câu hàng xóm thật). Hết nuốt header/chỉ dẫn.

### Kết quả (prototype gap-detection trên ảnh thật)
- q1 → 4 cột x=[0,709][709,1199][1199,1825][1825,2550] — khớp đúng A/B/C/D ✓
- q2 → 4 cột x=[0,633][633,1208][1208,1783][1783,2550] — tách đúng cả B bị thiếu ✓
- Stat mới trong log: `re-slice đáp án`.

### Còn lại / lưu ý
- Layout đáp án 2x2 (lưới) chưa re-slice (giữ crop Phase 2) — hiếm ở đề này.
- Cần CHẠY THẬT trên WSL (cần numpy) để xác nhận; máy Mac không chạy được Paddle/numpy stack.
- Đề Tiếng Anh/Toán 8: re-slice chỉ chạy trên câu MCQ bị flag — verify không hồi quy.

---

## [Phase 3.3.1] - 2026-06-05 - Sửa re-crop nới lên NUỐT câu trước (band tìm hàng xóm sai)

### Mục đích
Sau 3.3, chạy thật (output c939d5ba): re-crops=17 (đã kích hoạt), nhưng `q29_full.png`
chứa CẢ Câu 28 lẫn Câu 29 → ảnh xấu hơn, người dùng tưởng "không cải thiện".

### Vấn đề
`_recrop_fullwidth` tìm hàng xóm trên bằng lọc `bottom <= y_top`. Nhưng Phase 2 hay cho
vùng các câu CHỒNG LẤN nhau (Câu 28: 479-945 thò xuống qua đỉnh Câu 29: 913). Vì 945 > 913
nên Câu 28 bị loại khỏi "câu ở trên" → thuật toán nhảy lên câu xa (đáy ~465) → nuốt trọn Câu 28.

### Giải pháp (`src/services/vlm_verifier.py`)
Đổi tiêu chí hàng xóm sang VỊ TRÍ TƯƠNG ĐỐI của ĐỈNH (y_top), chịu được chồng lấn:
- `above = [b for (a,b) if a < y1]` → sàn = max(above). Nếu câu trên chồng xuống quá y1
  thì sàn > y1 → `min(.., y1)` ép new_y1=y1 ⇒ KHÔNG nới lên (không nuốt câu trên).
- `below = [a for (a,b) if b > y2]` → trần = min(below) (đối xứng cho mép dưới).

### Kết quả (simulate trên bố cục trang 4 có chồng lấn)
- Câu 29: 913-1186 → 913-1225 (up=0 ✓ không nuốt Câu 28, down=+ bắt trọn đáp án).
- Câu 31/34 (kẹp giữa câu chồng lấn): không nới — an toàn.

### Lưu ý quan trọng cho người dùng
Với MCQ toán: ảnh crop CŨ **đã chứa đủ 4 đáp án về mặt pixel** — OCR chỉ không ĐỌC được
công thức. Nên re-crop to hơn KHÔNG đổi nhiều về thị giác; giá trị thật là **text đáp án
được khôi phục trong `exam.json`** (q3 thêm C, q13 thêm A...). Re-crop chỉ tạo khác biệt
thị giác khi nội dung bị cắt vật lý (hiếm ở đề này). → Kiểm tra exam.json, đừng chỉ nhìn ảnh.

---

## [Phase 3.3] - 2026-06-05 - Sửa re-crop VLM không bao giờ kích hoạt (ảnh crop không đổi)

### Mục đích
Sau khi chạy thật đề azota (output 920dc046): VLM gọi 18 lần, +20 đáp án, nhưng
**re-crops = 0** → ảnh trong crops/ y hệt cũ. Các câu crop sai vẫn sai.

### Vấn đề (chẩn đoán từ vlm.log + exam.json)
1. **Điều kiện re-crop không bao giờ đúng**: `not content_complete or (has_figure and not figure_complete)`.
   - VLM báo `content_complete=True` cho TẤT CẢ câu — vì nó chỉ nhìn ẢNH ĐÃ CROP,
     phần bị cắt nằm NGOÀI ảnh nên nó không thấy → tưởng đủ (mù với phần đã mất).
   - `has_figure=False` cho tất cả → nhánh figure chết, kể cả 5 câu `figure_complete=False`.
   → Toàn bộ region-repair inert. Đáp án VLM bổ sung chỉ là TEXT (`image=None`), không tạo lại ảnh.
2. **`_recrop_fullwidth` chỉ nới chiều NGANG** (full-width, y±20) → kể cả khi chạy cũng
   không cứu được vết cắt theo CHIỀU DỌC (đồ thị/tử số phân số) — vốn là ca chính.

### Giải pháp (`src/services/vlm_verifier.py`)
1. **Trigger re-crop khi `answers_added > 0`**: VLM bổ sung được đáp án ⇒ crop chắc chắn
   thiếu đáp án đó. Tín hiệu này đáng tin hơn `content_complete` (vốn mù). Tách image-repair
   khỏi phán đoán "complete" của VLM.
2. **`_recrop_fullwidth` nới CHIỀU DỌC tới câu trước/sau**: build `regions_by_page`
   (y_top/y_bottom mọi câu/trang); mép trên nới lên tới đáy câu liền trước, mép dưới
   xuống tới đỉnh câu liền sau (chừa GAP=4px), không hàng xóm → nới 12% chiều cao trang.
   Không bao giờ thu hẹp so với vùng gốc; kẹp trong trang.

### Kết quả (simulate trên bbox exam.json 920dc046)
| Câu | cao gốc → band mới | up/down |
|---|---|---|
| q29 | 273 → 708px | ↑↓ |
| q47 | 383 → 838px | ↑↓ |
| q13/16/38 | +nới 2 phía | ↑↓ |
| q3 | chạm đáy trang (cap) | ↑ |

→ 6 câu azota bổ sung đáp án (q3,13,16,29,38,47) sẽ được re-crop band rộng chứa đủ nội dung.

### Còn lại
- Câu có đồ thị NHƯNG không bị flag (vd q45: đủ 4 đáp án, type biết, review=False) →
  gating bỏ qua, VLM không nhìn tới. Muốn xử lý cần mở gating cho câu has_figure/đồ thị (cân nhắc Phase 3.4).
- Đáp án VLM thêm vẫn `image=None` (chưa tách crop riêng từng đáp án) — full_image đã chứa, đủ để review.
- Đề Toán 8 / Tiếng Anh: gating ít/không trigger → cần verify không hồi quy khi chạy lại.

---

## [Phase 3.2] - 2026-06-05 - VLM Verifier + CLI tích hợp Stage 7

### Mục đích
Orchestrator lazy: chọn câu → gọi VLM batch async → merge kết quả vào Exam. Tích hợp vào CLI là Stage 7.

### Vấn đề giải quyết
- Câu thiếu đáp án (OCR sót) → VLM nhìn ảnh bổ sung.
- Loại câu UNKNOWN → VLM classify chính xác.
- Content/figure bị cắt → VLM báo signal re-crop full-width.

### Giải pháp
- **`src/services/vlm_verifier.py`** (MỚI):
  - `select_questions()`: lazy gating — chỉ gọi VLM khi needs_review/UNKNOWN/answers≠4/confidence<0.6.
  - `_merge_result()`: bổ sung answer (KHÔNG ghi đè khi VLM thấy ít hơn), update type/flags, OR logic flags.
  - `_recrop_fullwidth()`: mở rộng bbox → full-width band, gọi lại cropper.
  - `verify_exam()`: batch async (Semaphore=2), fail-safe, cập nhật exam stats.
- **`scripts/parse_cli.py`** (SỬA):
  - Thêm Stage [7/7] VLM Verify sau Cropper.
  - Cờ `--no-vlm` (ép tắt VLM, debug Phase 2).
  - `parse.log` ghi full pipeline log.
  - Snapshot trước/sau VLM: so sánh n_answers, n_UNKNOWN, n_review.
  - VLM stats trong summary.txt.

### Kết quả mong đợi
- Đề Toán 8: ≈0 VLM calls (đề dễ, không trigger gating).
- Đề azota: VLM bổ sung đáp án thiếu, classify math questions, re-crop đồ thị.
- Pipeline không crash dù Ollama timeout/lỗi (fail-safe).

---

## [Phase 3.1] - 2026-06-05 - VLM Client + Schema structured output

### Mục đích
Low-level client gọi Ollama Qwen3-VL phân tích ảnh câu hỏi. Schema Pydantic ép JSON response.

### Giải pháp
- **`src/schemas/vlm.py`** (MỚI): `VLMQuestionType`, `VLMAnswer`, `VLMQuestionResult` — dùng với Ollama `format=<json schema>`.
- **`src/services/vlm_client.py`** (THAY placeholder):
  - `analyze_question_async()`: httpx async, gửi ảnh base64 (JPEG), structured JSON output.
  - Resize ảnh >1600px trước khi gửi (Qwen3-VL tốt với ảnh vừa).
  - Retry 1 lần khi timeout/lỗi mạng. Fail-safe: trả None.
  - `vlm.log` riêng: question_id, kích thước ảnh, thời gian, kết quả.
  - Prompt tiếng Việt: mô tả CẤU TRÚC, KHÔNG giải bài.

### Trade-off
- ✅ Structured output (Ollama format parameter) → không cần parse tay.
- ✅ Ảnh JPEG quality=85 giảm size gửi API (~3x nhỏ hơn PNG).
- ⚠️ Semaphore=2 giới hạn đồng thời — 32B model nặng GPU.

---

## [Phase 3.0] - 2026-06-05 - Kế hoạch VLM Semantic Layer + chốt baseline 2.8

### Mục đích
Chốt kế hoạch Phase 3 (Qwen3-VL-32B, lazy) để vá lỗi OCR mà rule engine không sửa được
(đáp án mất, công thức nát, đồ thị bị cắt, loại câu mơ hồ).

### Quyết định baseline
- **Quay về Phase 2.8** vì 2.9/2.10 làm sai nhận diện câu đề Tiếng Anh (đã revert code + CHANGELOG).
- Phase 3 khởi đầu từ 2.8 — hợp lệ vì VLM là tầng cộng thêm, độc lập rule engine.
- Fix hình học 2.9 (`answer_block_top`) + 2.10 (graph band) KHÔNG khôi phục ở rule engine
  → VLM gánh thay qua cơ chế re-crop (PHASE3_GUIDE §6.4).
- Khuyến nghị cherry-pick lại fix group-regex word-boundary của 2.10
  (`r"^\s*(phan)\s+(?:[ivxlcdm]+|\d+)\b"`) — độc lập, tránh "group giả" nuốt câu.

### Giải pháp (chưa code)
- `PHASE3_GUIDE.md` — spec đầy đủ cho antigravity: gating lazy, `schemas/vlm.py`,
  `vlm_client.py` (Ollama /api/chat + format JSON + async), `vlm_verifier.py`
  (merge + re-crop có điều kiện), Stage [7] trong `parse_cli.py`, cờ `--no-vlm`.

### Còn lại
- Antigravity implement theo thứ tự 3.1→3.5. Phase 4+: chấm `is_correct`, MinIO upload, API serve.

---

## [Phase 2.8] - 2026-06-03 - Recover câu OCR mất marker + bare answer marker (đề azota)

### Mục đích
Đề azota (toán THPT, 23 trang, đầy công thức) detect thiếu câu & đáp án. Xử lý.

### Vấn đề & nguyên nhân (verify trên d53bc224)
1. **Thiếu câu 34, 43, 46**: OCR MẤT HẲN dòng "Câu N:" (chỉ còn đáp án + dòng nội dung
   tiếp). Câu trước (33/42/45) có `end` = câu kế (35/44/47) → nuốt luôn câu bị mất.
2. **Đáp án thiếu (nhiều câu 0-3 đáp án)**: công thức toán bị OCR tách rời → marker
   đáp án thành "bare marker" ("A." / "B." không có nội dung ngay sau) → regex đòi `\S` → trượt.

### Giải pháp
**`anchor_extractor.py`** — regex đáp án bỏ yêu cầu `\S` sau dấu: `^\s*([A-D])\s*[\.\),]\s*`
→ bắt được bare marker "A." (nội dung công thức ở fragment khác, gộp qua nearest-anchor).

**`snake_walker.py`**
- `_recover_missing_anchors()`: phát hiện gap số câu (33→35) + chu kỳ đáp án A-D dư trong
  vùng giữa → tạo anchor tổng hợp (source="recovered") cho câu bị mất.
- Câu recovered: full region = băng full-width [start,end] (crop được cả stem OCR bỏ sót),
  bắt buộc needs_review.
- MCQ có 1-3 đáp án (chuẩn 4) → needs_review (OCR có thể sót đáp án).

### Kết quả (verify qua simulation trên blocks.json)
| | Trước | Sau |
|---|---|---|
| Câu trích | 47 (thiếu 34,43,46) | **50** (recover 34,43,46) ✅ |
| Đáp án 0 | 4 câu | 1 câu ✅ |
| Đáp án đủ 4 | 30 câu | 32 câu ✅ |
| Câu <4 đáp án | (lẫn lộn) | đều set needs_review để soát ✅ |

### Còn lại (PHẠM VI SAU — giới hạn OCR, cần VLM Phase 3)
- ~18 câu vẫn <4 đáp án: công thức toán bị OCR phân mảnh nặng / marker đáp án mất hẳn
  (không có tín hiệu để recover). Đã flag needs_review.
- Vùng câu recovered là băng full-width ước lượng → review thủ công.
- Đề toán phức tạp (đồ thị, phân số nhiều tầng) → độ chính xác phụ thuộc OCR; VLM sẽ cải thiện.

---

## [Phase 2.7] - 2026-06-03 - Crop chính xác đáp án inline + blank cuối câu + tên group

### Mục đích
Sau 2.6 (đề Anh đã đủ đáp án), tinh chỉnh CHẤT LƯỢNG vùng crop theo phản hồi:
content/đáp án câu dạng "Question N: A.. B.." còn dính nhau; blank "____" cuối câu bị mất.

### Vấn đề & nguyên nhân (verify trên 833c8435)
1. **q4/q5/q27/q28**: content crop cả dòng (kèm A,B,C,D); đáp án A kèm "Question N".
   Nguyên nhân: gán nearest-anchor GỘP cả dòng câu hỏi vào bucket A → A phình lại; content
   không có line riêng (đáp án inline cùng y) → fallback full-width.
2. **q24**: blank "____" cuối câu ("dreams of having ___") mất — OCR không đọc gạch dưới
   → dòng kết thúc ở "having", content cắt tại đó.
3. Yêu cầu: tên file group kèm dải số câu để kiểm tra group đúng/sai.

### Giải pháp (`snake_walker.py` + `cropper.py`)
1. **Đáp án inline** (`source=regex_inline`): dùng bbox ƯỚC LƯỢNG riêng, KHÔNG gộp line.
   - Loại chính dòng câu hỏi khỏi zone đáp án.
   - Chỉ gán zone line vào đáp án non-inline (đáp án tách dòng).
2. **Content khi có đáp án inline trên dòng câu hỏi**: content = phần stem TRƯỚC đáp án
   inline đầu tiên ("Question 27:" thôi).
3. **`_extend_right_to()`**: content thường mở rộng mép phải = mép phải full region
   → bắt trọn blank "____" cuối câu OCR bỏ sót.
4. **Tên file group**: `g{k}_header_{min}_{max}.png` (vd `g1_header_1_3.png`) — dải số câu.

### Kết quả (verify qua simulation)
| | Trước | Sau |
|---|---|---|
| q27 content | [0,2550] full | [154,419] "Question 27:" ✅ |
| q27 đáp án A | [146,647] kèm Question | [419,639] "A. answer" ✅ |
| q4/q28 | A dính Question | content + A tách riêng ✅ |
| q24 blank | cắt x1152 | mở rộng x1899 (bắt blank) ✅ |
| tên group | g1_header.png | g1_header_1_3.png ✅ |

### Còn lại (PHẠM VI SAU)
- bbox đáp án inline là ƯỚC LƯỢNG theo tỉ lệ ký tự (đã set needs_review) → muốn pixel-chuẩn
  cần OCR-lại vùng hoặc VLM Phase 3.
- Blank dài VƯỢT mép phải answer column (hiếm) có thể vẫn thiếu chút → cần image-scan gạch dưới.

### Cần làm
Chạy lại `parse_cli.py` đề tienganh trên WSL, kiểm tra crops/ (q4,5,24,27,28) + tên g*_header_*.

---

## [Phase 2.6] - 2026-06-03 - Fix azota 0-crop + đáp án dính dòng + group leak

### Mục đích
Test 3 đề (toan8 4b9fece1, azota 5b5763ee, tienganh 6365f51b) phát hiện loạt lỗi:
azota crop 0 ảnh; đề Anh thiếu đáp án A; câu cuối nhóm nuốt đoạn dẫn nhóm sau.

### Vấn đề & nguyên nhân (verify bằng dữ liệu thật)

**1. AZOTA crop = 0** (nghiêm trọng nhất). `_find_solution_boundary` match cụm
"lời giải chi tiết" trong CÂU HƯỚNG DẪN dài ở TRANG 1 ("Phần lời giải chi tiết:
Bắt đầu phần này...") → tưởng phần giải bắt đầu ngay đầu đề → loại sạch 94 câu.
Marker thật là heading ngắn "HƯỚNG DẪN GIẢI CHI TIẾT" ở trang 8.

**2. Đề Anh thiếu đáp án A** (q4,5,27,28). OCR gộp "Question 4: A. liberty" thành
1 dòng → phân loại QUESTION (ưu tiên) → "A. liberty" không được tách thành đáp án.

**3. Đề Anh thiếu đáp án B** (q10,14). OCR đọc "B." → "B," (dấu phẩy) → regex trượt.
Hệ quả phụ: dòng "B," bị tính vào content (q10 content kèm đáp án).

**4. Câu cuối nhóm nuốt đoạn dẫn nhóm sau** (q3 kèm "Mark the letter…", q36 kèm
"Read the following passage…", toan8 q4 kèm "Phần II"). `end` câu = đầu câu kế,
không dừng ở group header chen giữa.

**5. Đoạn dẫn nhóm không được crop riêng.**

### Giải pháp
**`anchor_extractor.py`**
- Regex đáp án + inline: `[\.\)]` → `[\.\),]` (chấp nhận dấu phẩy OCR). [Fix 3]
- `_extract_inline_answers(start, min_count)`: quét đáp án SAU marker câu hỏi trên
  cùng dòng ("Question 4: A. …") + đáp án dàn hàng ngang. [Fix 2]

**`snake_walker.py`**
- `_find_solution_boundary`: marker chỉ nhận khi là HEADING NGẮN (≤45 ký tự, không
  phải câu hướng dẫn dài) + tín hiệu số câu reset mạnh (drop ≥5). Lấy mốc sớm nhất. [Fix 1]
- Clip `end` câu tại GROUP HEADER chen giữa → không nuốt đoạn dẫn nhóm sau. [Fix 4]
- Mọi group: tính lead-in region [header → câu đầu] = chỉ dẫn + passage → crop riêng. [Fix 5]
- Đáp án nguồn `regex_inline` (bbox ước lượng) → set needs_review.

**`cropper.py`**
- `crop_group_lead()` → `g{k}_header.png`, gán `group.header_image` (+ passage_image nếu PASSAGE).

### Kết quả (verify qua simulation trên blocks.json — không chạy Paddle)
| | Trước | Sau |
|---|---|---|
| azota câu trích | 0 | **47** (1-50, boundary trang 8) ✅ |
| tienganh câu | 50 (boundary trang 6 đúng) | 50 ✅ |
| q4/q5/q27/q28 đáp án | thiếu A | **A,B,C,D** ✅ |
| q10/q14 đáp án | thiếu B | **A,B,C,D** ✅ |
| q3 end | nuốt "Mark the letter" | clip tại y=1699 ✅ |
| q36 end | nuốt "Read passage" | clip tại y=356 ✅ |
| group lead-in | không crop | `g{k}_header.png` ✅ |

### Còn lại (PHẠM VI SAU)
- Đáp án dính dòng câu hỏi (pronunciation/stress): bbox A ước lượng theo tỉ lệ ký tự
  → crop A có thể kèm "Question N:". Đã set needs_review. Cần OCR-lại vùng hoặc VLM để chuẩn.
- q12 blank "____": OCR không bắt nét gạch dưới → content thiếu phần điền (giới hạn OCR).
- content_text thứ tự từ với toán phân số (cosmetic — bbox crop đúng).

### Cần làm
Chạy lại `parse_cli.py` cả 3 đề trên WSL, kiểm tra overlay + crops thực tế.

---

## [Phase 2.5] - 2026-06-03 - Fix vùng crop đáp án + reading-order + clip câu cuối

### Mục đích
Sau Phase 2.4, test lại `demau_toan8.pdf` (output 9e49b396) vẫn còn 4 lỗi VÙNG CROP
(anchor đã đúng, nhưng region tính sai). Fix tại `snake_walker.py`.

### Vấn đề & nguyên nhân (verify bằng dữ liệu thật)

**Issue A — Đáp án A/D crop ra full-row (cả 4 đáp án).**
Đáp án trắc nghiệm VN nằm CÙNG HÀNG (y gần bằng nhau, khác cột x). Cách cũ chia
"dải y" giữa các anchor đáp án → khi y bằng nhau, dải rỗng (→ fallback full-width)
hoặc gộp 2 đáp án.

**Issue C — Câu 4 mất "= 0 là:", Câu 3 lẹm xuống Câu 4.**
Phân số toán của Câu 4 render Ở TRÊN dòng chữ "Câu 4" (y < anchor) → gán nhầm sang Câu 3.

**Issue D — Câu cuối (q7) nuốt "Chúc các em…/Đáp án/bảng đáp án".**
Câu cuối lấy `end = (last_page, +inf)`.

**Phụ — q4 đáp án C kéo dài xuống header "Phần II"** chen giữa Câu 4 và Câu 5.

### Giải pháp (`src/services/snake_walker.py`)
1. **Issue A — gán line vào anchor GẦN NHẤT** (Euclidean, phạt khác trang) thay chia dải y.
   Xử lý đúng: cùng hàng, lưới 2x2, đáp án xuống dòng.
2. **Issue C — `_compute_effective_starts()`**: mở rộng start câu lên trên để bao line
   y-overlap ≥40% với dòng anchor & nằm bên phải (phân số là đuôi dòng "Câu N").
3. **Issue D — `_find_content_end()`**: clip câu cuối tại marker kết thúc đề (Hết/Đáp án/
   "chúc…làm bài"/bảng "1.C 2.B…"). Khác footer số trang → không clip nhầm đề nhiều trang.
4. **Phụ — filter zone đáp án**: bỏ line dưới xa hàng đáp án cuối (> bottom + 1.2×line_h).
5. Xoá `_clip_last_answer_lines` (không cần nữa).

### Kết quả (verify qua simulation trên JSON — không cần chạy Paddle)
| Câu | Trước | Sau |
|---|---|---|
| q1-q3 đáp án | A/D full-row | 4 cột hẹp tách bạch ✅ |
| q4 content | mất "= 0 là:" | bao đủ phân số + "= 0 là:" ✅ |
| q3 | lẹm xuống Câu 4 | end=1941, không lẹm ✅ |
| q7 | nuốt tới bảng đáp án | clip tại "Chúc các em…" (y=2093) ✅ |
| q4 đáp án C | kéo xuống "Phần II" | đúng ô [323,2388,539,2437] ✅ |

### Còn lại (PHẠM VI SAU)
- Đáp án trên CÙNG 1 OCR line thật sự (inline): bbox ước lượng theo tỉ lệ ký tự — toan8 không gặp.
- content_text thứ tự từ với phân số hơi lộn xộn (text tham khảo — bbox crop ĐÚNG).
- Reading-order phức tạp / 2 cột báo → cần VLM Phase 3.
- has_figure dương tính giả khi cả trang là 1 figure-block (workaround: chỉ set khi line rỗng text).

### Cần làm
Chạy lại `parse_cli.py` trên CẢ 3 đề (toan8/tienganh/azota) trên WSL xác nhận không regression —
đặc biệt đề Anh (đáp án xuống dòng) & azota (phân số nhiều).

---

## [Phase 2.4] - 2026-06-03 - Fix line-granularity + answer regex + clip đáp án + inline answers

### Mục đích
Fix 4 bug gốc rễ phát hiện khi test Phase 2 trên `demau_toan8.pdf`. Bugs khiến: câu nuốt cả trang, mất đáp án B/C, đáp án lẹm sang câu sau, đáp án ngang 1 dòng chỉ bắt được 1.

### Bug 1 🔴 (CRITICAL) — Snake Walker gom theo BLOCK thay vì LINE
**Nguyên nhân:** `snake_walker.py` dùng `block.bbox` để gom — nhưng layout model `en` gom cả trang 2 thành 1 block `figure` (bbox y=46→3273). Kết quả: q5 nuốt cả trang, q6/q7 chỉ còn 66px.
**Fix:** Thêm `PositionedLine` dataclass + `_flatten_lines()`. Chuyển toàn bộ snake_walk sang LINE granularity: gom, tính bbox, split content/answer đều dùng `line.bbox` thay `block.bbox`.
**Kết quả:** q5 chỉ ôm các dòng phương trình thật, q6/q7 có đủ nội dung.

### Bug 2 🔴 — Regex đáp án mất B/C (dính liền OCR)
**Nguyên nhân:** Pattern `\s+\S` (≥1 space sau dấu chấm). OCR cho "B.m =4" → trượt.
**Fix:** Đổi `\s+` → `\s*` trong ANSWER + SUB_QUESTION pattern.
**Kết quả:** toan8 Câu 1-4 ra đủ 4 đáp án (trước: 2,2,3,4).

### Bug 3 🔴 — Đáp án cuối lẹm sang câu sau
**Nguyên nhân:** Vùng đáp án cuối kéo tới `end` (anchor câu kế). Phân số câu sau render trước dòng "Câu N" → lọt vào.
**Fix:** `_clip_last_answer_lines()` — chỉ giữ line cùng hàng hoặc sát dưới dòng đáp án (≤ 1.5x line_height).
**Kết quả:** q3_D chỉ chứa "D. m=3", không kèm Câu 4.

### Bug 4 🟡 — 4 đáp án chung 1 dòng OCR
**Nguyên nhân:** OCR trả "A. x B. y C. z D. w" là 1 line → `^` match chỉ A.
**Fix:** Thêm `ANSWER_INLINE_RE` + `_extract_inline_answers()` trong anchor_extractor: dùng `re.finditer` quét nhiều đáp án, chia bbox theo tỉ lệ ký tự.
**Kết quả:** Câu có đáp án ngang 1 dòng ra đủ 4 anchor (source="regex_inline").

### Đã sửa
- **`src/services/snake_walker.py`** — rewrite: thêm `PositionedLine`, `_flatten_lines()`, `_compute_multi_region_from_lines()`, `_clip_last_answer_lines()`. Bỏ hàm `_gpos_of_block`, `_compute_multi_region` cũ.
- **`src/services/anchor_extractor.py`** — regex `\s*`, thêm `ANSWER_INLINE_RE`, `_extract_inline_answers()`, refactor `extract_anchors()`.
- **`tests/test_phase2.py`** — thêm 2 test: `test_flatten_lines`, `test_snake_walk_line_granularity`, `test_clip_last_answer`.

### Trade-off
- ✅ Line bbox luôn chính xác dù layout model sai → pipeline robust hơn.
- ✅ has_figure chỉ set khi line rỗng text thuộc block figure (tránh dương tính giả).
- ⚠️ Inline answer bbox ước lượng theo tỉ lệ ký tự (không pixel-perfect), đủ để crop.
- ⚠️ _clip_last_answer_lines dùng heuristic 1.5x line_height — có thể cần tune.

---

## [Phase 2.3] - 2026-06-03 - Cropper + Debug Overlay + CLI tích hợp

### Mục đích
Crop ảnh từng câu hỏi / đáp án / passage từ page images, vẽ debug overlay có bbox màu để review thủ công, tích hợp toàn bộ Phase 2 vào CLI.

### Đã thêm / sửa

**`src/services/cropper.py`** — viết mới hoàn toàn
- `_crop_region()`: crop 1 Region từ 1 trang, clamp bbox trong biên ảnh.
- `_crop_multi_region()`: crop MultiRegion (hỗ trợ vắt trang → ghép dọc vertical stack).
- `_make_cropped_image()`: crop + lưu PNG + tạo CroppedImage object (minio_key="" cho Phase 4).
- `crop_question()`: crop full/content/từng đáp án cho 1 câu.
- `crop_group_passage()`: crop passage cho 1 group.
- `crop_all()`: điền CroppedImage in-place vào Exam + vẽ overlay.
- `_draw_overlay()`: vẽ bbox màu lên ảnh gốc (🔴 group, 🔵 question, 🟣 content, 🟢 answer).
- `_draw_rect()`: vẽ rectangle + label text góc trên trái.

**`scripts/parse_cli.py`** — cập nhật từ Phase 1 [1/3] → [1/6]
- Stage 4: Snake Walker → questions, groups, layouts.
- Stage 5: Classifier → gán type.
- Stage 6: Cropper → crop ảnh + overlay.
- Thêm flag `--no-crop` để chạy nhanh tới classify.
- Output: `exam.json`, `crops/`, `overlay/`.
- Summary cập nhật: breakdown question type, group, metadata, needs_review.

### Kết quả
- Output structure đầy đủ: `exam.json` + `crops/` + `overlay/` + `summary.txt`.
- Naming convention: `q{N}_full.png`, `q{N}_content.png`, `q{N}_{label}.png`, `g{k}_passage.png`.
- Debug overlay: mỗi trang 1 ảnh với bbox màu theo spec §6.4.

### Trade-off
- ✅ Hỗ trợ cross-page crop (ghép dọc) cho câu vắt trang.
- ✅ CroppedImage có width/height/size_bytes thật từ file PNG.
- ⚠️ minio_key="" và url=local path → Phase 4 mới upload MinIO.
- ⚠️ Dùng `ImageFont.load_default()` cho label overlay (không cần font riêng).

---

## [Phase 2.2] - 2026-06-03 - Question Type Classifier (rule-based)

### Mục đích
Phân loại QuestionType cho mỗi câu hỏi theo cây quyết định rule-based (KHÔNG dùng VLM).

### Đã thêm

**`src/services/question_classifier.py`** — viết mới hoàn toàn
- `classify(question, group)` → QuestionType: cây quyết định theo §4.1.
- `classify_all(questions, groups)`: gán type in-place cho toàn bộ questions.
- Logic phân loại:
  1. Group PASSAGE → READING_COMPREHENSION
  2. n_answers >= 3 → MCQ_SINGLE (MCQ_MULTI nếu keyword "chọn nhiều")
  3. n_answers == 0: sub-question → TRUE_FALSE, chỗ trống → FILL_BLANK, content dài → ESSAY, ngắn → SHORT_ANSWER
  4. n_answers ∈ {1,2} → UNKNOWN + needs_review
- Keyword lists: MCQ_MULTI, FILL_BLANK regex, ESSAY keywords.
- MCQ nhưng đáp án ≠ 4 → needs_review.

### Kết quả
- Đề Toán 8: Câu 1-4 → MCQ_SINGLE, Câu 5-7 → ESSAY ✅
- Đề Tiếng Anh: đa số MCQ_SINGLE, passage → READING_COMPREHENSION ✅

### Trade-off
- ✅ Hoàn toàn rule-based, không cần VLM → nhanh, deterministic.
- ⚠️ Một số edge case phức tạp (matching, ordering) chưa detect → để UNKNOWN, Phase 3 VLM sẽ xử lý.

---

## [Phase 2.1] - 2026-06-03 - Snake Walker + Region dataclass

### Mục đích
Implement Snake Walker — thuật toán gom anchor thành Question/Group theo "con rắn" liên trang. Tạo Region/MultiRegion/QuestionLayout dataclass truyền từ Walker → Cropper.

### Đã thêm

**`src/services/snake_walker.py`** — viết mới hoàn toàn
- Dataclass: `Region`, `MultiRegion`, `QuestionLayout` (§5 PHASE2_GUIDE).
- `snake_walk()`: hàm chính — sort anchor, xác định ranh giới, gom block, tạo Question/Group.
- `_find_solution_boundary()`: phát hiện phần lời giải (Azota §3.6b):
  - Tìm marker text: "hết", "bảng đáp án", "lời giải chi tiết"
  - Fallback: số câu nhảy lùi (không tăng đơn điệu)
  - Loại anchor sau ranh giới → chỉ giữ phần câu hỏi
- `_classify_group_type()`: phân loại GroupType từ header text (PASSAGE/SECTION_PART/INSTRUCTION).
- `_compute_multi_region()`: tính MultiRegion hỗ trợ vắt trang (trang đầu/giữa/cuối).
- `_check_continuity()`: kiểm tra số câu liên tục, đánh dấu needs_review.
- `_parse_metadata()` / `parse_exam_metadata()`: parse mã đề, môn, thời gian, trường, năm học.

### Kết quả
- Đề Toán 8: 7 câu (1-7), có group SECTION_PART.
- Đề Tiếng Anh: 50 câu (1-50), group PASSAGE + INSTRUCTION.
- Đề Azota: phát hiện ranh giới lời giải → giữ 50 câu, loại phần giải.

### Trade-off
- ✅ Cross-page robust: câu vắt trang được tách region multi-page.
- ✅ Edge case Azota: 2 cơ chế fallback (marker text + monotonic check).
- ⚠️ Đáp án 2 cột (A,B trái; C,D phải): bbox answer tính theo anchor, chưa tách cột hoàn hảo.
- ⚠️ Region tính từ block bbox — nếu layout model miss block thì vùng có thể hẹp hơn thực tế.

---

## [Phase 1.2] - 2026-06-03 - Fix miss câu hỏi (block-type filter + regex điểm)

### Mục đích
Sau khi chạy Phase 1.1 thành công trên 2 đề mẫu, phát hiện vẫn miss câu hỏi:
- Đề Tiếng Anh: 44/50 câu (mất **Q23–28**)
- Đề Toán 8: 4/7 câu (mất **Câu 5, 6, 7**)

OCR đọc **đúng** toàn bộ text các câu này → lỗi nằm ở tầng anchor extraction, không phải OCR.

### Vấn đề (2 bug gốc rễ)

**Bug #1 — Block-type filter loại nhầm câu hỏi**
`anchor_extractor` chỉ quét block thuộc `ANCHOR_BLOCK_TYPES` (TEXT/TITLE/LIST/HEADER/FOOTER),
bỏ qua FIGURE/TABLE/EQUATION. Nhưng layout model `en` của PP-Structure phân loại **sai**
nhiều vùng tiếng Việt / cột đáp án thành `figure`:
- Đề Anh: cả Q23–28 nằm trong **1 block `figure`** → mất sạch 6 câu
- Đề Toán: "Câu 6", "Câu 7" nằm trong block `figure` → mất

**Bug #2 — Regex không nuốt được phần điểm `(Nđ)`**
Pattern cũ `cau\s+(\d+)\s*[\.\:]` bắt buộc `.`/`:` ngay sau số.
"Câu 5 **(4đ)**." có `(` chen giữa số và dấu chấm → không match (Câu 5 nằm block text vẫn miss).

### Giải pháp

**`src/services/anchor_extractor.py`**
1. **Bỏ filter block type**: đổi `ANCHOR_BLOCK_TYPES` → `SKIP_BLOCK_TYPES = set()` (rỗng).
   Quét tất cả block có text. Lý do: OCR text đáng tin, layout classification của model `en`
   trên tài liệu tiếng Việt thì không. Block thuần ảnh (không có line) tự khắc bị bỏ qua.
2. **Nới regex QUESTION** dùng lookahead:
   `^\s*(?:cau|bai|question)\s+(\d+)(?=[\s\.\:\)\(]|$)`
   → sau số chỉ cần là khoảng trắng / `.` / `:` / `)` / `(` / hết dòng → nuốt được "(4đ)".

### Kết quả

| Đề | Trước | Sau |
|---|---|---|
| Tiếng Anh | 44/50 | **50/50** ✅ |
| Toán 8 | 4/7 | **7/7** ✅ |

### Trade-off
- ✅ Không còn miss câu do layout misclassification
- ⚠️ Quét mọi block → tăng nhẹ khả năng false-positive anchor, nhưng pattern đủ chặt
  (phải bắt đầu line bằng "Câu N" / "A." ...) + có human review ở POC nên chấp nhận được.

---

## [Phase 1.1] - 2026-06-03 - Fix PaddleOCR Vietnamese support (tách Layout + OCR)

### Mục đích
Fix lỗi `ppocr ERROR: lang latin is not support` khi chạy PP-Structure với `lang='vi'`.
PP-Structure layout model chỉ hỗ trợ `en` và `ch`, không có Vietnamese.

### Vấn đề
```
[2026/06/03 13:34:34] ppocr ERROR: lang latin is not support,
we only support dict_keys(['en', 'ch']) for layout models
```

PaddleOCR PP-StructureV3 cố tự động chọn layout model cho 'vi' (→ map sang 'latin')
nhưng không có model nào support → crash khi init.

### Giải pháp - Tách Layout và OCR
Thay vì 1 PPStructure xử lý cả layout + OCR, dùng 2 engine riêng:
1. **PPStructure(`lang='en'`, `ocr=False`)**: chỉ làm layout detection (text/figure/table)
2. **PaddleOCR(`lang='vi'`)**: Vietnamese OCR toàn trang
3. **Merge logic**: gán OCR lines vào layout blocks theo bbox overlap (>=50% inside)
4. **Orphan lines** (không thuộc block layout nào): tạo block TEXT riêng để không miss

### Đã sửa

**`src/services/paddle_parser.py`** - rewrite hoàn toàn `PaddleParser`
- Thêm class constant `LAYOUT_LANG = "en"` (cho layout model)
- Tách `_engine` → `_structure` (layout) + `_ocr` (Vietnamese OCR)
- `_ensure_engines()`: lazy load cả 2 engines độc lập
- `_parse_ocr_result()`: convert PaddleOCR raw output → list[TextLine]
- `_convert_and_merge()`: gán OCR lines vào blocks theo bbox overlap, handle orphan lines
- Thêm helper `_bbox_overlap_ratio(inner, outer)` để check containment

### Trade-off
- ✅ Hỗ trợ tốt mọi ngôn ngữ (OCR riêng) - không bị giới hạn của layout model
- ✅ Không miss text (orphan lines vẫn được tạo block)
- ⚠️ Chậm hơn ~30% vì chạy 2 inference (layout + OCR riêng), nhưng đỡ phụ thuộc PP-Structure internal
- ⚠️ Overlap threshold 50% có thể cần tune nếu bbox layout chính xác không cao

### Verify
```bash
python scripts/parse_cli.py input/de_thi.pdf
```

---

## [Phase 1.0] - 2026-06-03 - Core pipeline implementation (Preprocess + PaddleOCR + Anchor)

### Mục đích
Implement 3 stage đầu của pipeline P4 để verify PaddleOCR + Anchor detection chạy đúng trên đề thật. Chưa cần Snake walker, VLM, Crop, MinIO upload - những phần đó để Phase 2-4.

Output Phase này: PDF input → 2 file JSON (`blocks.json`, `anchors.json`) + `summary.txt` để debug và validate.

### Đã thêm

**`src/services/preprocess.py`** - Stage 1 (Preprocess)
- `load_input(path, dpi)`: PDF → list[PIL.Image] qua PyMuPDF (300 DPI mặc định); ảnh JPG/PNG/... → PIL load trực tiếp
- `deskew(img, threshold)`: detect góc nghiêng bằng OpenCV Hough transform, xoay nếu > 0.5°
- `preprocess(path, dpi, do_deskew)`: orchestrator gộp load + deskew

**`src/services/paddle_parser.py`** - Stage 2 (PaddleOCR PP-StructureV3 wrapper)
- `PaddleParser` class với lazy load engine (load model 1 lần, reuse cho nhiều page)
- `parse_page(image, page_index)` → `list[Block]` với bbox + text + type
- `parse_pages(images)` → `list[list[Block]]`
- Map PaddleOCR raw output → `Block`/`TextLine` Pydantic schemas
- Hỗ trợ block types: text, title, list, table, figure, equation, header, footer
- Table block lưu HTML structure trong `extra["table_html"]`

**`src/services/anchor_extractor.py`** - Stage 3 (Anchor Extraction)
- `strip_accents(text)`: bỏ dấu tiếng Việt → OCR-tolerant regex (`Câu` ≈ `Cau` ≈ `Cảu`)
- `ANCHOR_PATTERNS` dict: regex cho 6 loại anchor (QUESTION, ANSWER, SUB_QUESTION, GROUP_HEADER, METADATA, FOOTER)
- `extract_anchors(blocks_per_page)` → flat list[Anchor] với value, bbox, confidence
- Priority matching: nếu 1 line match nhiều type, giữ type ưu tiên cao nhất (QUESTION > GROUP > ANSWER > ...)
- Log breakdown anchor stats theo type

**`scripts/parse_cli.py`** - CLI Phase 1 test
- Args: `--input`, `--dpi`, `--no-deskew`, `--save-images`, `--debug`
- Pipeline 3 stages + dump intermediate output
- Output cấu trúc:
  ```
  output/{exam_id}/
  ├── blocks.json    # PaddleOCR full output
  ├── anchors.json   # extracted anchors
  ├── summary.txt    # human-readable tóm tắt
  └── pages/         # rendered images (nếu --save-images)
  ```
- Summary hiển thị: số pages, số blocks/lines, anchor count per type, top 20 question anchors

### Đã sửa

**`scripts/verify_setup.py`**
- `check_paddle()`: hiển thị mode "GPU (CUDA)" hoặc "CPU only" thay vì fail nếu CPU-only
- `check_paddle_inference()`: tự detect CPU/GPU mode, hiển thị `matmul {mode} 500x500 = Xms`
- `check_paddleocr_inference()`: đọc env `PADDLE_USE_GPU`, hiển thị `(CPU, X.Xs)` hoặc `(GPU, X.Xs)`
- `check_ollama_gpu()`: chấp nhận mixed CPU/GPU mode (76%/24%) là pass cho POC, không bắt buộc 100% GPU

**`src/core/config.py`**
- Default `paddle_use_gpu: bool = False` (RTX 5090 Blackwell sm_120 chưa được Paddle support)
- Thêm `paddle_cpu_threads: int = 8` cho CPU mode

**`.env.example`**
- `PADDLE_USE_GPU=false` (default)
- Thêm `PADDLE_CPU_THREADS=8`
- Note rõ Blackwell limitation

**`SETUP.md`**
- §1 (Tổng quan): Paddle CPU thay GPU
- §7 (PaddlePaddle): rewrite hoàn toàn - giải thích CUDA error 209 do thiếu sm_120 kernel, hướng dẫn cài Paddle CPU stable, trade-off CPU vs GPU
- §10.2: đổi image Docker `minio/minio` → `quay.io/minio/minio` (tránh Docker Hub rate limit 100 pull/6h)
- §10.4 (mới): tạo Access Key qua `mc admin user svcacct add` trong container (Community Edition Console không có UI Access Keys)
- §10.5 (mới): hướng dẫn lưu credentials vào `.env`
- §15 (checklist): update Paddle CPU check

### Vấn đề đã giải quyết

| Issue | Resolution |
|---|---|
| Docker Hub rate limit khi pull MinIO | Switch sang `quay.io/minio/minio` |
| MinIO Community Edition không có UI Access Keys | Dùng `mc admin user svcacct add` trong container |
| PyTorch `undefined symbol ncclCommResume` | Clean uninstall + cài PyTorch nightly cu128 (NCCL 2.29.7) |
| Paddle GPU CUDA error 209 (no kernel for sm_120) | Switch Paddle sang CPU mode (`pip install paddlepaddle==3.0.0`) |
| Ollama mixed CPU/GPU (24%/76%) | Accept cho POC - vẫn nhanh hơn pure CPU ~5x |

### Verify

```bash
conda activate exam_parser_paddle

# Verify environment
python scripts/verify_setup.py
# Expected: 16+/17 pass (Paddle = CPU mode, Ollama = GPU hoặc mixed)

# Test Phase 1 với đề mẫu
python scripts/parse_cli.py input/de_thi.pdf --save-images
# Expected: blocks.json + anchors.json + summary.txt
# Check: số question anchors ≈ số câu trong đề thật
```

### Next: Phase 2

- [ ] Snake Walker (cross-page question extraction)
- [ ] Question type classifier (rule-based)
- [ ] Crop images (single page + multi-page stitch)

---

## [Phase 0.2] - 2026-06-03 - Documentation + flow

### Mục đích
Bổ sung tài liệu mô tả luồng hoạt động chi tiết để team hiểu architecture trước khi code.

### Đã thêm

**`PIPELINE_FLOW.md`** (16 sections)
- Data flow giữa stages (schema transitions)
- Chi tiết từng stage với pseudo-code + output mẫu
- Snake Walker giải thích trực quan (ví dụ Câu vắt 2 trang)
- Decision tree classify question type
- Crop strategy cho multi-page
- MinIO bucket structure
- Output JSON schema mẫu đầy đủ
- API sequence diagram
- 6 edge cases + cách xử lý
- Performance breakdown per stage trên RTX 5090
- Debugging tools (preview PDF, verbose log, step dump)

---

## [Phase 0.1] - 2026-06-03 - Foundation setup

### Mục đích
Khởi tạo project với folder structure + setup environment + skeleton files. Chưa có business logic.

### Đã thêm

**Folder structure**
```
exam_parser_paddle/
├── src/{api,core,services,schemas}/
├── scripts/
├── config/
├── input/  output/  tests/
```

**Documentation**
- `README.md` - Quick start + tech stack overview
- `SETUP.md` - Hướng dẫn cài chi tiết (16 sections): WSL2, Miniconda, PyTorch CUDA 12.8, PaddlePaddle, PaddleOCR, Ollama + Qwen3-VL-32B, MinIO Docker
- `.env.example` - Template 25+ env vars
- `.gitignore`

**Dependencies**
- `requirements.txt` - PaddleOCR, PyMuPDF, OpenCV, Ollama client, FastAPI, MinIO SDK, Loguru, Click...
- `docker-compose.yml` - MinIO container + auto bucket init

**Source skeleton (Pydantic schemas + empty service files)**
- `src/core/config.py` - Settings via pydantic-settings
- `src/core/logging.py` - Loguru setup
- `src/schemas/block.py` - Block, TextLine, BlockType
- `src/schemas/anchor.py` - Anchor, AnchorType (6 types)
- `src/schemas/exam.py` - Exam, Question, Answer, Group, QuestionType (9 types), CroppedImage
- `src/services/*.py` - Empty placeholder cho mỗi stage
- `src/api/main.py` - FastAPI app skeleton

**Scripts**
- `scripts/verify_setup.py` - Test 17 checks: Python, PyTorch CUDA + Blackwell, Paddle GPU, PaddleOCR inference, Ollama API + model + GPU, MinIO connection + bucket + upload/download
- `scripts/parse_cli.py` - Skeleton (chưa implement logic)

### Related design docs (parent folder)

- `PROJECT_PLAN.md` - Full project plan + 10 scope decisions
- `PADDLEOCR_HYBRID_APPROACHES.md` - So sánh 4 phương án P1/P2/P3/P4, recommend P4
- `REGION_DETECTION_APPROACHES.md` - 4 approaches D/H/M/L (anchor-based)
- `MINIO_SETUP.md` - MinIO local setup guide
