# Setup Guide - exam_parser_paddle

**Mục tiêu**: Cài đặt toàn bộ môi trường để chạy pipeline Phương án P4 (PaddleOCR + VLM + Snake Walker).

**Phần cứng target**: RTX 5090 32GB VRAM trên WSL2 Ubuntu

---

## 1. Tổng quan các thành phần cần cài

| # | Thành phần | Lý do | Bắt buộc? |
|---|---|---|---|
| 1 | WSL2 + nVidia Driver Windows | GPU passthrough | ✅ Bắt buộc |
| 2 | Miniconda | Python env manager | ✅ Bắt buộc |
| 3 | PyTorch CUDA 12.8 | Cho VLM (Ollama) + utils | ✅ Bắt buộc |
| 4 | PaddlePaddle **CPU** | Backend cho PaddleOCR (Blackwell chưa GPU support) | ✅ Bắt buộc |
| 5 | PaddleOCR | Layout + OCR (CPU mode) | ✅ Bắt buộc |
| 6 | Ollama + Qwen3-VL-32B | Semantic VLM (Phase 3, hiện TẮT) | ⚠️ Tuỳ chọn |
| 7 | MinIO (Docker) | Object storage (crop + file gốc) | ✅ Bắt buộc |
| 8 | MongoDB (Docker) | Lịch sử đề thi (1 đề = 1 bản ghi) | ✅ Bắt buộc |
| 9 | PyMuPDF, Pillow, OpenCV | PDF/image processing | ✅ Auto cài qua pip |
| 10 | FastAPI, uvicorn | API service | ✅ Auto cài qua pip |

---

## 2. WSL2 + nVidia Driver (Windows side)

### 2.1 Cài WSL2

Mở PowerShell **as Administrator**:
```powershell
wsl --install -d Ubuntu-22.04
# Reboot nếu cần
```

### 2.2 Cài nVidia Driver cho Windows

- Tải driver mới nhất: https://www.nvidia.com/drivers
- Chọn: **RTX 5090** + **Windows 11** + **Game Ready** hoặc **Studio**
- Cài xong **không cần cài CUDA toolkit trong WSL** - driver Windows cung cấp CUDA runtime cho WSL2

### 2.3 Verify

Trong WSL Ubuntu:
```bash
nvidia-smi
# Phải hiện RTX 5090, CUDA Version: 12.x
```

---

## 3. System packages (WSL Ubuntu)

```bash
sudo apt update && sudo apt upgrade -y

# Build tools + libs cần thiết
sudo apt install -y \
    build-essential cmake git curl wget \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
    libgomp1 libgl1-mesa-glx \
    poppler-utils ghostscript
```

---

## 4. Miniconda

```bash
# Tải installer
cd ~
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh

# Cài (default options)
bash Miniconda3-latest-Linux-x86_64.sh -b

# Init shell
~/miniconda3/bin/conda init bash
exec bash   # reload

# Verify
conda --version
```

---

## 5. Tạo Conda environment

```bash
conda create -n exam_parser_paddle python=3.11 -y
conda activate exam_parser_paddle
```

**LƯU Ý**: Mọi lệnh `pip install` từ đây đều phải sau khi `conda activate`.

---

## 6. PyTorch CUDA 12.8 (cho RTX 5090 Blackwell sm_120)

```bash
# QUAN TRỌNG: cài PyTorch TRƯỚC PaddlePaddle (tránh conflict)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

# Verify - phải có sm_120 trong Archs
python -c "import torch; print('CUDA:', torch.cuda.is_available()); print('Device:', torch.cuda.get_device_name(0)); print('Archs:', torch.cuda.get_arch_list())"
```

**Expected output**:
```
CUDA: True
Device: NVIDIA GeForce RTX 5090
Archs: [..., 'sm_120', ...]   ← BẮT BUỘC có sm_120
```

**Nếu KHÔNG có `sm_120`**, dùng PyTorch nightly:
```bash
pip uninstall -y torch torchvision
pip install --pre torch torchvision --index-url https://download.pytorch.org/whl/nightly/cu128
```

---

## 7. PaddlePaddle - CPU mode (Blackwell chưa support)

