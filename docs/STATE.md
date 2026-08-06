# STATE — bàn giao trạng thái

Đọc file này đầu mỗi phiên. Tối đa một trang. Chi tiết ở `DECISIONS.md` và
`VALIDATION_REPORT.md`. Cập nhật ở cuối mỗi phase, ghi đè, không phụ lục thêm.

## Đang ở đâu

- Phase 1–8 xong. Phase 9 (broker) chuyển từ Bybit sang **Binance qua
  ccxt** (2026-08-06, xem `docs/DECISIONS.md`). 193 passed / 4 skipped.
- Forward test chạy từ 2026-08-06, cấu hình đóng băng, launchd hằng ngày.
  Mốc đánh giá: 2026-11-06 / 2027-02-06 / 2027-08-06. Không đụng tới trong
  phiên này.
- Cổng: `CLAUDE.md` #12 — xây tầng thực thi ở **testnet** được, **mainnet**
  bị chặn tới 2027-08-06.

## Testnet đang bị chặn — KHÔNG PHẢI lỗi Binance, KHÔNG PHẢI lỗi code

Chặn ở tầng tài khoản GitHub. Đã xác nhận trước đó bằng gọi thật:
`exchange_reachable` OK (mạng/API Binance testnet sống bình thường,
155ms), vấn đề nằm ngoài cả hai lớp (sàn, code). Không debug thêm ở
hướng "sửa CCXTClient"/"sửa health_check" cho việc này — không phải chỗ
hỏng. Mọi việc cần key Binance testnet thật (nghiệm thu submit_order/
cancel_order/idempotency qua mạng, mục 1 cũ trong danh sách treo) **tạm
dừng**, không phải ưu tiên hiện tại — xem "Việc còn treo" bên dưới, thứ
tự đã đổi để tránh nó.

## Việc còn treo, theo thứ tự ưu tiên (cập nhật — tránh nhánh bị chặn testnet)

1. ~~`tests/test_forward_golden.py`~~ — **ĐÃ CÓ, đã xanh.** Được giao lại
   là "chưa có, quan trọng nhất" nhưng kiểm tra trực tiếp (file tồn tại,
   `git log` cho thấy đã commit ở `479495d`, có trên `origin/main`, chạy
   `pytest tests/test_forward_golden.py` PASS) không khớp — đã báo lại
   thay vì âm thầm build lại. Không có việc gì để làm ở mục này; đã nằm
   trong danh sách 5 test bắt buộc ở CLAUDE.md #15 từ trước phiên này.
2. **4 test skip trong `test_hmm.py`** — ưu tiên thật hiện tại.
   `hmm_engine.py` là thứ forward test phụ thuộc trực tiếp; `test_look_ahead`
   xanh chỉ chứng minh không có look-ahead bias, KHÔNG phủ BIC selection,
   gán nhãn regime, hay bộ lọc ổn định (`stability_bars`/flicker). Độ phủ
   thật thấp hơn con số "193 passed" gợi ý.
3. **Lỗi mypy trong `tests/test_forward_logger.py`** (8 lỗi, pre-existing,
   không ai xử lý qua nhiều phiên) — nợ kỹ thuật tồn đọng.
4. **`prompts/phase-10-main-loop.md`** — xây được và test được bằng
   `--dry-run`, KHÔNG cần sàn thật. Chỉ phần nghiệm thu đặt lệnh thật mới
   cần testnet (bị chặn, xem trên) — không chặn việc xây main loop.
5. Điền `EXCHANGE_API_KEY`/`EXCHANGE_API_SECRET` + nghiệm thu `CCXTClient`
   qua mạng thật — **TẠM DỪNG**, chờ testnet hết bị chặn ở tầng tài khoản
   GitHub. Không phải việc kế tiếp.
6. Copy `phase-12b-harness-engineering.md` và `phase-12c-shadow-deploy.md`
   vào `prompts/` (đã soạn, chưa có trong repo).

## Đổi sàn Bybit -> Binance (ccxt) — 2026-08-06 (tóm tắt, chi tiết ở DECISIONS.md)

Bybit chặn theo khu vực (`retCode 10024`), không kết nối được cả testnet
lẫn mainnet kể cả public endpoint. Thay bằng `broker/ccxt_client.py::CCXTClient`,
`broker/bybit_client.py` giữ lại (deprecated, không xoá — bằng chứng
quyết định). WebSocket -> REST polling toàn bộ (`ExchangeClient` bỏ
`subscribe_klines`/`subscribe_executions`; `PositionTracker.on_execution()`
bỏ, thêm `poll()` — Phase 10 main loop PHẢI gọi `position_tracker.poll()`
mỗi vòng, đây là đường cập nhật vị thế duy nhất bây giờ).
`broker/order_executor.py` — 0 thay đổi logic (ABC không rò rỉ chi tiết
sàn). `ops/health_check.py` đọc `exchange.name` từ config, env var
`EXCHANGE_API_KEY/SECRET/TESTNET` — fallback đọc tên `BYBIT_*` cũ đã BỎ
HẲN (không còn đọc, kể cả làm dự phòng) — thiếu biến nào thì
`exchange_authenticated` FAIL và nêu đúng tên biến đó.

## Việc tiếp theo

test_hmm.py (4 skip) -> mypy test_forward_logger.py -> Phase 10 main loop
(`--dry-run`, không cần testnet) -> [testnet hết bị chặn] -> nghiệm thu
CCXTClient qua mạng thật.

## Quy tắc đã học, không lặp lại

- Mọi số đo thị trường lấy từ **testnet không dùng để hiệu chỉnh tham số**.
  Thanh khoản testnet là nhân tạo.
- Không bao giờ log giá trị key/secret, kể cả một phần.
- Không bao giờ hai tiến trình cùng khả năng đặt lệnh trên một tài khoản.
- Khả năng truy cập theo khu vực/tài khoản có thể chặn bất kỳ lớp nào
  (sàn — Bybit; hạ tầng — GitHub) bất kỳ lúc nào, không cảnh báo trước.
  Khi bị chặn, xác định ĐÚNG lớp bị chặn trước khi debug (đừng sửa code
  để "chữa" một chặn ở tầng tài khoản) — chuyển sang việc không phụ thuộc
  lớp đó, quay lại khi hết chặn, không đoán/không chờ không việc gì.
- Trước khi "xây lại" một file bị báo là thiếu/chưa có: kiểm tra thật
  (file tồn tại? đã commit? có trên remote? test pass?) rồi mới tin —
  CLAUDE.md #16 (đột biến trước khi tin test) áp dụng tương tự cho việc
  tin một báo cáo trạng thái: xác minh trước khi hành động, không phải
  sau.
