# Phase 2 Bugfix Spec — Snake Walker & Cropper

> **Đối tượng:** Antigravity. **Bối cảnh:** Phase 2 đã code xong (snake_walker.py, question_classifier.py, cropper.py) và chạy trên `input/demau_toan8.pdf` → output `output/f0c233d4`. Review phát hiện 4 bug gốc rễ. File này mô tả **chính xác nguyên nhân + cách sửa**, đã verify bằng dữ liệu thật.
> **Ngôn ngữ:** Python, comment tiếng Việt. **Sau khi sửa:** ghi CHANGELOG `[Phase 2.4]` + chạy lại 3 đề (toan8, tienganh, azota).

---

## Bằng chứng (đã kiểm tra trên output/f0c233d4)

Anchor & block thực tế:
```
PAGE 0 (page 1): 31 block TEXT, segment tốt.
  Câu 3 @ y=1563 ; A.m@1778  C.m@1779  B.m@1781  D.m@1781  (4 đáp án nằm SÁT NHAU 1 hàng)
  Câu 4 @ y=1966 ; nhưng dòng phân số của Câu 4: "x+1"@1941, "x"@1952, "= 0 là:"@1964  ← y NHỎ HƠN 1966!
PAGE 1 (page 2): CHỈ 1 block, type=figure, bbox y=46→3273 (gần cả trang).
  → mọi dòng (4 phương trình, Câu 6, Câu 7) bị nhồi vào 1 block figure khổng lồ.

exam.json kết quả lỗi:
  q5 pages=[1] bbox=[250,38,2422,3281]   ← nuốt cả trang 2
  q6 bbox=[0,968,2550,1034]  (66px)      ← chỉ còn dòng "Câu 6", mất nội dung
  q7 bbox=[0,1558,2550,1624] (66px)      ← tương tự
  q1 ans=2, q2 ans=2, q3 ans=3           ← mất đáp án B/C
```

Anchor "B.m =4" KHÔNG được tạo answer anchor vì OCR ghép sát: `B.m` (không space sau dấu chấm).

---

## BUG 1 🔴 (CRITICAL) — Snake Walker gom theo BLOCK thay vì LINE

### Nguyên nhân
`snake_walker.py` dùng `_gpos_of_block(b) = (b.page_index, b.bbox[1])` để gán block vào câu (hàm `snake_walk` Bước 3, và `_compute_multi_region` dùng `b.bbox`). Nhưng PaddleOCR layout `en` thường xuyên gom **cả trang thành 1 block** (`figure`). Khi đó:
- 1 block có y_top = 46 → mọi câu trên trang đó hoặc nuốt trọn block (cả trang) hoặc bị loại sạch.
- Thông tin vị trí thật nằm ở `block.lines[].bbox` (mỗi dòng OCR có bbox riêng, chính xác) — đang bị bỏ phí.

### Cách sửa — Chuyển toàn bộ Snake Walker sang LINE granularity

Tạo một đơn vị trung gian `PositionedLine` và làm việc trên nó thay vì Block:

```python
@dataclass
class PositionedLine:
    page_index: int
    bbox: BBox                 # bbox CỦA LINE (chính xác)
    text: str
    block_type: BlockType      # type của block cha (để biết figure/table/equation)
    confidence: float = 1.0

def _flatten_lines(blocks_per_page) -> list[PositionedLine]:
    """Bung mọi block thành list line, mỗi line giữ bbox riêng + type block cha."""
    out = []
    for page_blocks in blocks_per_page:
        for blk in page_blocks:
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
```

Sau đó **thay mọi chỗ dùng `all_blocks` + `_gpos_of_block` bằng `all_lines` + line position**:
- Bước 3 (gom vào câu): `q_lines = [l for l in all_lines if _in_range((l.page_index, l.bbox[1]), start, end)]`.
- `has_figure / has_table / has_formula`: kiểm tra `any(l.block_type == FIGURE for l in q_lines)`.
  ⚠️ **LƯU Ý:** vì cả trang có thể là 1 figure-block, cờ `has_figure` sẽ dương tính giả. Tạm thời chấp nhận / hoặc chỉ set has_figure nếu có line **rỗng text** thuộc block figure (figure thật thường không có text). Ghi chú TODO, không chặn.
