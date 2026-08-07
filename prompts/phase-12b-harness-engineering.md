# Phase 12b — Harness Engineering (Test + Operational + Observability)

Đọc `docs/Brain-Crypto-Bybit.md` PHASE 9 và `docs/VALIDATION_REPORT.md`.

**Mục tiêu:** dựng dây an toàn quanh bot để nó tự kiểm tra, tự báo lỗi, và dễ bảo trì trong môi trường 24/7. **Không thêm logic giao dịch nào.**

**Thứ tự chạy:** phần A chạy được ngay (chỉ cần backtest ổn định). Phần B và C cần Phase 10–11 đã xong.

---

## 0. ĐIỀU KIỆN TIÊN QUYẾT — làm trước, nếu không phần A vô nghĩa

Regression harness ở phần A yêu cầu Sharpe chênh lệch ≤ 0.001 giữa hai lần chạy. Ngưỡng đó chỉ có nghĩa nếu backtest **tất định**.

Hiện tại `hmm.n_init = 10` chạy nhiều khởi tạo ngẫu nhiên. Nếu `random_state` không được cố định, hai lần chạy cùng dữ liệu sẽ cho model khác nhau, và Sharpe lệch nhiều hơn 0.001 rất nhiều — regression harness sẽ báo động giả liên tục rồi bị vô hiệu hoá vì phiền.

**Việc cần làm trước:**

1. Cố định `random_state` cho mọi lời gọi `GaussianHMM` và mọi thao tác ngẫu nhiên trong đường backtest. Thêm `seed` vào `settings.yaml`.
2. Viết `tests/test_determinism.py`: chạy cùng một backtest hai lần, khẳng định `equity_curve.csv` giống **bit-for-bit**.
3. Nếu không đạt tất định, tìm nguồn ngẫu nhiên còn sót (thứ tự dict, thao tác numpy chưa seed, song song hoá) và sửa trước khi đi tiếp.

Nếu vì lý do nào đó không thể đạt tất định hoàn toàn, **báo cáo lý do và đo phương sai thực tế** giữa 20 lần chạy, rồi đặt ngưỡng regression theo phương sai đó thay vì 0.001. Đừng giữ ngưỡng không đạt được.

---

## A. TEST HARNESS

### A.1 `tests/regression_harness.py`

Lưu snapshot từ cấu hình đã kiểm định ở Phase 7 (`reports/pruned8_base`): `equity_curve.csv`, `regime_history.csv`, `trade_log.csv`, `benchmark_comparison.csv`, `cost_report.csv` → `tests/snapshots/phase7_baseline/`.

Script so sánh kết quả backtest mới với snapshot:

| Chỉ số | Ngưỡng |
|---|---|
| Sharpe | chênh ≤ 0.001 (hoặc theo phương sai đo được ở §0) |
| Calmar | chênh ≤ 0.001 |
| Max drawdown | chênh ≤ 0.001 |
| Số lệnh | chênh ≤ 1% |
| Regime transitions | chênh ≤ 2% |
| Tổng phí | chênh ≤ 0.5% |

Vượt ngưỡng → fail rõ ràng, **in diff chi tiết**: dòng đầu tiên lệch, giá trị cũ/mới, và bar nào.

**Quan trọng:** harness này không phải để "cho qua". Nếu nó fail sau một refactor, câu hỏi là *thay đổi đó có cố ý ảnh hưởng kết quả không*, không phải *làm sao cho nó xanh*. Ghi quy tắc này vào docstring của file.

### A.2 `tests/test_properties.py` — Hypothesis

Property-based test cho các hàm thuần. Mỗi property chạy tối thiểu 1000 ví dụ.

- `trend_gate.get_allocation_cap()` → luôn nằm trong [0.0, 1.0]
- `signal_generator.compose_layer_allocations()` → `final <= min(mọi input)`, với mọi tổ hợp đầu vào hợp lệ
- `risk_manager` cap → không bao giờ trả về > `max_allowed`, và không bao giờ **tăng** allocation đầu vào
- `cost_model.rebalance_cost()` → phí ≥ 0, đơn điệu không giảm theo `abs(delta_qty)`
- `InstrumentRules.round_qty()` → kết quả luôn ≤ đầu vào (ROUND_DOWN), luôn là bội của `base_precision`
- `predict_regime_filtered()` → `state_probabilities` tổng bằng 1.0 (sai số 1e-9), mọi phần tử ≥ 0

