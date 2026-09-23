# Thiết kế bot Telegram tóm tắt tin tức

Ngày: 2026-09-18

## 1. Mục tiêu

Xây dựng một bot cá nhân chạy với chi phí 0 đồng, định kỳ phát hiện bài mới từ bảy chuyên mục của VnExpress và Dân trí, lấy nội dung bài, tóm tắt bằng Gemini và gửi vào một cuộc trò chuyện Telegram riêng.

MVP ưu tiên độ tin cậy, không gửi trùng và có thể vận hành bằng GitHub Actions mà không cần máy tính cá nhân bật liên tục.

## 2. Phạm vi đã chốt

### Nguồn VnExpress

- Thời sự: `https://vnexpress.net/rss/thoi-su.rss`
- Thế giới: `https://vnexpress.net/rss/the-gioi.rss`
- Kinh doanh: `https://vnexpress.net/rss/kinh-doanh.rss`
- Bất động sản: `https://vnexpress.net/rss/bat-dong-san.rss`

### Nguồn Dân trí

- Thời sự: `https://dantri.com.vn/rss/thoi-su.rss`
- Thế giới: `https://dantri.com.vn/rss/the-gioi.rss`
- Bất động sản: `https://dantri.com.vn/rss/bat-dong-san.rss`

### Đầu ra

Mỗi bài được gửi riêng, gồm:

1. Tên báo và chuyên mục.
2. Tiêu đề bài viết.
3. Nội dung tóm tắt bằng tiếng Việt.
4. Liên kết bài gốc.

Độ dài tóm tắt được tính từ phần nội dung bài đã làm sạch:

- Mục tiêu bằng 20–25% số từ của bài gốc.
- Giới hạn cứng là 300 từ.
- Ví dụ: bài 1.000 từ được tóm tắt còn 200–250 từ; bài 2.000 từ hoặc dài hơn không vượt quá 300 từ.
- Nếu tin nhắn vượt giới hạn Telegram, bot chia tại ranh giới đoạn văn và đánh số phần.

Bot không được bổ sung dữ kiện không có trong bài và luôn dẫn liên kết nguồn.

## 3. Các phương án vận hành

### A. GitHub Actions và trạng thái trong repository — chọn cho MVP

- Chạy theo lịch 15 phút một lần và cho phép chạy thủ công.
- Miễn phí khi dùng repository công khai với runner tiêu chuẩn.
- Token được giữ trong GitHub Secrets; mã nguồn công khai nhưng bí mật không công khai.
- File trạng thái nhỏ được cập nhật chỉ khi có bài mới được gửi thành công.

Hạn chế: lịch có thể chạy chậm, repository công khai có thể bị tắt lịch sau thời gian dài không có hoạt động, và lịch sử Git có thêm commit trạng thái.

### B. Windows Task Scheduler trên máy cá nhân

Không cần repository công khai và có trạng thái cục bộ đơn giản, nhưng bot chỉ chạy khi máy tính và mạng đang hoạt động.

### C. Máy ảo Always Free

Ổn định hơn và có cơ sở dữ liệu cục bộ, nhưng thường cần tài khoản thanh toán/thẻ và cấu hình vận hành phức tạp hơn. Không chọn vì mục tiêu 0 đồng và đơn giản.

## 4. Kiến trúc

Các khối được tách theo trách nhiệm:

- `config`: đọc cấu hình nguồn, model, giới hạn tốc độ và biến môi trường.
- `feeds`: tải RSS, chuẩn hóa URL và tạo danh sách ứng viên.
- `extractors`: lấy HTML, tách nội dung chính theo từng báo và có bộ trích xuất chung dự phòng.
- `summarizers`: gọi Gemini hoặc dùng thuật toán trích xuất câu cục bộ khi Gemini không khả dụng.
- `telegram`: định dạng, chia tin nhắn và gửi qua Telegram Bot API.
- `state`: lưu bài đã gửi, số lượt Gemini trong ngày và dữ liệu khởi tạo.
- `pipeline`: điều phối toàn bộ luồng, retry và quyết định khi nào đánh dấu đã gửi.

