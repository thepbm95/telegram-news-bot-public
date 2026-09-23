# Telegram News Bot

Bot cá nhân theo dõi RSS, lấy nội dung bài mới, tóm tắt và gửi tới các tài khoản Telegram đã đăng ký trong chat riêng. Mỗi người tự chọn tối đa 10 chuyên mục muốn nhận. Dự án ưu tiên vận hành miễn phí bằng Gemini Free Tier và GitHub Actions.

## Nguồn tin và quy tắc tóm tắt

Danh mục gồm 55 chuyên mục của 5 báo, khai báo trong `config/feeds.toml`:

- VnExpress (17), Dân trí (18), CafeBiz (3), GenK (8), Kenh14 (9).

Số thứ tự người dùng thấy trong Telegram là vị trí của chuyên mục trong file. Khi thêm chuyên mục mới, hãy thêm vào cuối nhóm báo và không đổi `id` đã dùng, để số cũ của người dùng không bị lệch.

Tóm tắt dài khoảng 20–25% số từ của bài gốc và không bao giờ quá 300 từ. Mỗi tin gồm nguồn, chuyên mục, tiêu đề, bản tóm tắt và link bài gốc. Mỗi bài chỉ được tóm tắt một lần rồi dùng chung cho mọi người chọn chuyên mục đó, kể cả khi bài nằm ở nhiều chuyên mục. State theo dõi riêng tài khoản nào đã nhận và chỉ đánh dấu `delivered_to` sau khi Telegram xác nhận thành công. Bài được giữ trong state khi còn nằm trong RSS đang theo dõi, và bị xóa sau 7 ngày vắng khỏi RSS.

## Cách bot hoạt động

GitHub Actions chạy 10 phút một lần (phút 2, 12, 22, 32, 42, 52; lịch GitHub có thể trễ vài phút khi đông). Lượt theo lịch bỏ qua bước chạy test cho nhanh; lượt chạy tay vẫn chạy test. Mỗi lần chạy, bot:

1. Xử lý lệnh và lựa chọn trong Telegram.
2. Retry các bản tin đang chờ đúng người nhận.
3. Chỉ tải RSS của những chuyên mục có ít nhất một người chọn.
4. Trích toàn văn và tóm tắt bài mới.

Thứ tự tóm tắt: `gemini-3.1-flash-lite`, rồi `gemini-3.5-flash-lite`, rồi `gemma-4-31b-it`, cuối cùng là bộ tóm tắt cục bộ. Mỗi model có quota riêng trong gói miễn phí. Nếu không trích được toàn văn, bot gửi mô tả RSS kèm cảnh báo.

Lần đầu một chuyên mục được chọn, bot chỉ ghi nhận các bài đang có trong RSS mà không gửi, để tránh bắn hàng loạt tin cũ. Chuyên mục không còn ai chọn sẽ được khởi tạo lại khi có người chọn lại. Khi chạy tay với `limit=N`, bot chỉ xử lý N bài mới cũ nhất; phần còn lại để lượt sau.

## 1. Tạo Gemini API key miễn phí