Ghi chú: `tests/test_layer_composition.py` hiện có thể đã phủ property `min()`. Nếu vậy, **gộp vào file này** thay vì để hai chỗ, và cập nhật `CLAUDE.md` #15.

### A.3 `tests/test_snapshot.py`

Chạy backtest ngắn (7 ngày) với config mặc định, so sánh với snapshot đã commit: tổng phí, số lệnh, final equity, chuỗi regime.

Nhanh hơn `regression_harness.py` nhiều — dùng làm smoke test chạy mỗi lần commit, còn regression harness chạy trước mỗi lần merge lớn.

### A.4 Quan hệ với `tests/test_forward_golden.py`

Ba tầng, đừng để trùng nhau:

| Test | Phạm vi | Tần suất |
|---|---|---|
| `test_forward_golden.py` | Một lượt pipeline forward, dữ liệu tổng hợp | Mỗi lần chạy test |
| `test_snapshot.py` | Backtest 7 ngày | Mỗi commit |
| `regression_harness.py` | Backtest đầy đủ so với baseline Phase 7 | Trước merge lớn |

---

## B. OPERATIONAL HARNESS

### B.1 `monitoring/health.py` — khác với `ops/health_check.py`

Phân biệt rõ, đừng gộp:

- **`ops/health_check.py`** (đã có) — probe khởi động/liveness. Trả exit code 0/1. Docker dùng.
- **`monitoring/health.py`** (mới) — ảnh chụp trạng thái lúc chạy. Ghi ra file JSON, **không mở port** (mở port trên máy cá nhân là thêm bề mặt tấn công không cần thiết).

Ghi `monitoring/state/health.json`, cập nhật mỗi chu kỳ vòng lặp chính:

```json
{
  "status": "ok | degraded | down",
  "updated_at": "2026-08-06T09:35:00Z",
  "last_bar_time": "2026-08-06T08:00:00Z",
  "bars_behind": 0,
  "ws_latency_ms": 45,
  "api_latency_ms": 230,
  "clock_skew_ms": 34,
  "hmm_regime": "WEAK_BULL",
  "hmm_confidence": 0.72,
  "hmm_model_age_days": 2,
  "trend_gate": "BULL_STRUCTURE",
  "hmm_allocation": 0.95,
  "trend_gate_cap": 1.00,
  "risk_manager_cap": 1.00,
  "final_allocation": 0.95,
  "position_delta_pct": 2.3,
  "unfilled_orders": 1,
  "unfilled_value_usdt": 150.0,
  "circuit_breaker": "normal",
  "cumulative_fees_usdt": 47.50,
  "fees_pct_of_gross": 8.1,
  "last_alert_minutes_ago": 12,
  "uptime_seconds": 86400,
  "testnet": true
}
```

**Ba trường bắt buộc phải có mà thiết kế gốc thiếu:** `hmm_allocation`, `trend_gate_cap`, `risk_manager_cap` bên cạnh `final_allocation`. Chỉ nhìn `final_allocation` thì không biết **tầng nào** đang giới hạn. Đó là thông tin chẩn đoán quan trọng nhất khi có gì đó bất thường.

Quy tắc `status`:

- `down` — mất data feed > 2 chu kỳ bar, hoặc API không phản hồi, hoặc circuit breaker đang halt
- `degraded` — `bars_behind > 0`, hoặc `clock_skew_ms > 1000`, hoặc có lệnh chưa khớp > 5 phút, hoặc model HMM cũ hơn 2× chu kỳ retrain
- `ok` — còn lại

### B.2 `ops/RUNBOOK.md` — bổ sung

Với mỗi trạng thái `degraded`/`down`, thêm một mục: dấu hiệu, nguyên nhân thường gặp, cách xử lý, khi nào cần can thiệp tay.

### B.3 Kiểm tra tự động sau khởi động