Model chính là `gemini-3.1-flash-lite`. Theo quota của project đã kiểm tra, model có 15 RPM, 250.000 TPM và 500 RPD.

Giới hạn nội bộ:

- Tối đa 10 request Gemini/phút.
- Tối đa 450 request Gemini/ngày để giữ 50 request dự phòng.
- Một bài dùng một request chính; retry tối đa hai lần cho lỗi tạm thời nhưng không retry lỗi cấu hình/xác thực.
- Ngày quota được tính theo múi giờ Pacific để khớp cách Gemini đặt lại RPD.

## 5. Luồng xử lý

1. Workflow đọc secrets và kiểm tra cấu hình bắt buộc.
2. Tải trạng thái gần nhất từ `state/seen.json`.
3. Tải song song bảy RSS với timeout và retry ngắn.
4. Chuẩn hóa URL, bỏ tham số theo dõi, gộp các bài trùng giữa nhiều chuyên mục và sắp xếp bài từ cũ đến mới.
5. Ở lần chạy đầu tiên, ghi nhận các bài đang có trong RSS nhưng không gửi, tránh dội toàn bộ tin cũ vào Telegram.
6. Với mỗi bài mới, tải HTML bằng User-Agent rõ ràng và tốc độ lịch sự.
7. Trích tiêu đề, ngày đăng và nội dung chính; loại menu, quảng cáo, chú thích ảnh lặp và các khối “tin liên quan”.
8. Đếm từ và tính độ dài mục tiêu theo quy tắc tại mục 2.
9. Gọi Gemini với yêu cầu chỉ dựa trên nội dung đã cung cấp, không suy diễn và trả về văn bản thuần.
10. Nếu Gemini gặp lỗi tạm thời, chờ tăng dần rồi thử lại. Nếu hết quota hoặc vẫn lỗi, dùng bộ tóm tắt cục bộ.
11. Gửi Telegram. Chỉ sau khi Telegram xác nhận thành công mới đánh dấu bài là đã gửi.
12. Ghi file trạng thái và commit lại repository nếu trạng thái thay đổi.

## 6. Trạng thái và chống gửi trùng

Khóa bài là hash của URL chuẩn hóa. Trạng thái giữ tối đa 2.000 bài gần nhất hoặc 30 ngày, tùy điều kiện nào loại bỏ trước.

`state/seen.json` chứa:

- Phiên bản schema.
- Cờ đã khởi tạo.
- Danh sách hash URL, URL gốc và thời điểm gửi.
- Bộ đếm Gemini theo ngày quota.

Workflow dùng `permissions: contents: write` và một nhóm `concurrency` duy nhất để tránh hai lượt chạy cùng sửa trạng thái. Commit trạng thái không kích hoạt thêm workflow vì workflow chỉ chạy theo lịch hoặc thủ công.

## 7. Xử lý lỗi

- Một RSS lỗi không làm dừng các RSS còn lại.
- Không ghi đè trạng thái bằng dữ liệu rỗng khi mạng hoặc parser lỗi.
- Không trích được nội dung: gửi tiêu đề, mô tả RSS và link kèm nhãn “không lấy được toàn văn”; không gọi Gemini với dữ liệu quá ngắn.
- Gemini trả `429`: tôn trọng thời gian chờ, retry có giới hạn, sau đó dùng tóm tắt cục bộ.
- Telegram lỗi: không đánh dấu đã gửi để lượt sau thử lại.
- Telegram trả `401/403`: dừng sớm và báo lỗi cấu hình trong log, tránh lặp lại hàng trăm request.
- Một bài lỗi không chặn các bài khác; cuối lượt chạy in thống kê thành công, dự phòng, bỏ qua và thất bại.

## 8. Bảo mật và sử dụng nội dung

Các bí mật bắt buộc:

- `GEMINI_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Secrets không xuất hiện trong file cấu hình, log, exception hoặc URL được in ra. Repository có `.env.example` chỉ chứa tên biến giả.

Bot dùng RSS chính thức, truy cập toàn văn với tần suất thấp, chỉ gửi bản tóm tắt vào chat cá nhân và luôn gắn nguồn. Hệ thống không lưu toàn văn bài báo sau khi xử lý. Việc truy cập phải tôn trọng `robots.txt`, điều khoản của nguồn và yêu cầu dừng cung cấp nếu tòa soạn yêu cầu.

## 9. Cấu trúc dự kiến

```text
.
├── .github/workflows/news-bot.yml
├── config/feeds.toml
├── docs/superpowers/specs/
├── src/newsbot/
│   ├── config.py
│   ├── feeds.py
│   ├── extractors.py
│   ├── summarizers.py
│   ├── telegram.py
│   ├── state.py
│   ├── pipeline.py
│   └── main.py
├── state/seen.json
├── tests/
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

Python 3.12 được dùng trên GitHub Actions. Các thư viện dự kiến gồm `httpx`, `feedparser`, `beautifulsoup4`, `lxml` và `google-genai`. Telegram được gọi trực tiếp bằng HTTP để không cần thêm một framework bot nặng.

## 10. Kiểm thử và nghiệm thu

### Kiểm thử tự động

- Parse fixture RSS của cả hai nguồn.
- Chuẩn hóa URL và loại bài trùng giữa chuyên mục.
- Trích nội dung từ fixture HTML của VnExpress và Dân trí.
- Tính số từ mục tiêu và giới hạn 300 từ.
- Khởi tạo không gửi backlog.
- Chỉ đánh dấu seen khi Telegram thành công và chỉ giữ tối đa 2.000 bài gần nhất.
- Retry Gemini/Telegram đúng loại lỗi và không vượt giới hạn.
- Chuyển sang tóm tắt cục bộ khi quota hết.
- Chia tin Telegram không vượt giới hạn ký tự.
- Mock toàn bộ API ngoài trong test; test không tiêu hao quota và không gửi tin thật.

### Kiểm tra tích hợp

- Chế độ `--dry-run` đọc RSS và trích một bài nhưng không gọi Gemini/Telegram và không đổi trạng thái.
- Chế độ `--limit 1` dùng để gửi thử đúng một bài sau khi cấu hình secrets.
- Workflow chạy thủ công trước khi bật lịch.

### Điều kiện hoàn thành MVP

- Lần chạy đầu không gửi bài cũ.
- Một bài mới hợp lệ tạo đúng một tin Telegram và không gửi lại ở lần chạy sau.
- Tóm tắt nằm trong khoảng mục tiêu khi chưa chạm trần, và không bao giờ quá 300 từ.
- Mất một nguồn hoặc Gemini không làm toàn bộ lượt chạy thất bại.
- Không có secret trong repository hoặc log.
- Test tự động và dry-run đều đạt trên GitHub Actions.

## 11. Hạn chế được chấp nhận

- Tin có thể đến chậm hơn 15 phút khi GitHub Actions quá tải.
- Bài không xuất hiện trong RSS có thể bị bỏ sót.
- Khi trang báo đổi HTML, chất lượng trích xuất có thể giảm cho tới khi selector được cập nhật.
- Tóm tắt cục bộ dự phòng kém tự nhiên hơn Gemini.
- Bài đã gửi rồi được tòa soạn sửa nội dung sẽ không tự gửi lại trong MVP.
- Quota Gemini có thể thay đổi; cấu hình model và giới hạn phải thay được mà không sửa pipeline.
- Mã nguồn trong repository công khai; chỉ secrets và chat Telegram là riêng tư.

## 12. Ngoài phạm vi MVP

- Nhiều người dùng, nhóm hoặc kênh Telegram.
- Giao diện quản trị.
- Lọc từ khóa, lịch gửi bản tin tổng hợp hoặc cá nhân hóa chủ đề.
- Lưu toàn văn, tìm kiếm lịch sử hoặc cơ sở dữ liệu ngoài.
- Gửi ảnh/video và phân tích nội dung đa phương tiện.
