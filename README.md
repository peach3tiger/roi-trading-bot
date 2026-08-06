# regime-trader-crypto

> Đọc file này trước. Nếu bạn đang đọc vì đã quên hết — bắt đầu từ đây,
> rồi tới link nào cần.

## Dự án làm gì

Bot phân bổ danh mục BTC/USDT theo chế độ biến động: HMM phát hiện regime
bằng forward algorithm (không nhìn tương lai), long-only, giảm tỷ trọng khi
biến động cao thay vì đảo chiều short. Walk-forward backtest đạt 6/8 tiêu
chí đi tiếp (§4.9) — giảm drawdown thật so với buy-and-hold nhưng **chưa**
chứng minh được lợi thế Sharpe; đang chạy forward test ghi log 12 tháng để
lấy bằng chứng ngoài mẫu thật, song song xây tầng thực thi ở TESTNET
(`risk_manager` đã xong, `order_executor` đang làm — chưa đủ để đặt lệnh
nào). Testnet là mặc định, chuyển mainnet cần gõ tay xác nhận **và** đạt
mốc 12 tháng của forward test, và risk manager có quyền phủ quyết tuyệt
đối, độc lập hoàn toàn với HMM.

## Sơ đồ

```
BTC OHLCV (Binance, HistoryLoader)
        │
        ▼
features tầng 1 — 8 cột "pruned-8", z-score rolling 365 ngày
        │                                   (data/feature_engineering.py)
        ▼
HMM filtered inference — P(state_t | obs_1:t), n_components tự chọn bằng BIC
        │                                          (core/hmm_engine.py)
        ▼
xếp hạng state theo VOLATILITY (vol_rank — độc lập với nhãn return)
        │                                    (core/regime_strategies.py)
        ▼
hmm_allocation  (LowVol 95% / MidVol 60-95% / HighVol 50%, ×0.5 nếu bất định)
        │
        │              StructuralTrendGate — SMA200 + slope → trend_gate_cap
        │                                          (core/trend_gate.py)
        └───────────────────────┬──────────────────────────┘
                                 ▼
              final_allocation = min(hmm_allocation, trend_gate_cap)
                                 │                (core/signal_generator.py)
                    ┌────────────┴────────────┐
                    ▼                          ▼
        backtest: mô phỏng lệnh          forward test: append 1 dòng
        (Decimal, cost model)            vào forward/log.csv — KHÔNG
        (backtest/backtester.py)         đặt lệnh (forward/logger.py)
```

## Trạng thái hiện tại

**6/8 tiêu chí §4.9 PASS** (2 fail nằm trong sai số đo, xem
[docs/VALIDATION_REPORT.md](docs/VALIDATION_REPORT.md)). CLAUDE.md bất
biến #12 đã sửa (2026-08-06): chưa đủ 8/8 vẫn được xây tầng thực thi ở
**TESTNET** — `core/risk_manager.py` đã implement (Phase 8/phase-08).
`broker/order_executor.py` (Phase 9/phase-09) vẫn là stub. **Mainnet/tiền
thật vẫn bị chặn** tới khi forward test đạt mốc 12 tháng (2027-08-06) và
§4.9 được đánh giá lại trên dữ liệu forward.

Đang chạy **forward test** (ghi log, không đặt lệnh) thay vì tiếp tục quét
tham số trên dữ liệu lịch sử — tập đó đã bị nhìn quá nhiều lần, không còn
ngoài mẫu (VALIDATION_REPORT.md mục 3.4). Mốc đánh giá — xem
[docs/DECISIONS.md](docs/DECISIONS.md), mục "Forward test — tiền đăng ký":

| mốc | ngày | đọc gì |
|---|---|---|
| 3 tháng | 2026-11-06 | chỉ xem hành vi, không rút kết luận thống kê |
| 6 tháng | 2027-02-06 | đọc sơ bộ xu hướng equity 4 track |
| 12 tháng | 2027-08-06 | thống kê đầu tiên có nghĩa (Sharpe/Calmar/max DD thật) |

## Ba quyết định phản trực giác

1. **`uncertainty_mode="halve"` giữ nguyên**, dù thí nghiệm A/B/C cho thấy
   bỏ nó cải thiện Sharpe trên đúng các bar confidence thấp. Lý do: ở cấp
   toàn kỳ nó làm việc phòng vệ đuôi thật — bỏ nó làm max drawdown sâu thêm
   ~5.6pp. → `docs/DECISIONS.md`, "Thí nghiệm: uncertainty-mode".

2. **Feature cắt từ 14 xuống còn 8 cột ("pruned-8")**. Lý do:
   `covariance_type=full` làm số tham số HMM tăng BẬC HAI theo số feature —
   14 cột làm `samples_per_param < 1` ở MỌI window walk-forward (mô hình
   thiếu dữ liệu nghiêm trọng so với số tham số), biểu hiện ra ngoài là bất
   ổn khi đổi mốc bắt đầu và ETH fail tiêu chí ngoài mẫu. → `docs/DECISIONS.md`,
   "Vì sao pruned-8, không phải 14 cột Tầng 1 đầy đủ".

