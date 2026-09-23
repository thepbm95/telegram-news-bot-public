# Thiết kế Telegram nhiều người đăng ký

Ngày: 2026-09-19

## 1. Mục tiêu

Mở rộng bot hiện tại từ một chat Telegram cố định thành một danh sách nhỏ các tài khoản cá nhân tự đăng ký. Tài khoản nhắn `/start` sẽ nhận các bài xuất hiện sau thời điểm đăng ký; `/stop` sẽ ngừng nhận. Việc thêm người nhận không làm tăng số lần gọi Gemini cho mỗi bài và không làm người đã nhận thành công bị gửi trùng khi một người khác gặp lỗi.

Thiết kế này mở rộng đặc tả bot gốc và thay thế giới hạn “một người dùng” trong phạm vi MVP. Các quy tắc nguồn tin, tóm tắt, lịch 15 phút, quota Gemini và giới hạn 2.000 bài không thay đổi.

## 2. Hành vi người dùng

- Chỉ xử lý lệnh trong chat riêng.
- `/start`, `/start <payload>` và `/start@<tên_bot>` đăng ký tài khoản.
- `/stop` và `/stop@<tên_bot>` hủy đăng ký.
- Lệnh lặp lại phải idempotent: không tạo bản ghi trùng và vẫn trả lời trạng thái hiện tại.
- Phản hồi lệnh xuất hiện ở lần workflow kế tiếp, thông thường trong 15 phút; bot không chạy long polling liên tục.
- Người mới không nhận lại tin lịch sử. Các bài đã có trước lúc `/start` được đánh dấu đã xử lý cho người đó.
- Người đã `/stop` không nhận các bài mới, nhưng lịch sử giao nhận cũ vẫn được giữ đến khi state bị prune.
- Bất kỳ tài khoản Telegram nào tìm thấy bot đều có thể dùng `/start`. Phiên bản này không có phê duyệt thủ công, mã mời hay giao diện quản trị.

## 3. Các phương án đã cân nhắc

### A. Nhóm Telegram riêng

Bot tiếp tục gửi vào một chat ID duy nhất và người dùng thêm nhiều tài khoản vào cùng nhóm. Đây là cách ít mã nhất nhưng thay đổi trải nghiệm từ chat riêng sang nhóm và không đáp ứng hành vi `/start` đã chọn.

### B. Danh sách đăng ký trong state — chọn

Workflow đọc Telegram updates, lưu người đăng ký và theo dõi giao nhận theo từng chat ID trong `state/seen.json`. Không cần database hoặc dịch vụ trả phí, phù hợp mô hình GitHub Actions hiện tại.

### C. Danh sách cho phép do chủ bot quản lý

An toàn hơn khi bot bị tìm thấy công khai, nhưng cần thêm lệnh quản trị hoặc cấu hình ID thủ công. Không chọn cho phiên bản đầu vì tăng vận hành và không cần thiết cho hai tài khoản cá nhân.

## 4. Mô hình trạng thái

Nâng state lên schema v2:

```json
{
  "schema_version": 2,
  "initialized": true,
  "subscriptions_initialized": true,
  "telegram_update_offset": 12345,
  "subscribers": ["111", "222"],
  "seen": {
    "article_hash": {
      "url": "https://example.test/article",
      "sent_at": "2026-09-19T00:00:00+00:00",
      "delivered_to": ["111", "222"],
      "pending_message": null
    }
  },
  "usage": {
    "quota_day": "2026-09-19",
    "gemini_requests": 10
  }
}
```

Quy tắc:

- `subscribers` là danh sách chat ID riêng tư đang hoạt động, được sắp xếp ổn định khi ghi file.
- `telegram_update_offset` bằng `update_id + 1` lớn nhất đã xử lý để lệnh không bị phát lại.
- `delivered_to` ghi những người đã nhận thành công từng bài.
- `sent_at` tiếp tục là mốc dùng để prune; với bài chưa giao cho ai, nó mang nghĩa thời điểm bot ghi nhận bài lần đầu.
- `pending_message` chỉ giữ bản tin Telegram hoàn chỉnh khi còn ít nhất một người nhận bị lỗi tạm thời. Trường này được xóa ngay khi không còn người nhận đang hoạt động nào thiếu bài, nên state không phình lên bởi toàn bộ nội dung 2.000 bài.
- Mỗi lần gửi thành công cho một người phải lưu state ngay tại máy chạy; workflow vẫn chỉ tạo một commit state ở cuối lượt.
- Khi không có người đăng ký, bài mới vẫn được thêm vào `seen` với `delivered_to` rỗng để người đăng ký sau không nhận backlog.
- Việc prune vẫn giới hạn 2.000 bài hoặc 30 ngày; danh sách người đăng ký và offset không bị prune.

