# Thiết kế chọn báo và chuyên mục theo từng người

Ngày: 2026-09-23

## 1. Mục tiêu

Mỗi người đăng ký tự chọn tối đa 10 chuyên mục từ danh mục do chủ bot quản lý, gồm 5 báo: VnExpress, Dân trí, CafeBiz, GenK và Kenh14. Người dùng chỉ nhận bài thuộc chuyên mục mình chọn. Mỗi bài vẫn chỉ được tóm tắt một lần dù nhiều người nhận.

Thiết kế này mở rộng đặc tả gốc (`2026-09-18-telegram-news-bot-design.md`) và đặc tả nhiều người đăng ký (`2026-09-19-telegram-multi-subscriber-design.md`). Quy tắc độ dài tóm tắt, lịch 15 phút, `pending_message` và giao theo từng chat ID vẫn giữ nguyên, trừ các điểm được thay thế dưới đây.

## 2. Danh mục chuyên mục

Danh mục nằm trong `config/feeds.toml`. Mỗi mục có thêm trường `id` cố định. Số thứ tự hiển thị cho người dùng là vị trí của mục trong file, bắt đầu từ 1.

| Báo | Tiền tố id | Chuyên mục (slug RSS) |
|---|---|---|
| VnExpress | `vne-` | thoi-su, the-gioi, kinh-doanh, bat-dong-san, khoa-hoc-cong-nghe, giai-tri, the-thao, phap-luat, giao-duc, suc-khoe, gia-dinh, du-lich, oto-xe-may, y-kien, tam-su, thu-gian, goc-nhin |
| Dân trí | `dt-` | thoi-su, the-gioi, kinh-doanh, bat-dong-san, cong-nghe, khoa-hoc, giai-tri, the-thao, phap-luat, giao-duc, suc-khoe, doi-song, du-lich, o-to-xe-may, noi-vu, lao-dong-viec-lam, tam-long-nhan-ai, tinh-yeu-gioi-tinh |
| CafeBiz | `cafebiz-` | song, bat-dong-san, quoc-te |
| GenK | `genk-` | ai, mobile, internet, kham-pha, apps-games, do-choi-so, xe, tin-ict |
| Kenh14 | `k14-` | star, cine, musik, sport, doi-song, the-gioi-do-day, hoc-duong, tek-life, beauty-fashion |

Tổng 55 chuyên mục. URL RSS theo mẫu `https://<domain>/rss/<slug>.rss`.

Đã loại:

- Kenh14 Xã hội (~125 bài/ngày), Kenh14 Sức khỏe (~60) và CafeBiz Câu chuyện kinh doanh (~100), vì quá nhiều bài.
- Các chuyên mục bỏ hoang hoặc RSS rỗng: CafeBiz Quản trị, Startup, Công nghệ, Chứng khoán, Chính sách, Bán lẻ, Marketing, Ngân hàng; GenK Máy tính, Tri thức; VnExpress Startup; Dân trí Giá vàng.

Số bài được đo ngày 2026-09-23.

## 3. Hành vi người dùng

### Lệnh

- `/start` và `/chon`: đăng ký nếu chưa có, rồi gửi danh sách chọn.
- `/danhsach`: liệt kê các chuyên mục đang chọn.
- `/stop`: hủy đăng ký và xóa toàn bộ lựa chọn.
- Tin nhắn chỉ gồm chữ số, dấu cách, dấu phẩy và dấu gạch ngang (`-` hoặc `–`), có ít nhất một chữ số, được hiểu là một lựa chọn.
- Chỉ xử lý tin nhắn trong chat riêng. Chuẩn hóa hậu tố `@tên_bot` và payload của `/start` như hiện tại.

### Danh sách chọn

Tin nhắn phải cho biết rõ số nào thuộc báo nào:

```text
Chọn tối đa 10 chuyên mục muốn nhận tin.
Trả lời bằng các số, cách nhau dấu cách hoặc dấu phẩy. Có thể dùng dải số.
Ví dụ: 1 3 20-22

VnExpress
1. Thời sự
2. Thế giới
...

Dân trí
18. Thời sự
...
```

