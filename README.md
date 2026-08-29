# VI-Translate (PDF Translation Tool)

<p align="center">
  <b>Dịch tài liệu PDF khoa học, giáo trình kỹ thuật sang tiếng Việt — Giữ nguyên 100% bố cục, bảng biểu và công thức toán học.</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Version-2.0.0-blue.svg" alt="Version 2.0.0">
  <img src="https://img.shields.io/badge/Python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue" alt="Python Versions">
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20Google%20Colab-green" alt="Platforms">
  <img src="https://img.shields.io/badge/Threads-Up%20to%20256-orange" alt="Threads">
  <img src="https://img.shields.io/badge/Tests-84%20Passing-brightgreen" alt="Tests">
  <img src="https://img.shields.io/badge/License-AGPL--3.0-lightgrey" alt="License">
</p>

---

## 📸 Giao diện Ứng dụng & Kết quả Thực tế

### 1. Giao diện Dịch thuật Kéo thả & Tiến độ Thời gian thực
Giao diện trực quan, hỗ trợ kéo thả nhiều file/thư mục, hiển thị phần trăm hoàn thành, trang đang dịch và ước tính thời gian còn lại (ETA):

<p align="center">
  <img src="docs/images/app_main.png" alt="Giao diện chính VI-Translate" width="95%">
</p>

### 2. Tab Cài đặt & Thuật ngữ Chuyên ngành Bảo tồn (Custom Glossary)
Bảo tồn chính xác các thuật ngữ công nghệ/y khoa không cần dịch, tùy chỉnh số luồng song song lên tới 256 luồng và hỗ trợ tăng tốc phần cứng GPU (DirectML):

<p align="center">
  <img src="docs/images/app_settings.png" alt="Cài đặt và Thuật ngữ chuyên ngành" width="85%">
</p>

### 3. Kết quả Dịch Thực tế (Bảo toàn 100% Công thức Toán học & Biểu đồ)
Minh chứng thực tế dịch trang giáo trình kỹ thuật bán dẫn (Semiconductors): bảo toàn nguyên vẹn phân số, ma trận, căn bậc hai, chỉ số trên/dưới, đồ thị và căn chỉnh chuẩn xuất bản:

<p align="center">
  <img src="docs/images/translation_demo.png" alt="Kết quả dịch thực tế bảo toàn công thức" width="90%">
</p>

---

## 🚀 Điểm nổi bật & Tính năng nâng cấp phiên bản 2.0.0

- 📐 **Bảo toàn công thức toán học đỉnh cao:** Giữ nguyên vẹn mọi công thức LaTeX phức tạp, phân số lồng nhau, ma trận, số mũ, chỉ số dưới, căn bậc hai $\sqrt{E}$, ký tự Hy Lạp ($\lambda, \mu, \Delta, \xi, \dots$) và các biến số có gạch đầu ($\bar{n}_1, d\bar{n}_1/d\lambda$).
- ⚡ **Tốc độ siêu tốc (Mở khóa tới 256 luồng):** Tối ưu hóa HTTP Connection Pool (`urllib3/requests`) xử lý đồng thời hàng trăm trang tài liệu trong vài chục giây.
- 🎯 **Trí tuệ nhân tạo nhận diện bố cục (`DocLayout-YOLO`):** Tự động phát hiện và phân vùng chính xác văn bản, hình vẽ, bảng biểu, chú thích hình và tiêu đề.
- 🔤 **Tự động xử lý font chữ & typography tiếng Việt:** Tích hợp font Unicode cao cấp (*BeVietnamPro* & *GoNotoKurrent*), tự động chống lỗi mất dấu, chống rách chữ, tự động hàn gắn chữ cái đầu dòng (`Since` $\to$ `Vì`, `where` $\to$ `trong đó`) và giữ cỡ chữ đồng đều toàn trang.
- 📄 **Tùy chọn trang linh hoạt:** Cho phép dịch toàn bộ tài liệu hoặc chọn dải trang tùy ý (ví dụ: `1-50, 75, 100-120`).
- ☁️ **Chạy đa nền tảng:** Hỗ trợ Windows Desktop App (file `.exe` chạy ngay không cần cài Python), Linux, macOS và **Google Colab**.

