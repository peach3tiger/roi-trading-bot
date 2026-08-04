# Phase 8 — Risk Management Layer

Chỉ bắt đầu sau khi Phase 7 đạt 8/8.

Đọc `docs/Brain-Crypto-Bybit.md` PHASE 5.

## Việc cần làm

Implement `core/risk_manager.py`: `RiskManager`, `RiskDecision`, `PortfolioState`, `CircuitBreaker`.

Circuit breaker (ranh giới ngày 00:00 UTC, tuần Thứ Hai 00:00 UTC):
- Daily DD > 4% → giảm size 50%; > 6% → đóng hết, dừng ngày
- Weekly DD > 10% → giảm 50%; > 14% → đóng hết, dừng tuần
- Peak DD > 20% → ghi `trading_halted.lock`, dừng hoàn toàn

**Hiệu chỉnh ngưỡng bằng dữ liệu, không dùng số mặc định.** Lấy phân phối lợi nhuận ngày từ backtest ở Phase 6, đặt ngưỡng "giảm size" ở phân vị 2–3%, ngưỡng "dừng" ở phân vị 0.5%. In ra các phân vị và ngưỡng đề xuất, cập nhật `settings.yaml`.

Rule đặc thù crypto (§5.4): kiểm tra spread > 0.10%, cảnh báo USDT lệch peg > 0.5%, log tổng số dư trên sàn.

Bỏ correlation check nhưng **giữ interface**.

## Bất biến phải giữ

`risk_manager.py` **không được import** `hmm_engine` hay `regime_strategies`. Nó ra quyết định từ P&L thực tế. Sự độc lập này là lý do nó bảo vệ được khi HMM sai.

## Nghiệm thu

- [ ] `pytest tests/test_risk.py -v` xanh
- [ ] `grep -n "import" core/risk_manager.py` — không có hmm_engine, không có regime_strategies
- [ ] Test: signal không có stop loss → bị từ chối
- [ ] Test: signal allocation 200% → bị cap về giới hạn
- [ ] Test: hai signal giống nhau trong 60 giây → cái thứ hai bị chặn
- [ ] Test: mô phỏng chuỗi lỗ → từng circuit breaker kích hoạt đúng ngưỡng, đúng thứ tự
- [ ] Test: peak DD 20% → file `trading_halted.lock` được tạo, mọi signal sau đó bị từ chối
- [ ] In phân vị lợi nhuận ngày từ dữ liệu thật và ngưỡng đề xuất
