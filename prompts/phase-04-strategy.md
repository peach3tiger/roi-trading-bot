# Phase 4 — Allocation Strategy

Đọc `docs/Brain-Crypto-Bybit.md` PHASE 3.

## Việc cần làm

Implement `core/regime_strategies.py`:

- ABC `Strategy` với `generate_signal(symbol, bars, regime_state) -> Optional[Signal]`
- Ba class: `LowVolBullStrategy` (95%), `MidVolCautiousStrategy` (95%/60% theo EMA50), `HighVolDefensiveStrategy` (50%)
- **Leverage luôn 1.0** — spot không có margin
- `StrategyOrchestrator`: sắp regime theo `expected_volatility` tăng dần, ánh xạ vol rank → strategy class
- Ngưỡng rebalance **25%**
- Uncertainty mode: giảm nửa allocation khi `prob < 0.55` hoặc đang flicker
- `Signal` dataclass dùng `Decimal` cho giá và allocation
- Alias tương thích ngược + `LABEL_TO_STRATEGY`

## Điểm dễ sai — chú ý

Orchestrator sắp theo **volatility**. Việc gán nhãn sắp theo **return**. Hai phép sắp này độc lập. Regime tên `EUPHORIA` trong crypto thường là regime vol **cao nhất** — nếu code để nhãn dẫn dắt strategy, bot sẽ all-in đúng đỉnh.

## Nghiệm thu

- [ ] `pytest tests/test_strategies.py -v` xanh
- [ ] Test: dựng `regime_infos` giả trong đó nhãn `BULL` có vol cao nhất → orchestrator phải map nó vào `HighVolDefensiveStrategy`, không phải `LowVolBullStrategy`
- [ ] Test: allocation trả về không bao giờ vượt 1.0
- [ ] Test: uncertainty mode giảm đúng một nửa
- [ ] Test: thay đổi allocation dưới 25% không sinh signal rebalance
- [ ] `grep -rn "leverage" core/regime_strategies.py` — mọi giá trị đều là 1.0
- [ ] Chạy orchestrator trên regime_infos thật từ Phase 3, in bảng: regime_id, label, expected_vol, vol_rank, strategy được gán
