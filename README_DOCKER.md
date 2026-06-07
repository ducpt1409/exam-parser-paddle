# AI Service — Đóng gói & chạy bằng Docker

`exam_parser_paddle` đã được đóng gói thành **1 AI service** với đúng **1 endpoint xử lý**.
Nhận file đề thi → chạy pipeline (PaddleOCR + Snake Walker → cắt ảnh + overlay) → đẩy
hết lên **MinIO** + lưu lịch sử **MongoDB**. **Không** giữ file crop ở local.

> CLI cũ (`scripts/parse_cli.py`) vẫn giữ nguyên để debug — xem ghi chú cuối file.

---

## 1. Kiến trúc

`docker-compose.yml` của AI service **chỉ chứa 1 service**: `ai-service`. Hạ tầng (MinIO +
MongoDB) nằm ở stack RIÊNG `../exam_parser_infra`.

| Stack | Service | Cổng | Vai trò |
|---|---|---|---|
| **exam_parser_infra** | `minio` | 9000 / 9001 | Object storage (API / Console) |
| | `minio-init` | — | Tạo bucket 1 lần |
| | `mongo` | 27017 | Lịch sử đề thi |
| | `mongo-express` | 8081 | UI xem Mongo (tùy chọn) |
| **exam_parser_paddle** | `ai-service` | 8000 | FastAPI + pipeline (image build từ `Dockerfile`) |

`ai-service` kết nối hạ tầng qua **host** (`host.docker.internal:9000` / `:27017`),
đã có `extra_hosts: host-gateway` để chạy được trên **WSL native Docker**.

---

## 2. Build & chạy

> **Bước 0 — chạy hạ tầng TRƯỚC** (xem `../exam_parser_infra/README.md`):
> ```bash
> cd ../exam_parser_infra && cp .env.example .env && docker compose up -d
> ```

```bash
cd exam_parser_paddle

# (tuỳ chọn) override biến — nếu bỏ qua sẽ dùng default trong compose
cp .env.docker.example .env     # sửa user/password cho khớp exam_parser_infra nếu cần

# Build image AI service
docker compose build ai-service

# Chạy AI service
docker compose up -d

# Xem log
docker compose logs -f ai-service
```

Chờ tới khi healthcheck `ai-service` chuyển sang **healthy** (lần đầu lâu hơn vì PaddleOCR
tải model về volume `paddle_models`). Kiểm tra:

```bash
curl http://localhost:8000/api/v1/health
# {"status":"ok","vlm_enabled":false,"minio_endpoint":"host.docker.internal:9000","mongo_enabled":true}
```

### Build lại sau khi đổi code
```bash
docker compose build ai-service && docker compose up -d ai-service
```

### Dừng / xoá
```bash
docker compose down            # dừng AI service (data ở stack infra, không mất)
docker compose down -v         # + xoá volume cache model Paddle (sẽ tải lại lần sau)
```

> Data MinIO/Mongo nằm ở stack `exam_parser_infra` — muốn xoá data thì `down -v` ở bên đó.

---

## 3. API — 1 endpoint duy nhất

### `POST /api/v1/exams/parse`
- **Body**: `multipart/form-data`, field **`file`** = file đề thi (`.pdf/.png/.jpg/.jpeg`).
- **Trả về**: CHỈ trạng thái + `exam_id` (không trả JSON cấu trúc câu hỏi).

**Thành công (HTTP 200):**
```json
{
  "status": "done",
  "exam_id": "a1b2c3d4",
  "message": "Đã xử lý xong và lưu lên MinIO/Mongo",
  "n_pages": 3,
  "n_questions": 40,
  "n_groups": 2,
  "bucket": "exam-parser",
  "minio_prefix": "exams/a1b2c3d4/"
}
```

**Lỗi (HTTP theo bảng mã):**
```json
{
  "status": "failed",
  "exam_id": "a1b2c3d4",
  "stage": "ocr",
  "error_code": "E102",
  "message": "Lỗi PaddleOCR (layout/OCR)",
  "detail": "..."
}
```

Thử nhanh:
```bash
curl -X POST http://localhost:8000/api/v1/exams/parse \
     -F "file=@input/de_mau_azota_toan_THPT.pdf"
```

Swagger UI: http://localhost:8000/docs

---

## 4. Bảng mã lỗi (định nghĩa ở `src/core/errors.py`)

| Mã | Stage | HTTP | Ý nghĩa |
|---|---|---|---|
| `E400` | input | 400 | File đầu vào không hợp lệ |
| `E415` | input | 415 | Định dạng file không hỗ trợ |
| `E422` | input | 422 | File rỗng |
| `E101` | preprocess | 500 | Lỗi render/tiền xử lý PDF |
| `E102` | ocr | 500 | Lỗi PaddleOCR (layout/OCR) |
| `E103` | anchor | 500 | Lỗi trích xuất anchor |
| `E104` | snake_walker | 500 | Lỗi gom câu/nhóm |
| `E105` | classify | 500 | Lỗi phân loại câu hỏi |
| `E106` | crop | 500 | Lỗi cắt ảnh / overlay |
| `E107` | minio_upload | 502 | Lỗi upload MinIO |
| `E108` | mongo_save | 502 | Lỗi lưu MongoDB |
| `E500` | unknown | 500 | Lỗi không xác định |

BE chỉ cần bắt theo `error_code` (ổn định), không phụ thuộc `message`.

---

## 5. Kết quả lưu ở đâu

Trên MinIO, mỗi đề nằm dưới prefix `{MINIO_PREFIX}{exam_id}/`:

```
exams/a1b2c3d4/
  raw/<tên file gốc>.pdf      # file gốc
  crops/qN_full.png ...       # ảnh từng câu / đáp án / passage
  overlay/page_00.png ...     # ảnh debug bbox màu (giống chạy CLI)
  exam.json                   # cấu trúc đầy đủ (chứa presigned URL các ảnh)
```

Trên MongoDB (`exam_parser.exams`): 1 document `_id = exam_id`, gồm thống kê + `raw` +
`output` (toàn bộ Exam). Xem bằng Mongo Express: http://localhost:8081

---

## 6. Lưu ý presigned URL khi chạy Docker

Mặc định `MINIO_ENDPOINT=host.docker.internal:9000`. Presigned URL sẽ ký theo host này →
**không mở trực tiếp từ trình duyệt ngoài**. Nhưng không sao: **BE lấy ảnh bằng `minio_key`**
(đọc bytes trực tiếp rồi convert base64), KHÔNG dùng presigned URL → trang verify vẫn hiện ảnh.

Nếu cần presigned URL mở được từ trình duyệt, đặt `MINIO_ENDPOINT` thành địa chỉ public của
MinIO (domain/IP LAN, cổng 9000 reachable).

---

## 7. CLI debug (giữ nguyên)

Để soi từng bước, vẫn chạy như cũ trong conda env trên host (ghi file ra `output/{exam_id}/`):

```bash
python scripts/parse_cli.py input/de.pdf
python scripts/parse_cli.py input/de.pdf --no-upload   # cắt local, không upload
```

CLI ghi `crops/`, `overlay/`, `blocks.json`, `anchors.json`, `exam.json`, `summary.txt`,
`parse.log` để kiểm tra thủ công.