`monitoring/health.py` phải có hàm `assert_healthy_or_alert()` chạy 60 giây sau khi bot khởi động: nếu `status != "ok"`, gửi cảnh báo ngay thay vì đợi tới lần kiểm tra định kỳ đầu tiên.

---

## C. OBSERVABILITY — phát hiện trôi lệch

Đây là phần có giá trị nhất và không có trong thiết kế gốc.

Bạn có số liệu baseline đo được từ backtest Phase 7. Bot chạy thật có thể trôi khỏi những con số đó, và **trôi lệch xuất hiện trước khi thua lỗ xuất hiện**.

### C.1 `monitoring/drift.py`

So sánh hành vi đang chạy với baseline backtest, cửa sổ trượt 30 ngày:

| Chỉ số | Baseline Phase 7 | Cảnh báo khi |
|---|---|---|
| Phân bố allocation (4 mức) | 30.6 / 18.1 / 16.5 / 34.8 % | Bất kỳ mức nào lệch > 15 điểm % |
| Tỷ lệ rebalance / bar | 32.3% | Lệch > 10 điểm % |
| Phí / lợi nhuận gộp | 11.68% | > 20% |
| Flicker rate | (lấy từ backtest) | Cao hơn baseline 2× |
| `warning_count` mỗi lần train | (lấy từ forward test) | Xu hướng tăng đơn điệu 3 lần liên tiếp |
| Thời gian trend gate chặn HMM | (lấy từ backtest) | Lệch > 20 điểm % |

Đọc baseline từ `tests/snapshots/phase7_baseline/`, không hardcode.

Xuất `monitoring/state/drift.json` và một bảng `rich` cho dashboard.

### C.2 Báo cáo tổng hợp hằng ngày

`monitoring/daily_digest.py` — chạy 00:05 UTC, ghi `logs/digest/YYYY-MM-DD.md`:

- Số bar xử lý, số lệnh, số lệnh bị risk manager từ chối kèm lý do
- Phân bố regime trong ngày, số lần đổi regime
- Số lần mỗi tầng là tầng giới hạn (HMM / trend gate / risk manager)
- P&L, phí, drawdown hiện tại
- Cảnh báo drift đang bật
- Số warning từ hmmlearn

Gửi qua Telegram nếu đã cấu hình ở Phase 11.

---

## RÀNG BUỘC BẮT BUỘC

1. **Không thêm logic giao dịch.** Phase này chỉ quan sát và kiểm tra.
2. **Không đường code nào trong Phase 12b được ghi vào `forward/`.** Đọc để lấy baseline thì được. Forward test là thí nghiệm 12 tháng với cấu hình đóng băng.
3. **`tests/test_forward_golden.py` phải còn xanh** sau toàn bộ Phase 12b.
4. Nếu §0 không đạt tất định, dừng và báo cáo trước khi làm phần A.

---

## Nghiệm thu

- [ ] `pytest tests/test_determinism.py -v` xanh — hai lần chạy backtest giống bit-for-bit
- [ ] `pytest tests/ -v` toàn bộ xanh, không skip, không xfail. Dán nguyên output.
- [ ] `tests/test_properties.py` chạy ≥ 1000 ví dụ mỗi property, tất cả pass
- [ ] Cố tình sửa một hằng số trong `core/regime_strategies.py` → `regression_harness.py` phải FAIL và in diff chỉ đúng chỗ. Revert lại sau khi thử.
- [ ] Cố tình sửa `compose_layer_allocations` thành `max()` → `test_properties.py` phải FAIL
- [ ] `monitoring/state/health.json` sinh ra đúng schema, đủ ba trường `hmm_allocation` / `trend_gate_cap` / `risk_manager_cap`
- [ ] Mô phỏng mất data feed → `status` chuyển `down` trong vòng 2 chu kỳ bar
- [ ] `monitoring/drift.py` chạy trên chính dữ liệu backtest Phase 7 → không cảnh báo gì (sanity check: baseline không được tự báo động)
- [ ] `grep -rn "forward/" monitoring/ tests/regression_harness.py` — chỉ có thao tác đọc, không có ghi
- [ ] `ruff check . && mypy .` sạch