Nếu dài quá giới hạn một tin Telegram, danh sách được chia tại ranh giới giữa hai báo.

### Lựa chọn

- Dải số `a-b` gồm cả hai đầu và yêu cầu `a <= b`. Số trùng chỉ tính một lần.
- Hợp lệ khi có từ 1 đến 10 chuyên mục và mọi số nằm trong khoảng 1–55.
- Lựa chọn hợp lệ **thay thế toàn bộ** lựa chọn cũ. Bot xác nhận bằng danh sách ghi rõ báo, chuyên mục và số, ví dụ `VnExpress – Thời sự (1)`.
- Lựa chọn không hợp lệ: bot trả lời lý do (số ngoài phạm vi, quá 10 chuyên mục, dải số sai) và giữ nguyên lựa chọn cũ.
- Gửi lựa chọn khi chưa đăng ký sẽ tự đăng ký.
- Người đã đăng ký nhưng chưa chọn chuyên mục nào thì không nhận tin.

### Chỉ nhận tin tương lai

Mỗi chuyên mục người dùng chọn được lưu kèm thời điểm chọn. Người dùng chỉ nhận một bài khi:

1. bài thuộc ít nhất một chuyên mục người đó đang chọn, và
2. bot ghi nhận bài lần đầu **tại hoặc sau** thời điểm người đó chọn chuyên mục ấy.

Khi chọn lại, chuyên mục đã có giữ nguyên thời điểm chọn cũ; chuyên mục mới nhận thời điểm hiện tại.

Tin đăng trong lúc người dùng chưa chọn không được gửi bù.

## 4. Chuyển đổi từ schema v2

Khi triển khai lần đầu:

1. Mọi subscriber hiện có chuyển sang trạng thái "chưa chọn", với `menu_pending = true`.
2. Mỗi lượt chạy, bot gửi cho mỗi người có `menu_pending` **một tin thông báo duy nhất**: bot đã có thêm báo, giờ mỗi người tự chọn chuyên mục, và bot tạm dừng gửi tin cho tới khi người đó chọn. Tin thông báo kèm danh sách chọn.
3. Gửi thành công thì đặt `menu_pending = false`. Lỗi tạm thời thì giữ nguyên để lượt sau thử lại. Lỗi `403` thì xóa subscriber.
4. Các bài cũ trong `seen` được giữ để chống trùng. Các bài này không gắn chuyên mục nào, nên không ai đủ điều kiện nhận lại. `pending_message` cũ bị xóa.
5. Không còn hỗ trợ đọc state schema v1, vì state thật đã ở v2.

## 5. Chỉ tải chuyên mục có người chọn

- **Chuyên mục hoạt động** là hợp các chuyên mục mà ít nhất một subscriber đang chọn. Mỗi lượt chạy chỉ tải RSS của các chuyên mục hoạt động.
- **Khởi tạo theo chuyên mục:**
  - Lần đầu một chuyên mục hoạt động được tải thành công, bot chỉ ghi nhận các bài đang có trong RSS mà không gửi.
  - Chuyên mục được thêm vào `bootstrapped_feeds` chỉ khi RSS của nó trả về ít nhất một bài.
- **Khởi tạo lại khi hết người chọn:** khi một chuyên mục không còn ai chọn, nó bị xóa khỏi `bootstrapped_feeds`. Nếu sau này có người chọn lại, chuyên mục được khởi tạo lại, nên các bài đăng trong lúc không ai theo dõi không bị gửi dồn.
- **Bài mới từ lần khởi tạo:** một bài mới được coi là bài khởi tạo nếu mọi chuyên mục chứa nó đều chưa khởi tạo. Bài khởi tạo được ghi với `delivered_to` là toàn bộ người đang đủ điều kiện, nên không ai nhận bài đó.
- **Chế độ `--dry-run`:** tải toàn bộ danh mục vì đây là lệnh kiểm tra. Vẫn không gọi Gemini, không gửi Telegram và không đổi state.