⚠️ **QUAN TRỌNG**: RTX 5090 (Blackwell sm_120) **chưa được Paddle support** (tính đến 2026).
Paddle stable 3.0.0 không có kernel sm_120 → mọi GPU operation fail với CUDA error 209
"no kernel image is available for execution on the device".

**Giải pháp POC**: Dùng Paddle CPU mode. PaddleOCR trên CPU mạnh (Ryzen 9 / Intel i9) đạt
~3-6s/page - chấp nhận được cho POC. Khi Paddle release Blackwell support → switch GPU.

### Cài Paddle CPU (CHỌN CÁCH NÀY)

```bash
# Package CPU - không cần CUDA libs Paddle
pip install paddlepaddle==3.0.0

# Verify
python -c "import paddle; print('CUDA:', paddle.is_compiled_with_cuda())"
# Expected: CUDA: False  ← đúng (CPU mode)

# Test inference
python -c "
import paddle
x = paddle.randn([500, 500])
y = paddle.matmul(x, x)
print('CPU inference OK, shape:', y.shape)
"
```

### (Tham khảo) Khi nào dùng GPU?

Đợi 1 trong các điều kiện:
- Paddle release version stable có sm_120 (theo dõi: https://github.com/PaddlePaddle/Paddle/releases)
- Compile Paddle from source với `-arch=sm_120` (1-3 giờ build)
- PaddleOCR Docker image official build cho Blackwell

Khi GPU support có → switch:
```bash
pip uninstall -y paddlepaddle
pip install paddlepaddle-gpu==X.X.X -i <official-blackwell-index>
# Update .env: PADDLE_USE_GPU=true
```

### Trade-off CPU vs GPU dự kiến

| Stage | CPU (RTX 5090 box) | GPU (khi có Blackwell support) |
|---|---|---|
| PaddleOCR PP-Structure | ~3-6s/page | ~0.5-1s/page |
| Throughput đề 10 trang | ~40-60s | ~10s |

POC OK với CPU. Production optimize sau.

---

## 8. PaddleOCR

```bash
pip install "paddleocr>=2.7,<3.0"

# Verify import
python -c "from paddleocr import PaddleOCR, PPStructure; print('PaddleOCR OK')"
```

### 8.1 Pre-download models (tránh tải lúc chạy)

```bash
# Tải sẵn models Vietnamese OCR + layout
python << 'EOF'
from paddleocr import PaddleOCR, PPStructure

# OCR Vietnamese
ocr = PaddleOCR(use_angle_cls=True, lang="vi", use_gpu=True, show_log=False)
print("PaddleOCR Vietnamese OK")

# Layout structure
structure = PPStructure(layout=True, table=True, ocr=True, show_log=False)
print("PP-Structure OK")
EOF
```

Models sẽ tự tải về `~/.paddleocr/` (~500MB-1GB).

---

## 9. Ollama + Qwen3-VL-32B

### 9.1 Cài Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh

# Start service
sudo systemctl enable ollama
sudo systemctl start ollama
sleep 5

# Verify
ollama --version
```

### 9.2 Config Ollama dùng GPU

```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d

sudo tee /etc/systemd/system/ollama.service.d/override.conf > /dev/null <<'EOF'
[Service]
Environment="CUDA_VISIBLE_DEVICES=0"
Environment="OLLAMA_KEEP_ALIVE=60m"
Environment="OLLAMA_NUM_GPU=999"
Environment="OLLAMA_HOST=0.0.0.0:11434"
EOF

sudo systemctl daemon-reload
sudo systemctl restart ollama
sleep 5
```

### 9.3 Pull model

```bash
ollama pull qwen3-vl:32b-instruct
# ~22GB, mất 10-30 phút tuỳ mạng
```

### 9.4 Verify GPU

```bash
# Test inference
ollama run qwen3-vl:32b-instruct "hi" --verbose
```

Trong terminal khác:
```bash
nvidia-smi
# Phải thấy: ollama process + VRAM ~22GB
```

Hoặc:
```bash
ollama ps
# Output phải có "100% GPU" cho qwen3-vl:32b-instruct
```

Nếu thấy "CPU" → xem [troubleshooting Ollama GPU](#143-ollama-không-dùng-gpu).

---

## 10. MinIO (Docker)

### 10.1 Cài Docker (nếu chưa có)

```bash
# Trong WSL Ubuntu
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add user vào docker group
sudo usermod -aG docker $USER
newgrp docker

# Test
docker run hello-world
```

### 10.2 Chạy MinIO container

```bash
mkdir -p ~/minio-data

docker run -d \
  --name minio \
  --restart unless-stopped \
  -p 9000:9000 \
  -p 9001:9001 \
  -v ~/minio-data:/data \
  -e MINIO_ROOT_USER=admin \
  -e MINIO_ROOT_PASSWORD=admin12345 \
  quay.io/minio/minio server /data --console-address ":9001"

# Verify
docker ps | grep minio
curl http://localhost:9000/minio/health/live
# → 200 OK
```

**⚠️ Lưu ý port**:
- `9000` = S3 API (cho app/`mc` CLI/SDK)
- `9001` = Web Console UI (cho browser)

**⚠️ Lưu ý image**: Dùng `quay.io/minio/minio` thay `minio/minio` để tránh Docker Hub
rate limit (100 pull/6h cho unauthenticated). Nếu vẫn muốn dùng Docker Hub, chạy
`docker login` trước.

### 10.3 Tạo bucket

Mở browser: **http://localhost:9001**
- Login: `admin` / `admin12345`
- Sidebar → Create Bucket → tên: `exam-parser` → Create

### 10.4 Tạo Access Key (BẮT BUỘC qua CLI - Community Edition không có UI)

**Quan trọng**: MinIO Community Edition mới (2025+) đã CẮT phần "Access Keys"
khỏi Web Console. Chỉ Enterprise mới có. Phải dùng `mc` CLI:

```bash
# Exec vào container minio đang chạy
docker exec -it minio sh

# Setup mc alias trỏ tới chính MinIO (port 9000 = S3 API)
mc alias set local http://localhost:9000 admin admin12345

# Tạo service account = Access Key cho app dùng
mc admin user svcacct add local admin --name "exam-parser-app"
```

**Output sẽ kiểu**:
```
Access Key: 4FtGZ8KQpRX2VY9NhWmL
Secret Key: 3sKpL8mQrXvZ2YnHwBjA7tCdEfGhIjKl
Expiration: NONE
Status: enabled
```

**⚠️ COPY NGAY** Access Key + Secret Key — không hiện lại lần sau.

```bash
exit   # thoát container
```

### 10.5 Lưu credentials vào `.env`

Lưu vào file `.env` (sẽ tạo chi tiết ở §13):
```bash
MINIO_ACCESS_KEY=<paste Access Key>
MINIO_SECRET_KEY=<paste Secret Key>
```

**Alternative cho POC nhanh**: dùng luôn root credentials (đỡ phải tạo service account):
```bash
MINIO_ACCESS_KEY=admin
MINIO_SECRET_KEY=admin12345
```
⚠️ Không dùng cho production.

---

## 11. MongoDB (Docker)

Lưu lịch sử trích xuất: **mỗi đề thi = 1 bản ghi** trong collection `exams`. Bản ghi gồm
metadata + `output` (toàn bộ cấu trúc đã cắt) + đường dẫn MinIO của file gốc & ảnh crop.

### 11.1 Chạy MongoDB container

```bash
mkdir -p ~/mongo-data

docker run -d \
  --name mongo \
  --restart unless-stopped \
  -p 27017:27017 \
  -v ~/mongo-data:/data/db \
  -e MONGO_INITDB_ROOT_USERNAME=admin \
  -e MONGO_INITDB_ROOT_PASSWORD=admin12345 \
  mongo:7

# Verify
docker ps | grep mongo
docker exec -it mongo mongosh -u admin -p admin12345 --eval "db.runCommand({ping:1})"
# → { ok: 1 }
```

> **POC nhanh (không auth)**: bỏ 2 dòng `-e MONGO_INITDB_*` → URI là
> `mongodb://localhost:27017`. Nếu bật auth như trên → URI có user/pass (xem 11.3).

### 11.2 (Tuỳ chọn) Mongo Express — UI xem dữ liệu

⚠️ Docker cài native trong WSL **KHÔNG có** `host.docker.internal` → phải cho mongo-express
và mongo chung 1 network rồi gọi Mongo bằng TÊN CONTAINER (`mongo`):

```bash
# Tạo network + gắn container mongo (đang chạy) vào
docker network create exam-net 2>/dev/null || true
docker network connect exam-net mongo

docker run -d --name mongo-express --restart unless-stopped \
  --network exam-net \
  -p 8081:8081 \
  -e ME_CONFIG_MONGODB_URL="mongodb://admin:admin12345@mongo:27017/?authSource=admin" \
  -e ME_CONFIG_BASICAUTH=false \
  mongo-express

docker ps | grep mongo-express      # phải Up (không Exited)
# Mở http://localhost:8081
```

> Mongo chạy KHÔNG auth (bỏ `MONGO_INITDB_ROOT_*` ở 11.1) → dùng
> `ME_CONFIG_MONGODB_URL="mongodb://mongo:27017"` (không cần user/pass/authSource).
>
> Nếu mongo-express **Exited**: `docker logs mongo-express` — thường là không resolve host
> hoặc auth fail. Sửa theo 2 điểm trên.

### 11.3 Cấu hình `.env`

```bash
USE_MONGO=true
# Không auth (POC):
MONGO_URI=mongodb://localhost:27017
# Có auth (như 11.1):
# MONGO_URI=mongodb://admin:admin12345@localhost:27017/?authSource=admin
MONGO_DB=exam_parser
MONGO_COLLECTION=exams
```

### 11.4 Cấu trúc 1 bản ghi (collection `exams`)

```jsonc
{
  "_id": "920dc046",              // = exam_id
  "source_file": "de_thi.pdf",
  "status": "done",
  "created_at": "2026-06-06T...Z",
  "n_pages": 23, "n_questions": 50, "n_groups": 1, "n_mcq": 50, "n_essay": 0,
  "bucket": "exam-parser",
  "minio_prefix": "920dc046/",
  "raw": {                         // file gốc đã lưu MinIO
    "filename": "de_thi.pdf",
    "minio_key": "920dc046/raw/de_thi.pdf",
    "url": "https://.../presigned",
    "size_bytes": 1234567,
    "content_type": "application/pdf"
  },
  "metadata": { "ma_de": "...", "mon": "..." },
  "output": { /* toàn bộ Exam: questions[], groups[] với minio_key+url mỗi ảnh */ }
}
```

---

## 12. Cài Python packages của project

```bash
cd ~/exam_parser_paddle    # hoặc đường dẫn project
conda activate exam_parser_paddle

pip install -r requirements.txt
```

---

## 13. Tạo file `.env`

```bash
cd ~/exam_parser_paddle
cp .env.example .env
nano .env   # hoặc editor khác
```

Điền các giá trị:
```bash
# MinIO
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=<từ §10.4>
MINIO_SECRET_KEY=<từ §10.4>
MINIO_BUCKET=exam-parser
MINIO_SECURE=false
MINIO_SAVE_RAW=true              # lưu cả file gốc PDF
USE_MINIO_UPLOAD=true

# MongoDB (lịch sử đề thi — §11)
USE_MONGO=true
MONGO_URI=mongodb://localhost:27017
MONGO_DB=exam_parser
MONGO_COLLECTION=exams

# Pipeline (Phase 4/5: VLM tắt mặc định)
DEFAULT_DPI=300
USE_VLM_VERIFICATION=false
LOG_LEVEL=INFO
```

---

## 14. Verify toàn bộ

Chạy script kiểm tra:

```bash
cd ~/exam_parser_paddle
conda activate exam_parser_paddle
python scripts/verify_setup.py
```

**Expected output**:
```
✓ PyTorch CUDA: True (RTX 5090, 32.0GB)
✓ PyTorch archs: ['sm_70', ..., 'sm_120']
✓ PaddlePaddle GPU: True
✓ PaddleOCR import OK
✓ PaddleOCR inference test: detected 5 lines
✓ Ollama connected: 1 models
✓ Qwen3-VL-32B available: yes
✓ Ollama using GPU: yes (22.1GB VRAM)
✓ MinIO connected: localhost:9000
✓ MinIO bucket "exam-parser" exists
✓ MinIO upload/download test: OK

🎉 Setup complete! Ready to run pipeline.
```

Nếu có lỗi, xem §14 troubleshooting.

---

## 15. Troubleshooting

### 14.1 PyTorch không có sm_120 (RTX 5090)
```bash
pip uninstall -y torch torchvision
pip install --pre torch torchvision --index-url https://download.pytorch.org/whl/nightly/cu128
```

### 14.2 PaddlePaddle GPU không hoạt động

```bash
# Check error chi tiết
python -c "import paddle; paddle.utils.run_check()" 2>&1 | head -50
```

**Lỗi thường gặp + fix**:

- **`CUDA Driver Version is insufficient`**: Update nVidia driver Windows
- **`compute capability X.X not supported`**: PaddlePaddle chưa support Blackwell → dùng nightly (cách 7B) hoặc Docker (cách 7C)
- **`undefined symbol cudnn`**: Conflict CUDA version, reinstall PaddlePaddle

### 14.3 Ollama không dùng GPU

```bash
# Check log chi tiết
sudo journalctl -u ollama -n 100 --no-pager | grep -iE "gpu|cuda|inference"

# Phải thấy: "inference compute id=GPU-... library=cuda compute=12.0"

# Nếu thấy "library=cpu":
# 1. Reinstall Ollama latest
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl restart ollama

# 2. Check CUDA visible
echo $CUDA_VISIBLE_DEVICES   # phải = 0

# 3. Hard restart
sudo systemctl stop ollama
sleep 2
sudo systemctl start ollama
```

### 14.4 MinIO không truy cập được từ Windows browser

- Default `localhost:9001` work với WSL2 (forward port tự động)
- Nếu fail, dùng IP WSL: `ip addr show eth0 | grep inet` → `http://<wsl-ip>:9001`

### 14.5 Lỗi `EPERM` khi chạy script trên WSL

→ Project folder đang ở `/mnt/c/...` (Windows). **Phải move sang WSL filesystem**:
```bash
mv /mnt/c/Users/me/exam_parser_paddle ~/exam_parser_paddle
cd ~/exam_parser_paddle
```

### 14.6 Out of memory (CUDA OOM)

VRAM bị chiếm quá:
```bash
# Restart Ollama (giải phóng model VRAM)
sudo systemctl restart ollama

# Kill orphan Python
pkill -9 python

# Kiểm tra VRAM
nvidia-smi
```

Nếu cần giảm memory:
- Dùng Qwen3-VL nhỏ hơn: `ollama pull qwen3-vl:7b-instruct`
- Reduce PaddleOCR batch size

---

## 15. Tổng kết - Checklist trước khi chạy

- [ ] WSL2 Ubuntu 22.04 + nVidia driver Windows
- [ ] `nvidia-smi` thấy RTX 5090
- [ ] Miniconda installed
- [ ] Conda env `exam_parser_paddle` activated
- [ ] PyTorch CUDA 12.8, archs có `sm_120`
- [ ] PaddlePaddle CPU import OK (`paddle.is_compiled_with_cuda()` = False)
- [ ] PaddleOCR import + inference CPU OK
- [ ] Ollama service running, GPU mode
- [ ] Qwen3-VL-32B pulled
- [ ] MinIO container running
- [ ] MinIO bucket `exam-parser` tạo xong
- [ ] `.env` đã điền đầy đủ
- [ ] `scripts/verify_setup.py` pass tất cả checks

---

## 16. Bước tiếp theo

Sau khi setup xong → bắt đầu implement pipeline:

```bash
# Test pipeline với file mẫu
python scripts/parse_cli.py input/de_thi_mau.pdf

# Output:
# - output/{exam_id}/exam.json
# - output/{exam_id}/questions/*.webp
# - Hoặc upload trực tiếp MinIO
```

Xem [README.md](README.md) cho usage chi tiết.
