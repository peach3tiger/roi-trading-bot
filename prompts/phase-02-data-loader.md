# Phase 2 — Historical Data Loader

Đọc `docs/Brain-Crypto-Bybit.md` §4.0 trước khi bắt đầu.

Làm trước HMM. Không có dữ liệu sạch thì không kiểm chứng được gì.

## Việc cần làm

Implement `data/history_loader.py`:

- Tải OHLCV qua CCXT từ **Binance** (BTC/USDT có từ 2017, dài hơn Bybit nhiều), có phân trang (~1000 bar/request)
- Cache ra parquet trong `data/cache/`, lần chạy sau chỉ tải phần thiếu
- Kline phải giữ cả `trade_count`/`turnover` — spec dùng nó thay cho volume
- Hỗ trợ tham số `bar_offset_hours` (0/6/12/18) để phục vụ bài test độ nhạy mốc đóng bar ở §4.8b

Kiểm tra tính toàn vẹn, fail rõ ràng nếu phát hiện:
- Thiếu bar (khoảng trống trong chuỗi timestamp)
- Timestamp trùng lặp
- Bar có volume = 0 hoặc OHLC không hợp lệ (`high < low`, `close` ngoài `[low, high]`)
- Giá nhảy phi lý (> 50% trong một bar ngày) — có thể là lỗi dữ liệu, không phải sự kiện thật

Ghi metadata mỗi lần tải: nguồn, khoảng thời gian, số bar, hash dữ liệu.

Thêm module tải dữ liệu phái sinh (funding rate, open interest) từ Bybit `/v5/market/funding-history` và `/v5/market/open-interest` — **để riêng, chưa dùng ở Phase 3**. Ghi rõ khoảng lịch sử khả dụng vì nó ngắn hơn dữ liệu giá.

## Nghiệm thu

- [ ] Tải được BTC/USDT 1D từ 2018-01-01 tới nay, in ra số bar và khoảng thời gian
- [ ] Chạy lần hai nhanh hơn rõ rệt (cache hoạt động)
- [ ] Kiểm tra toàn vẹn pass, in báo cáo
- [ ] Cố tình làm hỏng file cache (xoá vài dòng giữa) → loader phát hiện và báo lỗi rõ ràng
- [ ] `bar_offset_hours=6` cho ra chuỗi khác với `0` và cả hai đều pass kiểm tra toàn vẹn
- [ ] Tải được funding rate, in ra khoảng lịch sử khả dụng

Dán output.
