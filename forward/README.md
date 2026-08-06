# forward/ — Forward test: ghi log, KHÔNG đặt lệnh

Chạy hệ thống đã kiểm định (xem `docs/VALIDATION_REPORT.md`) trên dữ liệu
BTC mới nhất, mỗi bar tính regime/confidence/allocation rồi **chỉ append
một dòng vào `forward/log.csv`**. Không có bất kỳ đường code nào gửi lệnh
ra sàn — `forward/logger.py` không import gì từ `broker/`
(`tests/test_forward_logger.py::test_no_broker_import` kiểm tra tĩnh điều
này).

Lý do làm forward test thay vì đi tiếp Phase 8-12: xem
`docs/VALIDATION_REPORT.md` mục 6 (Quyết định) và
`docs/DECISIONS.md` mục "Forward test — tiền đăng ký".

## Đóng băng cấu hình

Hai khối, coi là **một** — đổi khối nào cũng phá thí nghiệm:

1. **`forward/config_frozen.yaml`** — bản copy nguyên văn `config/settings.yaml`
   tại thời điểm bắt đầu. `forward/logger.py` CHỈ đọc file này qua
   `load_frozen_settings()`, không bao giờ đọc `config/settings.yaml`.

   ```
   SHA256: be741c659bc5a11d607955e64ec27cb0b194c1b6c368ca09704b8d056a1ec15c
   ```

   Hash này cũng nằm ở `forward/config_frozen.sha256` — mỗi lần chạy,
   `logger.py` tính lại sha256 của `config_frozen.yaml` và so với file đó.
   Lệch hash → `RuntimeError` ngay, không ghi gì, không âm thầm chạy tiếp
   trên config đã bị sửa.

2. **`FEATURE_SUBSET`** trong `forward/logger.py` — bộ 8 cột "pruned-8" đã
   kiểm định (`log_return_1, log_return_5, realized_vol_20, vol_ratio_5_20,
   adx_14, sma50_slope, trade_count_zscore_50, trade_count_sma10_slope`).
   `settings.yaml` không có field này — nó là tham số CLI-only của
   `main.py` (`--feature-subset`), không phải config field — nên phải đóng
   băng riêng làm hằng số nguồn, ngay trong file đã commit. Đây chính xác
   là bộ feature dùng để đạt kết quả 6/8 §4.9 trong
   `docs/VALIDATION_REPORT.md` — dùng bộ 14 cột mặc định (khi không truyền
   subset) sẽ chạy một hệ thống KHÁC, đã bị loại (xem
   `docs/DECISIONS.md`, "Vì sao pruned-8, không phải 14 cột Tầng 1 đầy đủ").

**Muốn đổi bất kỳ thứ gì trong hai khối trên: KẾT THÚC thí nghiệm hiện
tại, bắt đầu thí nghiệm mới, ghi rõ vào `docs/DECISIONS.md`.** Không sửa
tại chỗ giữa chừng.

## Chạy

```bash
python -m forward.logger
```

In ra JSON `{"appended": N, "last_logged_date": "YYYY-MM-DD"}`. `N=0` nghĩa
là log đã cập nhật, không có bar mới (idempotent — chạy lại cùng ngày
không ghi thêm dòng nào).

Crontab gợi ý (chạy mỗi ngày lúc 01:00 UTC, đủ trễ sau ranh giới bar 00:00
UTC để dữ liệu ngày hôm qua chắc chắn đã có trên Binance):

```
0 1 * * * cd /path/to/regime-trader-crypto && .venv/bin/python -m forward.logger >> forward/cron.log 2>&1
```

## Backfill

