# STATE — bàn giao trạng thái

Đọc file này đầu mỗi phiên. Tối đa một trang. Chi tiết ở `DECISIONS.md` và
`VALIDATION_REPORT.md`. Cập nhật ở cuối mỗi phase, ghi đè, không phụ lục thêm.

## Đang ở đâu

- Phase 1–8 xong. Phase 9 (broker) viết lại từ Bybit sang **Binance qua
  ccxt** ngày 2026-08-06 — xem "Đổi sàn" dưới đây. 178 passed / 4 skipped.
- Forward test chạy từ 2026-08-06, cấu hình đóng băng, launchd hằng ngày.
  Mốc đánh giá: 2026-11-06 / 2027-02-06 / 2027-08-06. KHÔNG bị ảnh hưởng
  bởi đổi sàn — `forward/logger.py` không dùng `broker/`.
- Cổng: `CLAUDE.md` #12 sửa ngày 2026-08-06 — xây tầng thực thi ở **testnet**
  được, **mainnet** bị chặn tới 2027-08-06.

## Đổi sàn Bybit -> Binance (ccxt) — 2026-08-06

Bybit chặn theo khu vực (`retCode 10024`) từ môi trường vận hành — không
kết nối được cả testnet lẫn mainnet, kể cả public endpoint. Chi tiết đầy
đủ + lý do kỹ thuật: `docs/DECISIONS.md`, mục "Đổi sàn Bybit -> Binance
(ccxt)".

Tóm tắt việc đã làm:
- `broker/ccxt_client.py::CCXTClient` — implementation đầy đủ, thay
  `broker/bybit_client.py` (giữ lại, đánh dấu deprecated trong docstring,
  không xoá — bằng chứng quyết định).
- `broker/base.py::ExchangeClient` bỏ `subscribe_klines`/`subscribe_executions`
  — chuyển hẳn WebSocket -> REST polling.
- `data/market_data.py` — bỏ heartbeat/`is_feed_alive`/cache, `get_latest_kline()`
  giờ luôn REST trực tiếp.
- `broker/position_tracker.py` — bỏ `on_execution()` (đường push không
  còn tồn tại), thêm `poll()` (cùng logic `reconcile_on_startup()`, gọi
  định kỳ từ main loop — Phase 10 chưa xây, main loop sẽ cần gọi
  `position_tracker.poll()` mỗi vòng).
- `broker/order_executor.py` — **0 thay đổi logic**, xác nhận bằng test
  cũ pass nguyên vẹn + đọc lại từng lệnh gọi ABC. 1 dòng comment sửa (tên
  cơ chế cũ đã xoá).
- `config/settings.yaml`: `exchange.name: binance`. `exchange.testnet` giữ
  nguyên tên (không thêm field `sandbox` trùng nghĩa).
- `ops/health_check.py` đọc `exchange.name` từ config thay vì hardcode
  Bybit; env var `BYBIT_API_KEY/SECRET/TESTNET` -> `EXCHANGE_API_KEY/SECRET/TESTNET`
  (đọc được cả tên cũ làm fallback). `ops/RUNBOOK.md` cập nhật theo (mục
  "Mất WebSocket" -> "Mất dữ liệu giá (REST polling thất bại)", mục "Xác
  thực Bybit thất bại" -> "Xác thực sàn thất bại", tổng quát hoá).
- Test mới/viết lại: `tests/test_ccxt_client.py` (31 test, mới),
  `tests/test_market_data.py`, `tests/test_position_tracker.py` (viết lại
  theo REST polling).

**Chưa có credential Binance thật** — `.env` có `EXCHANGE_API_KEY`/
`EXCHANGE_API_SECRET` để trống, key Bybit cũ trong `.env` giờ vestigial
(đã biết không hợp lệ từ trước, xem lịch sử Phase 9). `check_exchange_reachable`
xác nhận OK qua mạng thật (155ms, testnet.binance.vision);
`check_exchange_authenticated` FAIL đúng lý do (thiếu key, không phải bug)
— chưa nghiệm thu được phần cần auth thật (submit_order/cancel_order/
get_open_orders/get_balance/get_positions qua mạng) cho tới khi có key
Binance testnet thật.

## Việc còn treo, theo thứ tự ưu tiên

1. **Điền `EXCHANGE_API_KEY`/`EXCHANGE_API_SECRET` (Binance testnet) vào
   `.env`** rồi chạy lại checklist nghiệm thu cần mạng thật cho
   `CCXTClient` (submit_order thật, cancel_order thật, idempotency qua
   `clientOrderId` trùng — retCode/error thật của Binance chưa xác nhận,
   chỉ có Bybit's 401/10003 đã biết từ trước) — cùng mức độ nghiêm ngặt đã
   làm với Bybit ở Phase 9 ("xác nhận bằng output thật, không suy luận").
2. Phase 10 (main loop) — khi viết, nhớ gọi `position_tracker.poll()` mỗi
   vòng (đường cập nhật vị thế duy nhất bây giờ, không còn push).
3. 4 test skip trong `test_hmm.py`.
4. Lỗi mypy trong `test_forward_logger.py` (8 lỗi, không liên quan đổi
   sàn — pre-existing, chưa ai xử lý).
5. Copy `phase-12b-harness-engineering.md` và `phase-12c-shadow-deploy.md`
   vào `prompts/` (đã soạn, chưa có trong repo).

## Việc tiếp theo

Key Binance testnet thật -> nghiệm thu CCXTClient qua mạng -> Phase 10
(main loop, gọi `position_tracker.poll()` mỗi vòng thay vì subscribe).

## Quy tắc đã học, không lặp lại

- Mọi số đo thị trường lấy từ **testnet không dùng để hiệu chỉnh tham số**.
  Thanh khoản testnet là nhân tạo.
- Không bao giờ log giá trị key/secret, kể cả một phần.
- Không bao giờ hai tiến trình cùng khả năng đặt lệnh trên một tài khoản.
- Khả năng truy cập theo khu vực của một sàn có thể đổi bất kỳ lúc nào,
  không cảnh báo trước — đây là lý do `ExchangeClient` ABC tồn tại; giữ
  ranh giới đó sạch (không rò rỉ chi tiết sàn lên tầng trên) không phải
  kỹ thuật thừa, nó vừa được chứng minh cần thiết trong thực tế.