- `_compute_multi_region`: nhận `list[PositionedLine]`, tính bbox từ `l.bbox` của từng line (KHÔNG dùng block bbox).
- Content/answer split: dùng line position thay block position.

### Kết quả mong đợi sau Fix 1
- Câu 5: vùng = các dòng phương trình thật ở đầu trang 2 (+ phần "Giải các phương trình" cuối trang 1), KHÔNG nuốt cả trang.
- Câu 6, 7: có đủ các dòng nội dung paragraph → crop ra nội dung thật.

---

## BUG 2 🔴 — Regex đáp án mất B/C (file Phase 1: `anchor_extractor.py`)

### Nguyên nhân
Pattern `AnchorType.ANSWER`:
```python
(re.compile(r"^\s*([A-D])\s*[\.\)]\s+\S"), 1)
```
Đòi `\s+` (>=1 khoảng trắng) sau `.`/`)`. OCR cho ra "B.m =4" (dính liền) → `[\.\)]\s+` trượt vì sau "." là "m".

### Cách sửa
Đổi `\s+` → `\s*` (cho phép 0 khoảng trắng), nhưng vẫn cần ký tự nội dung theo sau:
```python
(re.compile(r"^\s*([A-D])\s*[\.\)]\s*\S"), 1)
```
Áp dụng tương tự cho `SUB_QUESTION` (`a)`, `b)`) nếu cần: `^\s*([a-d])\s*\)\s*\S`.

### Kiểm thử nhanh (không cần OCR lại — chạy trên blocks.json sẵn có)
Sau khi sửa, đề toan8 phải ra **đủ 4 đáp án** cho Câu 1-4 (hiện tại 2,2,3,4).

> ⚠️ Lưu ý: việc này KHÔNG xử lý được trường hợp 4 đáp án nằm CHUNG 1 dòng OCR (xem Bug 4). Nó chỉ phục hồi các đáp án bị OCR tách thành line riêng nhưng dính dấu chấm.

---

## BUG 3 🔴 — Đáp án cuối "lẹm" sang câu sau (`snake_walker.py` + `cropper.py`)

### Nguyên nhân
Vùng đáp án cuối (`j == len-1`) đặt `a_end = end` (= anchor câu kế tiếp). Mọi line trong `[a_start, a_end)` bị gom vào đáp án cuối. Nhưng các dòng đầu của câu sau (đặc biệt **phân số toán** render cao hơn dòng số câu) có y < anchor câu sau → lọt vào.

Bằng chứng: Câu 4 có "x+1"@1941, "= 0 là:"@1964 < "Câu 4"@1966 → lọt vào vùng D của Câu 3.

### Cách sửa — Clip vùng đáp án theo nội dung thật, không kéo tới câu sau

Thay vì để đáp án cuối kéo tới `end`, **bound theo chiều cao hàng đáp án**:
```python
# Ước lượng chiều cao 1 dòng đáp án (median height các answer line đã thu được)
# Đáp án cuối: chỉ lấy các line cách a_start trong khoảng <= K * line_height
# HOẶC: dừng tại line đầu tiên mà text match pattern câu hỏi / phân số câu sau.
```
Đề xuất thuật toán đơn giản & robust:
1. Với mỗi đáp án, thu các line trong `[a_start, a_end)`.
2. **Lọc bỏ line "ngoại lai"**: tính y trung bình của line đầu (chính dòng đáp án). Chỉ giữ line có `y_top <= first_line_y_bottom + 0.5 * line_height` (cùng hàng / sát ngay dưới). Bỏ các line cách xa xuống dưới (đó là nội dung câu sau).
3. Với đáp án **không phải cuối**, `a_end` = đáp án kế (như cũ) — thường đã đúng.
4. Bổ sung: nếu một line trong vùng đáp án match regex QUESTION (vd "Cau 4...") hoặc rõ ràng là nội dung câu mới → cắt vùng tại đó.