1. Mở [Google AI Studio – API keys](https://aistudio.google.com/app/apikey), đăng nhập và tạo key cho một project Free Tier.
2. Mở trang **Rate limits** trong AI Studio và kiểm tra các dòng `Gemini 3.1 Flash Lite`, `Gemini 3.5 Flash Lite` và `Gemma 4 31B`. Quota có thể thay đổi theo project. Cấu hình mặc định tự giới hạn ở 10 RPM/450 request mỗi ngày cho hai model Flash Lite, và 3 RPM/300 request mỗi ngày cho Gemma (Gemma chỉ có 16K token/phút). **Không bật billing** cho project: khi chưa bật billing, hết quota chỉ trả lỗi `429` chứ không phát sinh phí.
3. Lưu key vào trình quản lý mật khẩu. Không dán key vào mã nguồn, issue, log hoặc chat.

Tài liệu chính thức: [API keys](https://ai.google.dev/gemini-api/docs/api-key), [rate limits](https://ai.google.dev/gemini-api/docs/rate-limits). Nếu project của bạn không có model này hoặc quota bằng 0, xem mục “Đổi model” bên dưới.

## 2. Tạo Telegram bot và cấu hình tự động

1. Trong Telegram, mở đúng tài khoản [@BotFather](https://t.me/BotFather), gửi `/newbot` và làm theo hướng dẫn.
2. Thu hồi mọi token từng gửi trong chat, lấy token mới, rồi mở bot vừa tạo và gửi `/start`. Bot không thể tự mở cuộc trò chuyện với bạn trước bước này.
3. Trên máy có Python 3.12, mở PowerShell tại thư mục dự án và cài package:

```powershell
python -m pip install -e ".[dev]"
```

4. Chạy trình setup một bước:

```powershell
news-bot --setup-github
```

Lệnh sẽ hỏi kín Telegram token mới, in username để bạn xác nhận đúng bot, tự chờ `/start`, tự lấy chat ID, hỏi kín Gemini key mới và gắn đủ ba GitHub Secrets. Giá trị bí mật được chuyển qua stdin, không nằm trong command line hoặc log. Không gửi bot token, Gemini key hoặc chat ID vào cuộc trò chuyện hỗ trợ. Xem thêm [hướng dẫn bot chính thức của Telegram](https://core.telegram.org/bots/tutorial).

## 3. Hai repository: code public, state private

Bot dùng hai repository:

- **Repo code (public)**: chứa code và workflow. Repo public được chạy GitHub Actions không giới hạn phút, nên bot chạy 10 phút một lần mà vẫn miễn phí.
- **Repo state (private)**: chỉ dùng để lưu `state/seen.json`. File này chứa chat ID người đăng ký và lịch sử giao nhận nên không được để public.

Mỗi lượt, workflow tải state từ repo private bằng một deploy key chỉ có quyền trên đúng repo đó, chạy bot, rồi đẩy state đã cập nhật về repo private. Workflow `keepalive.yml` tạo một commit rỗng mỗi tháng, vì GitHub tự tắt lịch của repo public sau 60 ngày không có hoạt động.

Cấu hình trong repo code, tại **Settings → Secrets and variables → Actions**:

| Loại | Tên | Giá trị |
|---|---|---|
| Secret | `GEMINI_API_KEY` | Key lấy từ Google AI Studio |
| Secret | `TELEGRAM_BOT_TOKEN` | Token do BotFather cấp |
| Secret | `STATE_DEPLOY_KEY` | Khóa SSH riêng của deploy key có quyền ghi vào repo state |
| Variable | `STATE_REPOSITORY` | Tên repo state, dạng `chủ/tên-repo` |
| Variable | `STATE_BRANCH` | Nhánh chứa `state/seen.json` |

`TELEGRAM_CHAT_ID` không còn bắt buộc: người dùng tự đăng ký bằng `/start`. Không tạo file chứa các giá trị bí mật và không dán chúng vào chat. Hướng dẫn chính thức: [Using secrets in GitHub Actions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).

Nếu quên key: Gemini key xem lại tại [Google AI Studio – API keys](https://aistudio.google.com/app/apikey); Telegram token xem lại trong [@BotFather](https://t.me/BotFather) → `/mybots` → chọn bot → **API Token** (xem không làm đổi token; chỉ bấm **Revoke** khi muốn thu hồi).

## 4. Chạy thử và bật vận hành

Vào tab **Actions → Telegram news bot → Run workflow**.

1. Chạy thử với `dry_run=true`, `limit=1`. Kết quả đúng là workflow xanh, log có `sent: 0`, `skipped: 1`, và Telegram không nhận tin.
2. Chạy thật với `dry_run=false`, `limit=1`. Kết quả đúng là workflow xanh và nếu có bài mới thì repo state nhận commit `chore: update delivery state`.
3. Kiểm tra tiêu đề, nội dung tóm tắt và link. Nếu ổn, không cần thao tác thêm: lịch 10 phút đã được bật sẵn.

Có thể chạy thử cục bộ không cần secret:

```powershell
news-bot --dry-run --limit 1
```

## Đăng ký và chọn chuyên mục

Bot trả lời ở lượt workflow kế tiếp, thường trong khoảng 10 phút.

1. Mở bot và gửi `/start`. Bot gửi danh sách chuyên mục đánh số, nhóm theo từng báo.
2. Trả lời bằng các số muốn nhận, cách nhau dấu cách hoặc dấu phẩy. Có thể dùng dải số, ví dụ `1 3 18-20`. Mỗi người chọn tối đa 10 chuyên mục.
3. Bot xác nhận danh sách đã lưu, ghi rõ tên báo, chuyên mục và số. Lựa chọn mới luôn thay thế toàn bộ lựa chọn cũ; lựa chọn sai thì bot giải thích lỗi và giữ nguyên lựa chọn cũ.
4. Người dùng chỉ nhận bài mà bot thấy lần đầu sau lúc chọn chuyên mục đó; bot không phát lại lịch sử. Người chưa chọn chuyên mục nào thì không nhận tin.

Các lệnh (gõ `/` trong Telegram để thấy menu; bot tự cập nhật menu này mỗi lượt chạy bằng `setMyCommands`):

- `/chon`: xem lại toàn bộ danh sách, ✅ đánh dấu mục đang nhận; số gửi lên sẽ thay toàn bộ lựa chọn.
- `/them`: xem toàn bộ danh sách có tên báo và chuyên mục, rồi trả lời số muốn thêm. Các mục cũ được giữ nguyên, tổng không quá 10. Gõ nhanh: `/them 5 20-22`.
- `/bo`: xem các mục đang nhận kèm số, rồi trả lời số muốn bỏ. Gõ nhanh: `/bo 4`.
- `/danhsach`: xem các chuyên mục đang nhận.
- `/stop`: dừng nhận tin và xóa lựa chọn. Gửi `/start` để đăng ký lại.

Sau `/them` hoặc `/bo`, chỉ tin trả lời bằng số ngay sau đó được hiểu là thêm hoặc bỏ; các lần gửi số tiếp theo lại là chọn lại toàn bộ. Chuyên mục vừa thêm chỉ nhận bài mới kể từ lúc thêm.

Khi nâng cấp từ bản cũ, mọi người đang nhận tin được chuyển sang trạng thái chưa chọn và nhận đúng một tin thông báo kèm danh sách. Tin trong khoảng thời gian chưa chọn không được gửi bù. Bất kỳ ai tìm thấy username của bot đều có thể gửi `/start`; phiên bản này chưa có allowlist hoặc phê duyệt thủ công.

## Vận hành và xử lý lỗi

- Xem log: vào tab **Actions**, chọn lần chạy đỏ, mở job `deliver` và bước lỗi. Không in hoặc sao chép secret vào issue/chat.
- Retry: dùng **Re-run failed jobs**. Bản tin đã tóm tắt được cache khi còn người chưa nhận; lần sau bot chỉ thử lại đúng người đó và không gọi Gemini lại.
- Telegram 401: bot token không hợp lệ, workflow dừng để tránh lặp hàng loạt request.
- Telegram 403 ở một chat: tài khoản đó có thể đã chặn bot; bot gỡ riêng tài khoản đó và vẫn tiếp tục gửi cho người khác.
- Gemini 429/hết quota: bot chuyển sang model kế tiếp trong chuỗi, cuối cùng là tóm tắt cục bộ. Quota request/ngày của từng model reset theo giờ Thái Bình Dương. Thống kê cuối mỗi lượt (`summaries_by_provider`) cho biết mỗi model đã tóm tắt bao nhiêu bài.
- RSS/trang báo đổi HTML: bot có thể gửi mô tả RSS thay cho toàn văn; cần cập nhật selector nếu lỗi kéo dài.
- Đổi model: vào **Settings → Secrets and variables → Actions → Variables**, tạo hoặc sửa `GEMINI_MODELS` theo dạng `model:rpm:rpd`, các model cách nhau dấu phẩy và theo thứ tự ưu tiên. Mỗi model phải tồn tại trong project và có quota. Nếu không có variable, bot dùng `gemini-3.1-flash-lite:10:450,gemini-3.5-flash-lite:10:450,gemma-4-31b-it:3:300`. Không dùng nhiều key hoặc nhiều project để cộng dồn quota miễn phí.
- Dừng khẩn cấp: vào **Actions → Telegram news bot → … → Disable workflow**. GitHub cho phép bật lại sau đó; xem [Managing workflow runs](https://docs.github.com/en/actions/how-tos/manage-workflow-runs).
- Đổi lịch: sửa biểu thức `cron` trong `.github/workflows/news-bot.yml`. Lịch GitHub Actions có thể chạy trễ vào giờ cao điểm.

## Giới hạn và sử dụng nội dung

- Free Tier và quota không được bảo đảm, có thể thay đổi; hãy kiểm tra trang Rate limits của chính project.
- Chuỗi model mặc định cho khoảng 1.200 request/ngày, còn retry cũng tiêu quota. Số bài phải tóm tắt bằng tổng số bài của mọi chuyên mục có người chọn, không nhân theo số người. Bot luôn ưu tiên không vượt giới hạn đã cấu hình.
- Tóm tắt AI hoặc cục bộ có thể bỏ sót/sai sắc thái; luôn dùng link bài gốc cho quyết định quan trọng.
- RSS có thể trễ, thiếu bài hoặc lặp URL; GitHub Actions cũng không bảo đảm chạy đúng từng phút.
- Selector phụ thuộc HTML của VnExpress, Dân trí và nền tảng chung của CafeBiz/GenK/Kenh14; có thể hỏng khi trang đổi giao diện.
- Dùng cho chat cá nhân, không vượt paywall, không tái xuất bản toàn văn. Người vận hành chịu trách nhiệm tuân thủ điều khoản của nguồn báo, Telegram, Google và GitHub.

## Kiểm tra dành cho người phát triển

```powershell
python -m compileall -q src
python -m pytest -q
news-bot --help
news-bot --dry-run --limit 1
```

Theo dõi tiến độ, lỗi đã gặp và bằng chứng kiểm thử trong `PROJECT_STATUS.md`.
