# Phase 5 — Structural Trend Gate

Đọc `docs/Brain-Crypto-Bybit.md` PHASE 3.5 toàn bộ.

Đây là phần **không có trong spec gốc**. Nó tồn tại để xử lý một chế độ thất bại cụ thể của crypto: thị trường giảm kéo dài với biến động thấp (BTC năm 2022). HMM sẽ đọc giai đoạn đó là "vol thấp" và vào 95%.

## Việc cần làm

Implement `core/trend_gate.py`:

- Hai đầu vào: `price_vs_sma200`, `sma200_slope_30`
- Ba trạng thái với trần: `BULL_STRUCTURE` 100%, `TRANSITION` 60%, `BEAR_STRUCTURE` 30%
- Buffer 2% quanh SMA200 (dải chết chống whipsaw)
- Xác nhận 5 bar
- Bất đối xứng: siết trần có hiệu lực ngay, nới trần phải qua xác nhận

Implement `core/signal_generator.py` — nơi kết hợp ba tầng:

```python
final_allocation = min(hmm_allocation, trend_gate_cap, risk_manager_cap)
```

## Giữ đơn giản — quan trọng

Ba tham số, hai đầu vào, ba trạng thái. **Không thêm gì.** Đừng thêm MA thứ hai, đừng thêm ngưỡng động, đừng thêm xác nhận momentum. Giá trị của tầng này nằm ở chỗ nó gần như không thể overfit.

## Nghiệm thu

- [ ] `pytest tests/test_trend_gate.py tests/test_layer_composition.py -v` xanh
- [ ] Property test: với 10.000 bộ giá trị ngẫu nhiên, `final_allocation <= min(mọi input)` luôn đúng
- [ ] Test: giá dao động quanh SMA200 trong dải ±2% → trạng thái không đổi
- [ ] Test: trần giảm có hiệu lực ngay ở bar tiếp theo; trần tăng cần đủ 5 bar
- [ ] `grep -rn "max(" core/signal_generator.py` — không được có, phải là `min()`
- [ ] Chạy gate trên dữ liệu BTC 2021-01 → 2023-12, in biểu đồ text trạng thái theo thời gian. Kiểm bằng mắt: phần lớn 2022 phải là `BEAR_STRUCTURE`.

Mục cuối là nghiệm thu thật sự. Nếu gate không nhận ra 2022 là bear structure, nó không làm được việc nó sinh ra để làm.
