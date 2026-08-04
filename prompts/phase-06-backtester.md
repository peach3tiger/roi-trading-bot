# Phase 6 — Walk-Forward Backtester + Cost Model

Đọc `docs/Brain-Crypto-Bybit.md` PHASE 4 (§4.1 → §4.7).

Phase dễ sai nhất sau HMM. Cân nhắc dùng plan mode.

## Việc cần làm

### `backtest/cost_model.py` — làm trước, tách riêng

Phí 0.10% mỗi chiều, slippage 0.03%. Method `rebalance_cost()` và `total_cost_report()`.

### `backtest/backtester.py`

Walk-forward: IS 365 bar, OOS 182 bar, step 182 bar.

**Toán allocation — chép đúng §4.2 của spec.** Điểm chết người:

```python
# SAI — đây là code equity, sẽ làm tròn mọi vị thế BTC về 0
target_qty = int(equity * target_allocation / price)

# ĐÚNG
target_qty = Decimal(...).quantize(base_precision, rounding=ROUND_DOWN)
```

Bỏ qua rebalance nếu giá trị lệnh < 5 USDT.

Spot: `target_allocation` không bao giờ > 1.0, `cash` không bao giờ âm. Nếu backtest ra cash âm, có bug.

Fill delay 1 bar. Ngưỡng rebalance 25%.

### `backtest/performance.py`

Annualize bằng **√365**. Đủ các chỉ số trong §4.5, bảng theo regime, bảng theo confidence bucket.

Bốn benchmark: buy-and-hold, SMA200 trend, random allocation (100 seed), **vol-targeting tĩnh không dùng HMM**.

Benchmark thứ tư là bài kiểm tra thật. Nếu HMM không vượt được vol-targeting đơn giản, cả tầng HMM là phức tạp thừa.

### `backtest/stress_test.py`

Crash injection **-15% đến -40%** (crypto, không phải -5..-15 như equity). Gap risk, regime misclassification, exchange outage.

## Nghiệm thu

- [ ] `pytest tests/test_cost_model.py tests/test_precision.py -v` xanh
- [ ] Test: `cash >= 0` ở mọi bar của mọi lần chạy
- [ ] Test: `equity = cash + qty * price` đúng ở mọi bar (sai số < 0.01 USDT)
- [ ] Test: tổng phí trong `cost_report` khớp tổng phí trong `trade_log`
- [ ] Chạy full backtest 2018→nay, xuất đủ: `equity_curve.csv`, `trade_log.csv`, `regime_history.csv`, `benchmark_comparison.csv`, `cost_report.csv`
- [ ] In bảng so sánh 4 benchmark
- [ ] In tổng phí theo USDT **và** theo % lợi nhuận gộp
- [ ] Chạy 2 lần với `--end` khác nhau, kiểm tra phần chồng lấn của equity curve giống hệt nhau (xác nhận lại không có look-ahead)

Dán bảng benchmark và dòng chi phí.