> Mục tiêu: q3_D chỉ chứa đúng "D. m = 3" (hoặc cả hàng "B.m=4 C.m=-3 D.m=3" nếu OCR gộp), KHÔNG kèm "Câu 4...".

### Lưu ý tương tác với Bug 1
Sau khi chuyển sang line-granularity (Bug 1), việc clip này thao tác trên `PositionedLine` — dễ chính xác hơn vì mỗi dòng đáp án là 1 line riêng.

---

## BUG 4 🟡 (mức trung bình, làm sau Bug 1-3) — 4 đáp án chung 1 dòng OCR

### Hiện trạng
Khi OCR trả về cả hàng "A. ... B. ... C. ... D. ..." là **1 line duy nhất**, anchor extractor chỉ bắt được "A." (do `^`). 3 đáp án còn lại mất, và không tách được bbox riêng để crop 🟢 từng đáp án.

### Cách xử lý (đề xuất, scope vừa phải)
Trong `anchor_extractor.py`, với line bắt đầu bằng đáp án: dùng `re.finditer` quét **nhiều** mốc đáp án trong cùng 1 line:
```python
ANSWER_INLINE = re.compile(r"(?:^|\s)([A-D])\s*[\.\)]\s*")
for m in ANSWER_INLINE.finditer(normalized):
    # tạo answer anchor cho từng nhãn, bbox ước lượng theo tỉ lệ ký tự trong line
```
- bbox từng đáp án: chia ngang line theo vị trí ký tự (start offset của match / len(line) → nội suy x trên `line.bbox`). Không hoàn hảo nhưng đủ để crop 🟢.
- Nếu khó: tối thiểu **tạo đủ 4 answer anchor** (để classify đúng MCQ 4 đáp án) và set `needs_review=True`, crop đáp án có thể bỏ qua hoặc crop cả hàng.

> Đây là edge case sẽ gặp NHIỀU ở đề thật (đáp án dàn hàng ngang). Ưu tiên đúng số lượng đáp án trước, bbox chính xác sau.

---

## Thứ tự sửa & nghiệm thu

1. **Bug 2** (regex, 1 dòng) — nhanh, làm trước. Verify: toan8 ra đủ 4 đáp án Câu 1-4.
2. **Bug 1** (line-granularity) — refactor lớn nhất, sửa tận gốc figure-cả-trang. Verify: q5 không nuốt cả trang; q6/q7 có nội dung.
3. **Bug 3** (clip đáp án cuối) — verify: q3_D không kèm Câu 4.
4. **Bug 4** (đáp án inline) — verify: câu có đáp án dàn hàng ngang ra đủ 4.

### Tiêu chí PASS lại (chạy `parse_cli.py` 3 đề)
- **toan8**: q1-4 mỗi câu **4 đáp án**, crop đáp án không lẹm sang câu khác; q5,q6,q7 (essay) **có ảnh nội dung thật** (không phải 66px, không phải cả trang).
- **tienganh**: 50 câu, đa số 4 đáp án; passage crop đúng; không lẹm.
- **azota**: 50 câu (loại phần lời giải); câu có đồ thị crop kèm hình; không crash.
- Mở `overlay/page_XX.png` mắt thường thấy box 🔵 câu / 🟢 đáp án ôm đúng vùng, không chồng câu kế.

### CHANGELOG
Ghi `[Phase 2.4] - Fix line-granularity + answer regex + clip đáp án` với: nguyên nhân từng bug, file sửa, số liệu trước/sau (vd toan8 đáp án 2→4, q5 full-page→đúng vùng).

---

## Ghi chú cho người review (con người)
Bug 1 là hệ quả trực tiếp của việc layout model `en` không hiểu tài liệu tiếng Việt → gom cả trang thành figure. Đây là lý do **toàn bộ pipeline neo vào LINE bbox (luôn chính xác) chứ không tin BLOCK type/bbox**. Nguyên tắc này đã áp dụng ở Phase 1.2 (bỏ filter block type cho anchor) và giờ phải áp dụng nốt cho Snake Walker.
