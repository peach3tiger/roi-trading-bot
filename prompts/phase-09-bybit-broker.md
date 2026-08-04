# Phase 9 — Bybit Integration

Đọc `docs/Brain-Crypto-Bybit.md` PHASE 6.

## Việc cần làm

### `broker/base.py` — viết trước tiên
ABC `ExchangeClient`. Tầng strategy và risk **không bao giờ** import `pybit` trực tiếp.

### `broker/instrument_rules.py`
Lấy từ `/v5/market/instruments-info`, cache. `round_qty()` luôn `ROUND_DOWN`. `round_price()` theo tickSize. `is_valid_order()` kiểm tra trước khi gửi.

### `broker/bybit_client.py`
- Testnet `api-testnet.bybit.com` mặc định
- Mainnet cần gõ `YES I UNDERSTAND THE RISKS`
- **Đồng bộ đồng hồ với server lúc khởi động**, cảnh báo nếu lệch > 1s — đây là nguyên nhân số 1 gây lỗi auth với Bybit
- Token bucket cho rate limit (600 req / 5s / IP), chặn ở phía client
- Auto-reconnect, exponential backoff

### `broker/order_executor.py`
- LIMIT ±0.05%, huỷ sau 30s
- **`orderLinkId` deterministic** từ `(symbol, bar_timestamp, target_allocation)` — khoá idempotency
- Xử lý khớp một phần
- `modify_stop()` chỉ siết, không nới

### `broker/position_tracker.py`
WebSocket private stream. **Đối soát với sàn lúc khởi động** — thị trường 24/7 nên bot offline giữa lúc thị trường chạy là chuyện thường ngày, không phải ngoại lệ.

### `data/market_data.py`
WebSocket public + heartbeat ping/pong. Không nhận dữ liệu quá 2× chu kỳ bar → coi như mất feed.

## Nghiệm thu — trên testnet thật

- [ ] `pytest tests/test_precision.py tests/test_orders.py -v` xanh
- [ ] `grep -rn "import pybit\|from pybit" core/` — không có kết quả
- [ ] Kết nối testnet, in số dư và `InstrumentRules` của BTCUSDT
- [ ] Đặt lệnh limit → xác nhận trên testnet → huỷ → kiểm tra trạng thái sạch
- [ ] Gửi **hai lần** cùng một signal → chỉ một lệnh được đặt (idempotency)
- [ ] Ngắt mạng giữa lúc có lệnh chờ → khôi phục → đối soát đúng, không đặt trùng
- [ ] Test làm tròn: qty = 0.0000001 BTC → bị từ chối vì dưới min; qty = 0.12345678 → làm tròn xuống đúng basePrecision