## 6. Mô hình trạng thái v3

```json
{
  "schema_version": 3,
  "telegram_update_offset": 12345,
  "subscribers": {
    "111": {
      "feeds": {"vne-thoi-su": "2026-09-23T02:00:00+00:00"},
      "menu_pending": false
    }
  },
  "bootstrapped_feeds": ["vne-thoi-su"],
  "seen": {
    "article_hash": {
      "url": "https://example.test/article",
      "feeds": ["vne-thoi-su"],
      "first_seen_at": "2026-09-23T02:15:00+00:00",
      "last_seen_at": "2026-09-23T04:15:00+00:00",
      "delivered_to": ["111"],
      "pending_message": null
    }
  },
  "usage": {
    "quota_day": "2026-09-23",
    "requests": {"gemini-3.1-flash-lite": 10}
  }
}
```

Quy tắc:

- `feeds` của một bài là hợp mọi chuyên mục từng chứa bài đó, được bổ sung khi bài xuất hiện ở chuyên mục khác.
- `last_seen_at` được cập nhật mỗi lượt bài còn nằm trong RSS vừa tải.
- **Cách xóa bài cũ khỏi state (thay thế D009):**
  - Giữ bài nếu còn `pending_message`, hoặc nếu `last_seen_at` nằm trong `SEEN_RETENTION_DAYS` ngày gần nhất (mặc định 7).
  - Không bao giờ xóa bài vẫn còn trong RSS đang theo dõi, vì bài đó được cập nhật `last_seen_at` ở mỗi lượt.
  - `MAX_SEEN_ARTICLES` mặc định 10.000, chỉ là trần an toàn. Khi vượt trần, xóa những bài có `last_seen_at` cũ nhất trước.
- Chuyển đổi v2 sang v3: `first_seen_at = last_seen_at = sent_at`, `feeds = []`, `requests = {model chính: gemini_requests}`.

## 7. Luồng mỗi lượt chạy

1. Tải state, đồng bộ lệnh và lựa chọn từ Telegram, gửi thông báo đang chờ (`menu_pending`), rồi lưu state.
2. Retry các `pending_message` cho người đủ điều kiện nhưng chưa nhận.
3. Tính chuyên mục hoạt động, cắt `bootstrapped_feeds` về tập đó, rồi tải RSS các chuyên mục hoạt động.
4. Với mỗi bài trong RSS: nếu đã có trong `seen` thì cập nhật `last_seen_at` và gộp `feeds`. Nếu là bài khởi tạo thì chỉ ghi nhận như mục 5.
5. Với mỗi bài mới thật, xử lý theo thứ tự ngày đăng từ cũ tới mới:
   - Tính người đủ điều kiện nhận.
   - Không ai đủ điều kiện: chỉ ghi nhận bài.
   - Có người đủ điều kiện: trích xuất, tóm tắt một lần, lưu `pending_message`, rồi giao như luồng nhiều người nhận hiện tại.
6. Đánh dấu các chuyên mục vừa tải thành công là đã khởi tạo, rồi lưu state.

Tùy chọn `--limit N` giới hạn số bài mới thật được xử lý trong lượt.

## 8. Chuỗi model Gemini miễn phí

Biến `GEMINI_MODELS` có dạng `model:rpm:rpd`, các model cách nhau dấu phẩy. Giá trị mặc định:

```text
gemini-3.1-flash-lite:10:450,gemini-3.5-flash-lite:10:450,gemma-4-31b-it:3:300
```

Căn cứ theo quota gói miễn phí của project, đọc từ AI Studio ngày 2026-09-23:

| Model | RPM | TPM | RPD |
|---|---|---|---|
| `gemini-3.1-flash-lite` | 15 | 250K | 500 |
| `gemini-3.5-flash-lite` | 15 | 250K | 500 |
| `gemma-4-31b-it` | 30 | 16K | 14.4K |