3. **`n_components` để BIC tự chọn mỗi window, không cố định**. Lý do: độ
   phức tạp thị trường đổi theo giai đoạn (2018 khác 2021 khác 2023) — ép
   cố định một số regime là một dạng bias lựa chọn ẩn, không phải đơn giản
   hoá vô hại. → docstring `HMMRegimeEngine.select_and_train`
   (`core/hmm_engine.py`) + cột `bic_margin`/`samples_per_param` trong mọi
   `model_selection.csv`, dùng để phát hiện đúng lựa chọn BIC nào "nông"
   (dễ lật khi dữ liệu xê dịch nhẹ) — xem `docs/DECISIONS.md`.

## Cách chạy

**Backtest** (tái lập đúng cấu hình đã kiểm định, 6/8 §4.9):

```bash
python main.py --backtest \
  --feature-subset log_return_1,log_return_5,realized_vol_20,vol_ratio_5_20,adx_14,sma50_slope,trade_count_zscore_50,trade_count_sma10_slope \
  --start 2018-02-09 --end 2026-08-04 \
  --output-dir reports/my_run
```

Kết quả vào `reports/my_run/` (equity curve, trade log, benchmark, cost
report). Cờ hữu ích khác: `--period 2022`, `--bar-offset 0,6,12,18`,
`--symbol ETHUSDT`, `--ablation`, `--no-trend-gate` — xem `main.py::build_arg_parser`.

**Forward test** (ghi log, không đặt lệnh — xem `forward/README.md`):

```bash
python -m forward.logger
```

In JSON `{"appended": N, "last_logged_date": "..."}`. Idempotent — chạy
lại cùng ngày không ghi trùng; máy tắt vài ngày tự bù khi chạy lại.

**Kiểm tra lịch chạy tự động (launchd) đang sống:**

```bash
launchctl print gui/$(id -u)/com.regime-trader-crypto.forward-test
```

`state = running` hoặc `waiting` là bình thường; `last exit code = 0` là
lần chạy gần nhất không lỗi. Hướng dẫn nạp/gỡ đầy đủ:
`forward/README.md`, mục "Lịch chạy tự động (launchd)".

## Bất biến quan trọng nhất

Chi tiết đầy đủ (10+ bất biến): [`CLAUDE.md`](CLAUDE.md) — đọc trước khi
sửa bất kỳ thứ gì trong `core/`. Ba cái sau là nền tảng, phá cái nào cũng
phá toàn bộ mô hình phòng thủ:

- **`min()`, không bao giờ `max()`** khi kết hợp tầng
  (`final_allocation = min(hmm_allocation, trend_gate_cap, risk_cap)`).
  Lý do: đây là toàn bộ lý do hệ thống an toàn khi một tầng hỏng — một
  tầng sai chỉ có thể làm tỷ trọng THẤP hơn, không bao giờ cao hơn.
- **Không bao giờ `model.predict()`/`decode()` của hmmlearn**, chỉ
  `predict_regime_filtered()` (forward algorithm, log space, chỉ dữ liệu
  tới hiện tại). Lý do: `predict()` chạy Viterbi trên toàn chuỗi, sửa lại
  quá khứ bằng dữ liệu tương lai — backtest đẹp giả tạo, live thất bại.
- **`Decimal` cho mọi qty/giá trong đường thực thi**, không bao giờ trộn
  với `float`. Lý do: `int()`/float rounding làm mọi vị thế BTC dưới một
  đơn vị lặng lẽ về 0 — backtest vẫn chạy, chỉ là không bao giờ vào lệnh.

## Tài liệu

| file | nội dung |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | bất biến bắt buộc — đọc trước mỗi phiên |
| [`docs/Brain-Crypto-Bybit.md`](docs/Brain-Crypto-Bybit.md) | spec đầy đủ theo phase |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | nhật ký quyết định + số liệu thật, theo thời gian |
| [`docs/VALIDATION_REPORT.md`](docs/VALIDATION_REPORT.md) | đóng giai đoạn kiểm định Phase 1-7, bảng §4.9 đầy đủ |
| [`forward/README.md`](forward/README.md) | forward test — đóng băng config, backfill, launchd |
| [`ops/RUNBOOK.md`](ops/RUNBOOK.md) | triển khai container — circuit breaker, HMM retrain lỗi, mất WebSocket, khôi phục sau crash |

## Disclaimer

Testnet Bybit là mặc định (`config/settings.yaml: testnet: true`);
mainnet yêu cầu gõ tay chuỗi xác nhận đầy đủ **và** đạt mốc 12 tháng của
forward test (2027-08-06, CLAUDE.md bất biến #12). `broker/order_executor.py`
chưa xong nên chưa có đường nào đặt lệnh được, kể cả ở testnet. Đây không
phải lời khuyên đầu tư — kể cả sau
khi forward test xong 12 tháng, kết quả OOS tốt trên một chu kỳ thị trường
không đảm bảo gì cho chu kỳ sau. Không hardcode credentials; `.env` nằm
trong `.gitignore`.