### Chuyển đổi schema v1

- Loader chấp nhận schema v1 hiện tại và chuyển trong bộ nhớ sang v2.
- Ở lần chạy v2 đầu tiên, `TELEGRAM_CHAT_ID` hiện tại được thêm làm người đăng ký ban đầu.
- Tất cả bài đã có trong `seen` được backfill chat ID ban đầu vào `delivered_to`, nên tài khoản hiện tại không nhận lại tin cũ.
- Sau khi state v2 đã được ghi, `subscriptions_initialized=true` ngăn việc tự thêm lại tài khoản gốc nếu tài khoản đó chủ động `/stop`.
- Không sửa hoặc xóa lịch sử bài đã gửi trong quá trình migration.

## 5. Đồng bộ lệnh Telegram

Trước khi tải RSS hoặc retry bản tin đang chờ, workflow thực hiện một lần `getUpdates` với offset đã lưu:

1. Nhận tất cả update mới và luôn tiến offset qua update đã đọc, kể cả update không liên quan.
2. Chỉ nhận message từ chat `private`.
3. Chuẩn hóa command, bỏ payload `/start` và hậu tố `@bot`.
4. Với `/start`, thêm chat ID nếu chưa có và backfill chat ID đó vào `delivered_to` của toàn bộ bài hiện có.
5. Với `/stop`, xóa chat ID khỏi danh sách hoạt động.
6. Gửi thông báo xác nhận ngắn gọn; lỗi gửi xác nhận không được làm mất offset đã xử lý.
7. Ghi state trước khi chuyển sang phần tin tức.

Việc backfill ở `/start` là cơ chế đảm bảo “chỉ nhận tin tương lai”; không cần dựa vào ngày đăng RSS vốn có thể thiếu hoặc sai múi giờ.

## 6. Gửi bài cho nhiều người

Trước khi xử lý RSS mới, pipeline giao lại các `pending_message` đang có cho đúng những subscriber còn thiếu. Nhờ dùng bản tin đã lưu, lượt retry không trích xuất bài và không gọi Gemini lần nữa, kể cả bài đã rời khỏi RSS.

Với mỗi ứng viên RSS:

1. Tính tập người còn thiếu bằng `subscribers - delivered_to`.
2. Nếu bài chưa có trong state và không có subscriber, ghi nhận bài nhưng không trích xuất, không gọi Gemini và không gửi.
3. Nếu không còn người thiếu, bỏ qua bài.
4. Nếu còn người thiếu, trích xuất và tóm tắt đúng một lần, dựng bản tin Telegram rồi lưu vào `pending_message` trước khi gửi.
5. Gửi cùng `pending_message` lần lượt tới từng chat ID còn thiếu.
6. Sau mỗi lần gửi thành công, thêm chat ID vào `delivered_to` và lưu state cục bộ ngay.
7. Nếu một người lỗi tạm thời, giữ họ ở trạng thái chưa giao để lần sau retry; những người đã thành công không bị gửi lại.
8. Khi mọi subscriber đang hoạt động đã nhận hoặc đã hủy đăng ký, xóa `pending_message` nhưng giữ metadata chống trùng.

`sent` tiếp tục đếm số bài có ít nhất một lượt giao thành công. Bổ sung `recipient_deliveries` để đếm tổng số chat nhận thành công và `recipient_failures` để đếm lỗi theo người nhận.

## 7. Xử lý lỗi

- Telegram `401`: token bot không hợp lệ, dừng workflow như hiện tại.
- Telegram `403` khi gửi tới một chat cụ thể: coi người đó đã chặn bot, tự xóa khỏi `subscribers`, ghi warning không chứa chat ID đầy đủ và tiếp tục người khác.
- Telegram `429` hoặc lỗi mạng/5xx: giữ người nhận ở trạng thái chưa giao để retry ở lượt sau.
- Một người nhận lỗi không được ngăn những người khác nhận tin.
- Nếu `getUpdates` lỗi, không thay đổi subscriber/offset nhưng vẫn có thể dùng danh sách đã lưu để gửi tin.
- State hỏng vẫn dừng an toàn; không được tự ghi đè bằng state rỗng.