Gemma chỉ có 16K TPM, nên bot giới hạn nó ở 3 lượt/phút.

Quy tắc:

- Mỗi model có bộ đếm lượt trong ngày theo múi giờ Pacific và bộ giới hạn tốc độ riêng.
- Với mỗi bài, bot thử các model theo thứ tự:
  - Model đã hết `rpd` nội bộ thì bỏ qua, không tốn lượt.
  - Lỗi tạm thời (`429`, `5xx`, timeout) được retry tối đa 2 lần trên cùng model, sau đó chuyển sang model kế tiếp.
  - Lỗi khác chuyển thẳng sang model kế tiếp.
- Mọi model đều thất bại: dùng tóm tắt cục bộ như hiện tại.
- `provider` của `SummaryResult` ghi tên model đã dùng. Thống kê cuối lượt ghi số bài theo từng model.
- Chạy miễn phí với điều kiện project không bật billing. Không dùng nhiều key hoặc nhiều project để cộng dồn quota.
- Biến `GEMINI_MODEL` cũ được thay bằng `GEMINI_MODELS`, cả trong workflow lẫn `.env.example`.

## 9. Nguồn tin mới

### RSS

- CafeBiz, GenK và Kenh14 ghi múi giờ dạng `+07`. Khi `feedparser` không đọc được ngày đăng, bot chuẩn hóa `+07` thành `+0700` rồi đọc bằng `email.utils`.
- Khi gộp bài trùng URL, bài giữ **hợp `feed_ids`** của mọi chuyên mục chứa nó. Tên báo và chuyên mục hiển thị lấy theo chuyên mục đầu tiên trong danh mục.
- Hàm tải trả về danh sách bài kèm tập chuyên mục tải thành công.

### Trích nội dung

Ba báo dùng chung nền tảng:

- Nội dung chính: `.detail-content`, dự phòng `[data-role="content"]`.
- Sapo: `[data-role="sapo"]`, `.knc-sapo`, `.sapo`.

Mỗi báo có một fixture HTML thật để test. Các khối tin liên quan hoặc quảng cáo nằm trong vùng nội dung bị loại bằng selector cụ thể tìm được từ fixture.

## 10. Xử lý lỗi

Các quy tắc cũ vẫn giữ: `401` dừng workflow; `403` xóa subscriber; `429` hoặc lỗi mạng giữ trạng thái để retry.

Bổ sung:

- Lỗi khi gửi danh sách chọn hoặc tin xác nhận không làm mất lựa chọn đã lưu.
- Một RSS lỗi không làm chuyên mục đó bị đánh dấu đã khởi tạo, và không làm dừng các RSS còn lại.
- `feeds.toml` có `id` trùng, `id` rỗng hoặc URL trùng thì dừng với lỗi cấu hình.
- State có `id` chuyên mục không còn trong danh mục: bỏ `id` đó khỏi lựa chọn của người dùng khi tải state.

## 11. Bảo mật

Không thay đổi so với các đặc tả trước:

- Không log chat ID đầy đủ, token hoặc key.
- Chat ID chỉ nằm trong state của repository private.

## 12. Phân chia trách nhiệm

- `config.py`:
  - Đọc `id` của chuyên mục, kiểm tra trùng.
  - Đọc `GEMINI_MODELS`.
- `models.py`: thêm `FeedConfig.id` và `ArticleCandidate.feed_ids`.
- `feeds.py`:
  - Đọc ngày `+07`.
  - Gộp `feed_ids` khi bài trùng URL.
  - Trả về tập chuyên mục tải thành công.
- `extractors.py`: bộ tách nội dung cho CafeBiz, GenK, Kenh14.
- `state.py`: schema v3, chuyển đổi từ v2, cách xóa bài cũ theo `last_seen_at`.
- `catalog.py` (mới):
  - Tạo danh sách chọn và tin xác nhận.
  - Đọc lựa chọn bằng số.
  - Tính người đủ điều kiện nhận một bài.
