# Quy tắc làm việc cho mọi phiên Codex

Các quy tắc này áp dụng cho toàn bộ repository.

## Trước khi làm việc

1. Đọc `PROJECT_STATUS.md`.
2. Đọc đặc tả tại `docs/superpowers/specs/2026-09-18-telegram-news-bot-design.md`.
3. Kiểm tra `git status --short` và không ghi đè thay đổi chưa rõ nguồn gốc.
4. Chỉ tiếp tục mục được ghi tại `Bước đang thực hiện` trong `PROJECT_STATUS.md`, trừ khi người dùng đổi phạm vi.

## Trong khi làm việc

- Chỉ có một bước ở trạng thái `IN_PROGRESS`.
- Trước khi bắt đầu bước mới, cập nhật trạng thái bước đó thành `IN_PROGRESS`.
- Sau khi hoàn thành, chạy kiểm tra tương ứng và ghi bằng chứng ngắn gọn vào bảng tiến độ.
- Nếu có lỗi, thêm một dòng vào `Nhật ký lỗi` trước khi sửa. Ghi mã lỗi, bước, biểu hiện, nguyên nhân nếu biết, cách xử lý và trạng thái.
- Không đánh dấu `DONE` nếu tiêu chí nghiệm thu chưa đạt.
- Không ghi API key, bot token, chat ID thật hoặc dữ liệu bí mật vào repository hay log.

## Trước khi kết thúc phiên

1. Cập nhật `PROJECT_STATUS.md`: bước vừa làm, kết quả kiểm tra, lỗi còn mở, bước tiếp theo và câu lệnh nên chạy tiếp.
2. Đảm bảo không còn hai bước cùng `IN_PROGRESS`.
3. Commit tài liệu trạng thái cùng thay đổi mã liên quan khi phù hợp.
4. Tóm tắt cho người dùng bằng mã bước để phiên/tài khoản tiếp theo tiếp tục chính xác.

## Trạng thái chuẩn

- `PENDING`: chưa bắt đầu.
- `IN_PROGRESS`: đang thực hiện.
- `BLOCKED`: không thể tiếp tục nếu chưa có dữ liệu/quyết định bên ngoài.
- `FAILED`: đã thử nhưng chưa đạt; phải có mã lỗi trong nhật ký.
- `DONE`: đã đạt tiêu chí và có bằng chứng kiểm tra.