---

## 📥 Tải về & Cài đặt

### Cách 1: Dùng bản Windows App đóng gói sẵn (Khuyên dùng cho người dùng phổ thông)
1. Tải bản mới nhất tại [Releases](https://github.com/DATWY/PDF-Translate-VI/releases/latest) (Tải file `PDFTranslate-windows.zip`).
2. Giải nén thư mục và nhấp đúp vào `PDFTranslate.exe` để sử dụng ngay.
3. *Không cần cài đặt Python, không cần cài thư viện.*

---

### Cách 2: Chạy trên Google Colab (Siêu tốc với mạng Gigabit)

Chỉ cần mở một Google Colab Notebook và chạy các lệnh:

```python
# 1. Tải mã nguồn & cài đặt thư viện
!git clone https://github.com/DATWY/PDF-Translate-VI.git /content/PDF-Translate-VI
%cd /content/PDF-Translate-VI
!pip install -q -r requirements.txt
!python scripts/fetch_assets.py

# 2. Dịch PDF với 64 luồng song song
!python -m pdf2zh.pdf2zh "duong_dan_file.pdf" -li en -lo vi -t 64 -o "thu_muc_xuat"
```

---

### Cách 3: Chạy từ mã nguồn Python (Dành cho lập trình viên)

```bash
# 1. Clone repository
git clone https://github.com/DATWY/PDF-Translate-VI.git
cd PDF-Translate-VI

# 2. Khởi tạo môi trường ảo
python -m venv .venv
# Trên Windows:
.venv\Scripts\activate
# Trên Linux/macOS:
source .venv/bin/activate

# 3. Cài đặt dependencies và tải assets model
pip install -r requirements.txt
python scripts/fetch_assets.py

# 4. Khởi chạy giao diện Desktop GUI
python -m app.gui
```

---

## 📖 Hướng dẫn sử dụng

### 1. Sử dụng Giao diện Desktop (GUI)
- **Kéo thả** một hoặc nhiều file PDF (hoặc cả thư mục) vào giao diện.
- Chọn **Ngôn ngữ nguồn** (mặc định: `en - English`) và **Ngôn ngữ đích** (mặc định: `vi - Tiếng Việt`).
- Chọn **Dải trang** (để trống nếu muốn dịch toàn bộ).
- Kéo thanh trượt **Số luồng song song** (khuyên dùng `16 - 64` cho mạng thông thường, `128 - 256` cho mạng tốc độ cao).
- Bấm nút **Bắt đầu dịch**. Kết quả được lưu tự động trong thư mục `translated/` cùng vị trí file nguồn.

### 2. Sử dụng dòng lệnh (CLI)
```bash
# Dịch toàn bộ file với 32 luồng:
python scripts/translate_pdf.py input.pdf --output-dir output --threads 32

# Dịch dải trang cụ thể (trang 1 đến 50):
python scripts/translate_pdf.py input.pdf --output-dir output --pages 1-50 --threads 64

# Sử dụng engine dịch thuật tùy chọn (google, bing, deepl, ollama):
python scripts/translate_pdf.py input.pdf --output-dir output --engine google
```

---

## 🛠️ Đóng gói ứng dụng Windows (.exe)

Nếu bạn sửa đổi mã nguồn và muốn đóng gói lại thành file `.exe` độc lập:

```powershell
# Chạy script đóng gói tự động trên PowerShell:
powershell -ExecutionPolicy Bypass -File build.ps1
```
*Kết quả đóng gói sẽ nằm trong thư mục `dist_v19/` kèm file nén `PDFTranslate-windows.zip`.*

---

## 📋 Kiểm thử tự động (Unit Tests)

Dự án đi kèm bộ 84 bài kiểm thử tự động bao quát toàn bộ tính năng xử lý công thức, font chữ, độ phân giải bảng biểu, xử lý TOC và kiểm soát luồng:

```bash
python -m unittest discover tests
```

---

## 📜 Giấy phép & Ghi công

Dự án được phân phối theo giấy phép [AGPL-3.0](LICENSE).  
Phát triển và nâng cấp dựa trên nền tảng [PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate) & [BabelDOC](https://github.com/funstory-ai/BabelDOC). Xem chi tiết tại [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