- `telegram.py`: đọc lệnh mới và tin nhắn lựa chọn.
- `subscriptions.py`: `/start`, `/chon`, `/danhsach`, `/stop`, lựa chọn, thông báo một lần.
- `summarizers.py`: chuỗi model với quota và giới hạn tốc độ riêng cho từng model.
- `pipeline.py`:
  - Chỉ tải chuyên mục hoạt động, khởi tạo theo chuyên mục.
  - Lọc người nhận theo chuyên mục.
  - Cập nhật `last_seen_at`.
- `main.py`, workflow, `README.md`, `.env.example`: ghép nối và hướng dẫn.

Không thêm database, webhook, dịch vụ trả phí hay dependency runtime mới.

## 13. Kiểm thử và nghiệm thu

Kiểm thử tự động:

- **Đọc lựa chọn:**
  - Số, dải số, dấu phẩy, số trùng.
  - Số ngoài phạm vi, quá 10 chuyên mục, dải số ngược, tin nhắn không phải lựa chọn.
- **Nội dung tin:** danh sách chọn và tin xác nhận ghi rõ tên báo, chuyên mục và số.
- **Chuyển đổi v2 sang v3:**
  - Mọi subscriber có `menu_pending`.
  - Thông báo chỉ gửi một lần; lỗi tạm thời thì lượt sau gửi lại.
  - Không gửi lại bài cũ.
- **Lệnh:**
  - `/chon`, `/danhsach`, `/stop` và lựa chọn thay thế toàn bộ.
  - Chuyên mục chọn lại giữ nguyên thời điểm chọn cũ.
- **Chỉ tải chuyên mục hoạt động:**
  - Chuyên mục mới khởi tạo mà không gửi bài cũ.
  - Chuyên mục hết người chọn rồi được chọn lại thì khởi tạo lại.
  - RSS lỗi thì chuyên mục không được đánh dấu đã khởi tạo.
- **Lọc người nhận:**
  - Người nhận đúng bài thuộc chuyên mục đã chọn.
  - Bài ở hai chuyên mục chỉ tóm tắt một lần và gửi cho người chọn một trong hai.
- **Xóa bài cũ:**
  - Bài còn trong RSS không bị xóa khỏi state.
  - Bài vắng khỏi RSS quá 7 ngày thì bị xóa.
- **Chuỗi model:**
  - Chuyển sang model kế tiếp khi hết quota hoặc lỗi.
  - Đếm quota riêng cho từng model.
  - Mọi model thất bại thì dùng tóm tắt cục bộ.
- **RSS và trích nội dung:**
  - Đọc được ngày `+07`.
  - Fixture CafeBiz, GenK, Kenh14 trích đúng nội dung chính.
- **Test cũ:** toàn bộ test cũ vẫn đạt, sau khi cập nhật theo hành vi mới.

Nghiệm thu thật:

1. Deploy, chạy workflow. Mỗi subscriber hiện có nhận đúng một tin thông báo kèm danh sách, và không nhận bài nào.
2. Một tài khoản trả lời số. Lượt sau tài khoản đó nhận xác nhận đúng tên báo và chuyên mục.
3. Các lượt tiếp theo: tài khoản chỉ nhận bài mới của chuyên mục đã chọn, không nhận bài cũ.
4. `/danhsach` và `/stop` hoạt động.
5. Workflow xanh, state commit không xung đột, log không chứa secret hay chat ID đầy đủ.

## 14. Giới hạn chấp nhận

- Bot phản hồi lệnh và lựa chọn chậm tới 15 phút.
- Số thứ tự hiển thị phụ thuộc thứ tự trong `feeds.toml`. Nếu chủ bot đổi thứ tự, người dùng cần xem lại danh sách mới.
- Bài xuất hiện trước khi người dùng chọn chuyên mục không được gửi, kể cả khi bài đó sau này được thêm vào chuyên mục khác.
- Chất lượng tóm tắt của Gemma có thể khác hai model Flash Lite.
