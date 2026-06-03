# exam_parser_paddle

**Pipeline AI bóc tách câu hỏi từ đề thi PDF/ảnh → JSON + ảnh crop lưu MinIO.**

Phương án P4: PaddleOCR (data layer) + Qwen3-VL-32B (semantic layer) + Snake Walker (cross-page native).

---

## 🎯 Mục tiêu

Input: PDF / ảnh scan đề thi (mọi môn, mọi format)
Output:
- **JSON** đầy đủ cấu trúc bài thi (câu hỏi, đáp án, nhóm câu, loại câu)
- **Ảnh crop** từng câu hỏi / đáp án (giữ nguyên hình vẽ, công thức, bảng)
- Lưu MinIO, trả URL qua FastAPI

---

## 📋 Quick start

### 1. Setup môi trường
Xem [SETUP.md](SETUP.md) - chi tiết từng bước.

Tóm tắt:
```bash
# WSL2 Ubuntu + nVidia driver Windows
conda create -n exam_parser_paddle python=3.11 -y
conda activate exam_parser_paddle

pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
pip install paddlepaddle-gpu==3.0.0rc1 -i https://www.paddlepaddle.org.cn/packages/stable/cu123/
pip install -r requirements.txt

# Services
docker compose up -d                          # MinIO
ollama pull qwen3-vl:32b-instruct            # VLM
```

### 2. Verify setup
```bash
python scripts/verify_setup.py
```

### 3. Parse 1 đề thi (CLI)
```bash
python scripts/parse_cli.py input/de_thi.pdf
```

### 4. Chạy API service
```bash
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

Truy cập: http://localhost:8000/docs

---

## 🏗️ Kiến trúc

```
PDF/Image
   ↓
[1] Preprocess (PyMuPDF render + OpenCV deskew)
   ↓
[2] PaddleOCR PP-StructureV3
    → blocks (text/figure/table/list/title) + bbox + OCR text
   ↓
[3] Anchor Extraction (regex từ text)
    → anchors (Câu N, A./B./C./D., Phần I, ...)
   ↓
[4] VLM Verify (Qwen3-VL on-demand)
    → bổ sung anchor nếu OCR miss
   ↓
[5] Snake Walker (global cross-page)
    → group blocks by anchor boundaries
    → tự nhiên span pages
   ↓
[6] Question Type Classifier
    → rule-based + VLM fallback
   ↓
[7] Crop + Upload MinIO
   ↓
Output JSON với MinIO URLs
```

Chi tiết: [PADDLEOCR_HYBRID_APPROACHES.md](../PADDLEOCR_HYBRID_APPROACHES.md) (parent folder)

---

## 📁 Project structure

```
exam_parser_paddle/
├── README.md                    # File này
├── SETUP.md                     # Hướng dẫn cài đặt chi tiết
├── requirements.txt             # Python dependencies
├── docker-compose.yml           # MinIO service
├── .env.example                 # Template env vars
├── config/
│   └── settings.yaml            # App config
├── src/
│   ├── api/                     # FastAPI service
│   ├── core/                    # Config, logging
│   ├── services/                # Business logic
│   └── schemas/                 # Pydantic models
├── scripts/
│   ├── verify_setup.py          # Check toàn bộ setup
│   └── parse_cli.py             # CLI parser
├── input/                       # Đặt PDF test
├── output/                      # Output local (dev)
└── tests/                       # Unit + integration tests
```

---

## 🔧 Configuration

Tạo `.env` từ `.env.example`:
```bash
cp .env.example .env
# Edit .env với access keys MinIO + Ollama host
```

---

## 📚 Documentation

- [SETUP.md](SETUP.md) - Cài đặt môi trường (WSL, PaddlePaddle, Ollama, MinIO)
- [PROJECT_PLAN.md](../PROJECT_PLAN.md) - Plan tổng thể project
- [PADDLEOCR_HYBRID_APPROACHES.md](../PADDLEOCR_HYBRID_APPROACHES.md) - So sánh các phương án
- [MINIO_SETUP.md](../MINIO_SETUP.md) - Chi tiết MinIO setup
- [REGION_DETECTION_APPROACHES.md](../REGION_DETECTION_APPROACHES.md) - 4 phương án region detection

---

## 🛠️ Tech stack

| Layer | Tool | Version |
|---|---|---|
| Language | Python | 3.11 |
| ML Framework | PyTorch (CUDA 12.8) | 2.5+ |
| Document Parser | PaddleOCR PP-StructureV3 | 2.7+ |
| Backend Paddle | PaddlePaddle GPU | 3.0.0rc1+ |
| Vision LLM | Qwen3-VL-32B-Instruct | via Ollama |
| API Framework | FastAPI | 0.110+ |
| Object Storage | MinIO | Latest |
| PDF Processing | PyMuPDF (fitz) | 1.24+ |
| Image Processing | OpenCV, Pillow | Latest |

---

## 📊 Performance target (RTX 5090 32GB)

| Metric | Target |
|---|---|
| Tốc độ/trang | 2-5s |
| Throughput | 100-200 đề/giờ |
| Question recall | >95% |
| Bbox accuracy | >95% |
| Cross-page accuracy | >90% |

---

## 🗺️ Roadmap

- [x] Phase 0: Setup foundation
- [ ] Phase 1: PaddleOCR pipeline core (1 tuần)
- [ ] Phase 2: Snake walker + cross-page (1 tuần)
- [ ] Phase 3: VLM semantic layer (1 tuần)
- [ ] Phase 4: FastAPI + MinIO integration (1 tuần)
- [ ] Phase 5: Test + tune (1 tuần)