Máy tắt vài ngày không sao — lần chạy kế tiếp tự phát hiện bar còn thiếu
(so `forward/log.csv`'s dòng cuối với bar mới nhất đã đóng trên sàn) và bù
lại TỪNG bar theo đúng thứ tự thời gian, mỗi bar chỉ dùng dữ liệu tới đúng
bar đó (không look-ahead). Chạy hằng ngày hay hằng tuần cho ra
`forward/log.csv` **giống hệt nhau** — `forward/logger.py::pending_bar_dates`
là hàm thuần, chỉ phụ thuộc khoảng cách ngày, không phụ thuộc tần suất gọi.

**Ngoại lệ duy nhất:** lần chạy ĐẦU TIÊN (log.csv chưa tồn tại) chỉ ghi
ĐÚNG MỘT bar — bar gần nhất đã đóng tại thời điểm chạy — không backfill
ngược về quá khứ. Tập dữ liệu lịch sử đã bị nhìn nhiều lần trong quá trình
kiểm định (`docs/VALIDATION_REPORT.md` mục 3.4); forward test chỉ có ý
nghĩa với dữ liệu CHƯA từng dùng để quyết định gì ở trên.

## Retrain

Theo lịch `hmm.retrain_interval_days` trong config đóng băng (7 ngày bar),
KHÔNG phải mỗi lần chạy. Cửa sổ train MỞ RỘNG (expanding) từ `DATA_START`
(2018-02-09, khớp nền dữ liệu đã dùng để kiểm định) — không phải cửa sổ
trượt `is_bars=365` của backtest walk-forward, vì `settings.yaml` không có
tham số độ dài cửa sổ train cho chế độ sống; `hmm.min_train_bars: 730` đọc
đúng như một sàn an toàn cho training SỐNG (2 năm dữ liệu thật), khớp lựa
chọn expanding-window. Xem docstring đầu `forward/logger.py` để biết lý do
đầy đủ.

Model KHÔNG được lưu vào `forward/log.csv` hay git — `forward/state/hmm_model.pkl`
(gitignored, `*.pkl`) chỉ là cache hiệu năng, tránh phải retrain lại (tốn
`n_candidates × n_init` lần fit EM) mỗi khi tiến trình khởi động lại giữa
hai lần tới hạn retrain thật. Mất file này không sai gì — lần chạy sau tự
retrain lại và ghi cache mới. **Thẩm quyền lịch retrain luôn là cột
`hmm_retrained` trong `forward/log.csv`**, không phải cache.

## Bốn đường equity song song

Mỗi dòng log ghi cash/qty/equity/target riêng cho 4 track paper (không có
vốn thật, không có lệnh thật): `strategy` (hệ thống đầy đủ — HMM →
StrategyOrchestrator → `min(hmm_allocation, trend_gate_cap)`),
`bh` (buy-and-hold), `sma200` (long khi giá > SMA200), `volTarget`
(nhắm vol ngày cố định 2%, không dùng HMM). Cả 4 dùng **chung** giá
OHLCV, **chung** cost model (0.10% phí + 0.03% slippage, đọc từ config
đóng băng), **chung** ngưỡng rebalance 25% và fill delay 1 bar — không có
bốn đường này thì không so sánh được gì.

## Cột `forward/log.csv`

`date, run_at_utc, open_price, close_price, hmm_retrained, hmm_train_bars,
regime_id, regime_label, regime_probability, regime_is_confirmed,
is_flickering, hmm_allocation, trend_gate_state, trend_gate_cap,
final_allocation`, rồi 4 nhóm `{prefix}_cash, {prefix}_qty, {prefix}_equity,
{prefix}_target_allocation` cho `prefix ∈ {strategy, bh, sma200, volTarget}`.

`{prefix}_target_allocation` của dòng hiện tại chính là target sẽ được
thực thi ở giá OPEN của bar KẾ TIẾP (fill delay 1 bar) — đây là toàn bộ
trạng thái cần để tiếp tục, không có state file ẩn nào khác ngoài cache
model (mục Retrain).

## Mốc đánh giá

Xem `docs/DECISIONS.md`, mục "Forward test — tiền đăng ký" — 3 tháng
(chỉ xem hành vi, không rút kết luận thống kê), 6 tháng (đọc sơ bộ), 12
tháng (đọc thống kê đầu tiên có nghĩa).
