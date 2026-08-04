# Phase 11 — Monitoring, Alerts & Dashboard

Đọc `docs/Brain-Crypto-Bybit.md` PHASE 8.

## Việc cần làm

### `monitoring/logger.py`
JSON có cấu trúc, file xoay vòng 10MB/30 ngày. Mỗi entry gồm cả `cumulative_fees_paid`.

### `monitoring/dashboard.py`
Layout `rich` như trong spec. Refresh 5 giây.

**Ô "Phí tháng này" phải luôn hiển thị** — nó là chỉ báo sớm cho việc giao dịch quá nhiều, và là thứ dễ bị bỏ qua nhất khi mọi thứ khác đang xanh.

Hiển thị cả trạng thái trend gate bên cạnh regime — hai tầng độc lập, cần nhìn thấy cả hai.

### `monitoring/alerts.py`
Trigger: đổi regime, đổi trạng thái trend gate, circuit breaker, P&L lớn, mất feed, mất API, HMM retrain, flicker vượt ngưỡng, USDT lệch peg, spread bất thường, lệch đồng hồ > 1s.

**Telegram là kênh chính** — bot chạy 24/7 nên cần nhận cảnh báo trên điện thoại lúc 3 giờ sáng. Email và webhook tuỳ chọn.

Rate limit 1 cảnh báo / loại sự kiện / 15 phút.

## Nghiệm thu

- [ ] Dashboard chạy được với dữ liệu thật từ testnet, chụp lại màn hình dạng text
- [ ] Kích hoạt thủ công từng loại alert, xác nhận nhận được trên Telegram
- [ ] Rate limit hoạt động: bắn 10 alert cùng loại trong 1 phút → chỉ nhận 1
- [ ] Log file xoay vòng đúng khi vượt 10MB
- [ ] `grep -rn "print(" monitoring/ core/ broker/` — không có `print()` trong code production
- [ ] Xác nhận không có API key nào xuất hiện trong log, kể cả một phần
