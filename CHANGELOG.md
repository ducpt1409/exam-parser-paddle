# Changelog

Lịch sử thay đổi của `exam_parser_paddle`. Mỗi phase ghi rõ mục đích + file đã sửa.

Format: `[Phase X.Y] - YYYY-MM-DD - Title`

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
