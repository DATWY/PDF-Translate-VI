# VI Translate

Dịch file PDF sang tiếng Việt nhưng giữ nguyên bố cục trang. Công thức toán, bảng, hình, mục lục và danh mục tài liệu tham khảo vẫn nằm đúng chỗ cũ.

Công cụ không trích chữ ra dịch rồi đổ vào một file trắng. Nó dò bố cục từng trang, khoanh vùng công thức và code để không đụng tới, dịch phần văn xuôi, rồi đặt chữ đã dịch trở lại đúng khung của bản gốc.

## Tải về

[Tải bản mới nhất ở đây](https://github.com/breslee1707/VI-Translate/releases/latest), giải nén, chạy `PDFTranslate.exe`.

Không cần cài Python. Lần chạy đầu cũng không cần mạng, vì model nhận diện bố cục và font đã nằm sẵn trong file tải về. Bản nén 198 MB, giải nén ra 382 MB.

Lần đầu mở, Windows SmartScreen sẽ chặn vì file chưa ký số. Bấm *More info* rồi *Run anyway*.

Hiện chỉ có bản Windows.

## Cách dùng

Đưa file vào bằng một trong ba cách:

- Kéo thả file PDF hoặc cả thư mục vào cửa sổ app
- Bấm nút *Chọn file* hoặc *Chọn thư mục*
- Thả file thẳng lên icon `PDFTranslate.exe`

Chọn ngôn ngữ rồi bấm *Dịch*. App chạy lần lượt từng file, kết quả ghi vào thư mục `translated` nằm cạnh file nguồn. Nếu một file bị lỗi thì app đánh dấu file đó rồi chạy tiếp, không dừng cả loạt.

Muốn dịch lại một file đã có kết quả thì tick ô *Ghi đè file đã dịch trước đó*. Mặc định app không ghi đè.

## Ngôn ngữ

Mặc định là tiếng Việt. Trong app có sẵn 36 ngôn ngữ dùng chữ Latin: Anh, Pháp, Đức, Tây Ban Nha, Bồ Đào Nha, Ý, Indonesia, Hà Lan, Ba Lan, Thổ Nhĩ Kỳ cùng các thứ tiếng châu Âu khác.

Không hỗ trợ tiếng Trung, Nhật, Hàn, Ả Rập, Do Thái, Thái và các chữ Ấn Độ. Font đi kèm không có glyph cho những chữ đó. App sẽ báo lỗi rõ ràng thay vì cho ra một file đầy ô vuông.

## Những gì công cụ không làm được

Không có OCR. Nếu file PDF chỉ là ảnh scan, không có lớp chữ bên dưới, thì công cụ không dịch được. Bạn cần chạy OCR trước.

Chữ nằm trong vùng mà công cụ nhận diện là bảng hoặc hình đôi khi bị giữ nguyên tiếng gốc. Nên mở file kết quả kiểm tra lại.

Trang mục lục, index, danh mục ký hiệu và tài liệu tham khảo được cố ý giữ nguyên bố cục, không dàn lại dòng. Lý do và chi tiết nằm ở [references/preservation-rules.md](references/preservation-rules.md).

Đoạn văn dài quá 5000 ký tự bị cắt bớt. Trường hợp này hiếm, vì mỗi đoạn thường ngắn hơn nhiều.

Công thức phân số nằm giữa dòng chữ được chừa chỗ theo chiều cao thật của nó, nhưng chỗ chừa chỉ lấy được trong phần trống còn lại của đoạn văn. Đoạn nào vốn đã chật thì mẫu số vẫn hơi sát dòng bên dưới. Vẫn đọc được, chỉ là không thoáng bằng bản gốc.

## Dùng như Agent Skill

Repo này đồng thời là một [Agent Skill](https://agentskills.io/), dùng chung được với Codex, Claude Code, GitHub Copilot và các coding agent hỗ trợ chuẩn `SKILL.md`.

Cài cho tất cả agent mà máy đang có:

```powershell
npx skills add breslee1707/VI-Translate -g --all
```

Lệnh trên giữ một nguồn skill duy nhất rồi đăng ký nó vào đúng thư mục của từng agent, tránh phải duy trì nhiều bản `SKILL.md`. Nếu chỉ muốn cài cho một agent, thêm `--agent codex`, `--agent claude-code` hoặc tên agent tương ứng thay cho `--all`.

Skill tự tạo môi trường Python riêng trong thư mục cài đặt. Nếu muốn chuẩn bị thủ công:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Gọi trong Codex:

> Use $pdf-translate to translate this PDF into Vietnamese.

Trong Claude Code hoặc Copilot CLI:

> /pdf-translate translate this PDF into Vietnamese.

Ở đây có hai chế độ dịch:

| | Dịch bởi | Chi phí |
| --- | --- | --- |
| Google | translate.google.com | Miễn phí, không cần API key |
| Handoff | Agent trong khung chat | Tốn token, chất lượng cao hơn |

Google là chế độ mặc định, và cũng là chế độ duy nhất bản app desktop dùng, vì một file exe không có agent để gọi.

Chế độ handoff trích toàn bộ đoạn văn ra file JSONL, coding agent đang chạy sẽ dịch ngay trong phiên làm việc rồi công cụ dựng lại PDF. Không gửi gì lên Google. Chênh lệch chất lượng thấy rõ ở tài liệu chuyên ngành. Ví dụ với một văn bản về truyền nhiệt, cùng từ *conduction*:

| | Kết quả |
| --- | --- |
| Google | "Sự **dẫn điện** xảy ra khi hai vật tiếp xúc trực tiếp" |
| Handoff | "**Dẫn nhiệt** xảy ra khi hai vật thể tiếp xúc trực tiếp với nhau" |

Đổi lại, handoff tốn token và chậm hơn. Một cuốn sách 300 trang có tới vài nghìn đoạn văn, nên nếu chỉ cần đọc hiểu thì Google là đủ.

Gọi thẳng runner:

```powershell
# Google
.venv\Scripts\python.exe scripts\translate_pdf.py INPUT.pdf --output-dir OUT

# Handoff, bước 1: trích đoạn văn ra cho agent dịch
.venv\Scripts\python.exe scripts\translate_pdf.py INPUT.pdf --engine handoff --emit-segments segments.jsonl
# bước 2: agent dịch segments.jsonl thành translations.jsonl
# bước 3: dựng lại PDF
.venv\Scripts\python.exe scripts\translate_pdf.py INPUT.pdf --engine handoff --segments translations.jsonl --output-dir OUT
```

Quy trình handoff đầy đủ nằm trong [SKILL.md](SKILL.md).

## Giấy phép

AGPL-3.0. Đây là bản rút gọn từ [PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate) 1.9.11 và [BabelDOC](https://github.com/funstory-ai/BabelDOC). Chi tiết ghi công ở [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Giấy phép này áp dụng cho cả file exe. Nếu bạn phát hành lại app hoặc chạy nó như một dịch vụ qua mạng thì phải kèm theo mã nguồn tương ứng.