## 8. Bảo mật và giới hạn

- Chat ID không phải token nhưng vẫn là dữ liệu riêng; chỉ lưu trong repository private và không in đầy đủ vào log.
- Không đưa bot token, Gemini key hoặc chat ID vào mã nguồn, commit message hay output kiểm thử.
- Mỗi bài chỉ gọi Gemini một lần trong luồng xử lý bình thường dù có nhiều người nhận hoặc phải retry ở workflow sau; bản tin đang chờ được cache cho tới khi giao xong.
- Phiên bản này dành cho danh sách cá nhân nhỏ; chưa cam kết quy mô phát sóng lớn, chống spam, phân quyền quản trị hoặc giới hạn số subscriber.
- Bot không gửi lại lịch sử và không hỗ trợ yêu cầu một bài cụ thể bằng command.

## 9. Phân chia trách nhiệm

- `state.py`: schema v2, migration v1, subscriber, offset, `delivered_to` và cache bản tin đang chờ.
- `telegram.py`: đọc command có offset, gửi tới chat ID chỉ định và phân loại lỗi token/người nhận/tạm thời.
- `subscriptions.py`: đồng bộ `/start` và `/stop`, backfill và phản hồi xác nhận.
- `pipeline.py`: tính người còn thiếu, tóm tắt một lần và lưu tiến độ giao theo người nhận.
- `main.py`: chạy migration/sync subscription trước pipeline.
- `README.md`: hướng dẫn tài khoản mới bấm `/start`, độ trễ tối đa và `/stop`.

Không thêm database, web server, webhook, framework Telegram hay dependency runtime mới.

## 10. Kiểm thử và nghiệm thu

Kiểm thử tự động phải chứng minh:

- State v1 migrate sang v2 mà không mất seen/quota và không tạo backlog cho tài khoản gốc.
- `/start` đăng ký idempotent, lưu offset, backfill bài cũ và gửi xác nhận.
- `/stop` hủy idempotent và không bị seed lại ở lượt sau.
- Update không phải chat riêng hoặc không phải command bị bỏ qua nhưng offset vẫn tiến.
- Một bài được tóm tắt một lần và gửi cho hai subscriber.
- Một người lỗi tạm thời không làm người đã nhận thành công bị gửi lại ở lượt sau; lượt retry dùng `pending_message` và không gọi Gemini lại.
- `401` dừng sớm; `403` loại đúng subscriber; `429` giữ trạng thái để retry.
- Không subscriber thì bài được ghi nhận mà không gọi Gemini/Telegram.
- Prune 2.000 bài/30 ngày vẫn giữ nguyên subscriber và update offset.
- Toàn bộ test cũ vẫn đạt và secret scan sạch.

Nghiệm thu thật:

1. Deploy schema v2 và chạy workflow migration; tài khoản hiện tại tiếp tục nhận bình thường, không có backlog.
2. Tài khoản thứ hai nhắn `/start` và nhận thông báo đăng ký trong tối đa một chu kỳ lịch.
3. Một bài mới sau đó xuất hiện trên cả hai tài khoản với cùng nội dung, không gọi Gemini hai lần.
4. Tài khoản thứ hai nhắn `/stop`, nhận xác nhận và không nhận các bài tiếp theo.
5. Nhắn `/start` lần nữa để khôi phục đăng ký nếu người dùng muốn giữ cả hai tài khoản hoạt động sau nghiệm thu.

## 11. Điều kiện hoàn thành

- Không có hai bước cùng `IN_PROGRESS` trong tracker.
- Không gửi backlog cho tài khoản mới hoặc tài khoản gốc sau migration.
- Không gửi trùng cho người đã nhận thành công khi người khác lỗi.
- Lệnh `/start` và `/stop` hoạt động từ chat riêng mà không cần cấu hình secret mới.
- Workflow lịch và chạy tay đều xanh; state commit không xung đột.
- Repository và log không chứa secret hoặc chat ID đầy đủ.
