# STATE — bàn giao trạng thái

Đọc file này đầu mỗi phiên. Chi tiết ở `DECISIONS.md` và
`VALIDATION_REPORT.md`. Cập nhật ở cuối mỗi phase, ghi đè, không phụ lục thêm.

> **Quy tắc "tối đa một trang" ở dòng này ĐÃ VỠ và tôi không giả vờ ngược
> lại.** File dài 580 dòng tính tới 2026-08-14, và mỗi phase lại thêm một
> mục. Hai lựa chọn, cần bạn chốt: (a) cắt gọn về đúng một trang, đẩy
> phần lịch sử xuống `DECISIONS.md` — mất khả năng đọc-một-chỗ; (b) bỏ
> câu "tối đa một trang" và chấp nhận đây là sổ bàn giao đầy đủ. Cho tới
> khi chốt, phần cần đọc TRƯỚC nằm ở mục "Đang ở đâu" ngay dưới.

## Đang ở đâu

- Phase 1–11 xong (Phase 11 = monitoring, 2026-08-07, xem dưới).
  **Phase 12b XONG 2026-08-14** (harness engineering, xem mục riêng).
  **731 passed / 6 slow passed / 0 skipped**; `ruff` sạch;
  `mypy` 86/86 file (xem mục "mypy chưa từng chạy hết repo" bên dưới).
  Bộ đầy đủ là HAI lệnh: `pytest && pytest -m slow`.
- **Phase 12b — harness engineering (2026-08-14).** 7 module mới:
  `monitoring/health.py` (ảnh chụp sức khoẻ lúc chạy →
  `${STATE_DIR}/health.json`, KHÁC `ops/health_check.py`),
  `monitoring/drift.py` (so hành vi với baseline Phase 7 →
  `${STATE_DIR}/drift.json`), `monitoring/daily_digest.py`
  (`logs/digest/YYYY-MM-DD.md`, 00:05 UTC),
  `tests/regression_harness.py` (~137s, `-m slow`),
  `tests/test_snapshot.py` (~8s canary), `ops/readiness_gate.py` (cổng §E),
  `ops/verify_scope.py` (CLAUDE.md #19). Cộng `.github/workflows/ci.yml`
  (CI lần đầu có trong dự án) và `docs/READINESS_GATE.md`.
  **Ba tầng hồi quy, đừng để trùng nhau:** `test_forward_golden` <1s /
  `test_snapshot` ~8s / `regression_harness` ~137s — vai trò từng tầng ghi
  trong docstring `tests/test_snapshot.py`.
- **9 bug tầng thực thi đã sửa (2026-08-08)** — bar bị lỡ + `execute`,
  `close_position(bar_timestamp)`, normalize orderLinkId, stop-loss qua
  `validate_signal`, `_requested_qty` đọc từ sàn, pre-flight số dư khả
  dụng, `round_price` có hướng, validate `log_return_1`, `FeatureCache`.
  Chi tiết + lý do từng lựa chọn: `docs/DECISIONS.md` mục 2026-08-08
  (sau). Test tái hiện: `tests/test_nine_bug_fixes.py`, 10/10 đột biến
  bị bắt. **`ops/RUNBOOK.md` có quy trình bắt buộc trước khi deploy**
  (đổi công thức orderLinkId → chỉ deploy khi không có lệnh chờ).
- **Sức khoẻ kênh cảnh báo (2026-08-08):** `AlertManager` đếm thất bại
  theo từng kênh, ghi `${STATE_DIR}/status.json` (đã .gitignore);
  3 lần thất bại liên tiếp của MỘT kênh -> `status: "degraded"`. Kênh
  file có `try` riêng, luôn được thử kể cả khi kênh từ xa nổ. Hợp đồng
  "không bao giờ raise" giữ nguyên.
- **Phân loại exception (2026-08-08):** lỗi lập trình
  (`TypeError`/`AttributeError`/`KeyError`) KHÔNG còn bị dán nhãn
  `DATA_FEED_LOST`/`API_LOST` — có `AlertType.INTERNAL_ERROR` riêng.
  Bảng rà soát đầy đủ 5 chỗ đã đổi + 4 chỗ cố ý giữ nguyên:
  `docs/DECISIONS.md` mục 2026-08-08 (sau nữa).
- **SỰ CỐ 2026-08-08: forward test đã dừng im lặng 3 ngày (08-06 → 08-08),
  ĐÃ SỬA + đã dựng canh gác.** Đọc mục riêng phía dưới TRƯỚC khi đụng
  `forward/`. Điểm quan trọng nhất: **`log.csv` đã cuộn sang `log_v2.csv`**,
  entry point là `python -m forward.runner` (KHÔNG phải `forward.logger`),
  và đọc dữ liệu phân tích phải dùng `forward.runner.load_all_bars()`.
  Xem `forward/SCHEMA.md`.
- **`tests/test_frozen_files.py` (2026-08-07, mở rộng 2026-08-08):** ghim
  SHA256 của `forward/logger.py`/`forward/config_frozen.yaml` (+
  `forward/log.csv` từ 08-08, xem mục sự cố) vào
  `tests/golden/frozen_hashes.json` — FAIL nếu MỘT TRONG HAI file đổi, dù
  vô tình hay cố ý, dù chỉ một dòng comment. Bổ sung lớp bảo vệ Ở TẦNG
  TEST SUITE cho `forward/config_frozen.yaml` (đã có hash-kiểm riêng ở
  tầng RUNTIME qua `forward/config_frozen.sha256`, không đổi) và lần đầu
  tiên bảo vệ CHÍNH `forward/logger.py` (trước đây chỉ "đóng băng" bằng
  quy ước docstring, không có gì kiểm tra tự động). Hash ghim PHẢN ÁNH
  `forward/logger.py` SAU khi thêm đoạn docstring "ĐO bars_window" (lần
  sửa duy nhất, được yêu cầu tường minh, xảy ra TRƯỚC khi file test này
  tồn tại) — từ giờ, không còn ngoại lệ nào nữa. Xác nhận bằng mutation
  (CLAUDE.md #16) trên CẢ HAI file riêng biệt (append một dòng comment
  vào từng file, xác nhận đỏ, khôi phục nguyên văn từ backup — xác nhận
  bằng hash lẫn `git diff --stat` rỗng cho cả hai). Thêm vào CLAUDE.md #15
  (7 file bắt buộc, tăng từ 6).
- **`StrategyOrchestrator.generate_signal()` thuần — ĐÃ KHOÁ bằng
  assertion, ĐÓNG (2026-08-07):**
  `tests/test_strategies.py::test_generate_signal_is_idempotent_no_hidden_state`
  (test độc lập, 3 lần gọi cùng instance cùng input) VÀ
  `tests/test_wiring_equivalence.py` (commit `645afff`, 2 lần gọi,
  `assert` inline ngay trước vòng lặp chính — dừng SỚM nếu tiền đề sai)
  — CẢ HAI đã có assertion, cả hai đã xác nhận bằng mutation (CLAUDE.md
  #16: `_call_count` rò rỉ vào `reasoning`, đỏ đúng vị trí thiết kế, revert
  sạch). Không còn việc gì mở ở mục này. (`tests/test_bars_window_sensitivity.py`
  vẫn dựa vào tiền đề này qua test độc lập ở `test_strategies.py` mà
  không có bản inline riêng — chưa từng được yêu cầu, chỉ ghi chú, không
  phải việc treo.)
- **`core/signal_generator.py::SignalGenerator` là đường nối dây thứ BA
  — rủi ro đã kiểm soát bằng `tests/test_wiring_equivalence.py`, không
  hợp nhất theo thiết kế (2026-08-07):** độc lập với
  `forward/logger.py`/`tests/test_forward_golden.py` (cả hai gọi thẳng
  HMM/strategy/trend_gate/`compose_layer_allocations`, bỏ qua class
  `SignalGenerator` hoàn toàn) — CỐ Ý không hợp nhất vì
  `forward/logger.py` đóng băng, không sửa được.
  `tests/test_wiring_equivalence.py` chạy cả ba đường trên cùng input
  (đồng bộ bằng hai `HMMRegimeEngine` train giống hệt + xác nhận từng
  bar), khẳng định `hmm_allocation`/`trend_gate_cap`/`final_allocation`
  khớp tuyệt đối — xác nhận bằng mutation (đổi `min()` -> `max()` trong
  `_apply_layer_caps()`, đỏ ngay bar đầu). Trong CLAUDE.md #15.
- **`bars_window` (`ohlcv.loc[:ts].tail(300)` ở `forward/logger.py:558` vs
  `ohlcv.loc[:ts]` không giới hạn ở golden/`test_wiring_equivalence.py`)
  — ĐO XONG, ĐÓNG (2026-08-07).** Trước đó chỉ suy luận "vô hại" (EMA/ATR
  hội tụ nhanh). Đo thật ở `tests/test_bars_window_sensitivity.py`: chạy
  công thức wiring của `forward/logger.py` hai lần độc lập trên 300 bar
  tổng hợp (đi qua đúng ranh giới nơi `.tail(300)` bắt đầu cắt thật),
  `current_allocation` tích luỹ riêng từng lần (không reset) — KHỚP 100%,
  0/300 lệch. Xác nhận test này không vô nghĩa (mutation): thu nhỏ
  `_TAIL_LOOKBACK` xuống 235 (sát ngưỡng warmup 230) — LỘ RA lệch thật
  ngay bar 230 (`cap=0.60` vs `0.30`), chứng minh test bắt được khác biệt
  thật khi nó tồn tại. Kết quả ghi vào docstring `forward/logger.py`
  (không sửa logic). Câu hỏi đóng — không cần đo lại trừ khi
  `_STRATEGY_BARS_LOOKBACK` đổi.
- Forward test chạy từ 2026-08-06, cấu hình đóng băng, launchd hằng ngày.
  Mốc đánh giá: 2026-11-06 / 2027-02-06 / 2027-08-06. Không đụng tới.
  (Gián đoạn 08-06 → 08-08, đã bù đủ, không mất bar — xem mục sự cố.)
- Cổng: `CLAUDE.md` #12 — xây tầng thực thi ở **testnet** được, **mainnet**
  bị chặn tới 2027-08-06. `main.py` KHÔNG có `--live` tự động xác nhận —
  vẫn yêu cầu gõ tay chuỗi xác nhận qua `require_live_confirmation()`
  (broker/ccxt_client.py) khi `testnet=False`.

## Phase 10 — Main loop (`main.py::run_live_loop`) — 2026-08-07

Xây theo `prompts/phase-10-main-loop.md` + `docs/Brain-Crypto-Bybit.md`
§Phase 7, điều chỉnh cho kiến trúc REST polling (không có bước "nhận bar
qua WebSocket"/"đóng WebSocket lúc tắt" — xem mục "Đổi sàn" dưới).

**Mới:**
- `main.py`: `LiveLoopState` (dataclass, ghi/đọc `state_snapshot.json`
  nguyên tử — tmp+rename), `process_one_bar()` (thuần, test được bằng
  fake, không cần mạng), `run_live_loop()` (khởi động 10 bước spec +
  vòng lặp poll vĩnh viễn), `run_train_only()` (`--train-only`, KHÔNG cần
  kết nối sàn — chỉ cần `HistoryLoader` công khai).
- `core/signal_generator.py::SignalGenerator.generate()` đổi trả về
  `SignalGeneratorResult` (thêm `regime_state`/`is_flickering`) thay vì
  `RiskDecision` trần — module này trước đó KHÔNG có caller/test nào
  trong repo, an toàn để đổi API.
- `broker/order_executor.py::restore_known_stop()` — MỚI, bắt buộc gọi
  lúc khởi động lại từ snapshot: không có nó, `modify_stop()` đầu tiên
  sau restart coi `current=None` và chấp nhận BẤT KỲ giá trị nào, kể cả
  rộng hơn stop thật trước khi crash — vi phạm CLAUDE.md #5 âm thầm.
- `config/settings.yaml`: thêm section `execution` (`limit_offset_pct`,
  `order_timeout_seconds`, `poll_interval_seconds`).
- `ops/regime-trader-crypto.service` — mẫu systemd, auto-restart
  (`Restart=always`, rate-limited), `SIGTERM` trước `SIGKILL`.
- Test mới: `tests/test_signal_generator.py` (4 test — SignalGenerator
  trước đó 0 test), `tests/test_main_loop.py` (14 test — dry-run không
  đặt lệnh, stop-loss breach, signal bị từ chối giữ nguyên state, snapshot
  round-trip/hỏng, reset ngày/tuần), `tests/test_orders.py` (+2, restore_known_stop).

**Thiết kế đáng chú ý:**
- Stop loss trên spot KHÔNG phải lệnh sàn native — bot tự theo dõi mỗi
  bar (`close_price <= tracked_stop` → `close_position()`), xem
  `broker/order_executor.py` ghi chú cũ.
- `reset_daily()` gọi MỖI bar (timeframe 1D = mỗi bar là một ngày mới);
  `reset_weekly()` chỉ khi `bar_ts.weekday() == 0` (Thứ Hai).
- Lỗi HMM ("giữ nguyên regime cũ", spec): KHÔNG bắt riêng trong
  `process_one_bar` — nếu nó raise, `run_live_loop`'s catch-all vòng
  ngoài giữ nguyên `state` của lần thành công gần nhất (chưa bị ghi đè) —
  cùng hiệu quả, không cần hai lớp try/except lồng nhau.
- `_latest_closed_bar_date()` trong `main.py` CỐ TÌNH KHÔNG import từ
  `forward/logger.py` dù logic giống hệt — `forward/` tự cô lập hoàn
  toàn (thí nghiệm tiền đăng ký 12 tháng), live loop không nên phụ thuộc
  ngược vào đó dù chỉ một hàm thuần.
- `ops/health_check.py::check_exchange_reachable/authenticated` được TÁI
  SỬ DỤNG trực tiếp làm bước 1-2 của khởi động (kết nối + xác thực) —
  không viết lại cùng logic lần hai. `--dry-run` bỏ qua bước xác thực
  (không cần đặt lệnh thật).

**Xác nhận bằng chạy thật (không chỉ unit test):**
- `python main.py --dry-run` chạy thật tới `testnet.binance.vision` —
  `exchange_reachable` OK 178ms, `InstrumentRules(BTCUSDT)` lấy đúng, vào
  tới bước train HMM thật (dừng ở đó có chủ đích — train đầy đủ
  n_candidates=[3,4,5,6,7]×n_init=10 tốn nhiều phút, không cần chạy hết
  để xác nhận pipeline đúng).
- Tạo `state/trading_halted.lock` thủ công → `python main.py --dry-run`
  thoát NGAY (exit 1, không hề gọi mạng), in đúng nội dung lock + hướng
  dẫn — đúng nghiệm thu #2 của `prompts/phase-10-main-loop.md`.
- `grep -rn "is_market_open\|market_hours" .` — không có kết quả (nghiệm
  thu #2... đánh số lại: xem file gốc, mục "không có giờ giao dịch").

**Tự kiểm chứng bằng mutation (CLAUDE.md #16):** 5 mutation trên
`process_one_bar`/`run_live_loop`/`load_state_snapshot` (bỏ qua dry_run ở
cả hai nhánh, reset_weekly gọi mọi bar, giữ allocation sai khi bị từ chối,
bỏ try/except JSON hỏng) — đúng 5 test liên quan đỏ, 9 không liên quan vẫn
xanh. Revert lại bản thật trước khi chạy full suite.

**Chưa xác nhận được (cần testnet thật, hiện bị chặn — xem dưới), KHÔNG
phải chưa xây:**
- Kill+restart QUA MẠNG THẬT rồi xác nhận khôi phục đúng (đã xác nhận
  bằng unit test `test_state_snapshot_roundtrip` + logic
  `restore_known_stop`, chưa chạy qua tiến trình `main.py` thật đầu-cuối).
- Chạy `--dry-run` liên tục 24 giờ — cần một phiên riêng ngoài phạm vi
  làm việc tương tác, không giả lập trong phiên này.
- submit_order/close_position/modify_stop thật qua mạng — cần
  `EXCHANGE_API_KEY`/`SECRET` thật (mục "Testnet đang bị chặn" dưới).

## `tests/test_forward_golden.py` — được giao lại LẦN 3 là "chưa có", VẪN đã có (2026-08-07)

Kiểm tra lại (file tồn tại, `git log` cho thấy đã commit ở `479495d`, có
trên remote, `pytest` PASS, đã có trong CLAUDE.md #15 dòng 138) — khớp y
hệt hai lần kiểm tra trước. Không có gì để xây thêm.

**Việc THẬT, mới, làm ở lần giao này — kiểm tra hồi tố Phase 10 có đổi
hành vi forward hay không** (yêu cầu cụ thể, khác hai lần trước):
`git worktree add /tmp/pre-p10 3edc6d4` (commit cha trực tiếp của `877ddc2`
— Phase 10), chạy `_run_golden_pipeline()` (hàm thật trong chính
`tests/test_forward_golden.py`, không viết lại pipeline riêng) ở CẢ HAI
cây trên cùng dữ liệu tổng hợp seed cố định, so JSON kết quả field-by-field
bằng script độc lập (không qua assert của chính test, để không tự tin
nhầm vào logic so sánh của bản thân file test).

**Kết quả: KHỚP 100%** — 60/60 bar, cả 9 field categorical/string
(`bar_index`/`regime_id`/`regime_label`/`regime_is_confirmed`/
`is_flickering`/`hmm_allocation`/`trend_gate_state`/`trend_gate_cap`/
`final_allocation`) khớp tuyệt đối ở MỌI bar, `regime_probability` lệch
đúng `0.0` (không chỉ trong dung sai — bit-for-bit giống hệt). Đối chiếu
thêm: golden baseline đã commit (`tests/golden/forward_baseline.json`)
cũng khớp 100% với cả hai lần chạy — ba nguồn (baseline đã commit,
pre-Phase-10, post-Phase-10/HEAD) đồng nhất tuyệt đối.

**Kết luận: Phase 10 KHÔNG đổi hành vi forward pipeline.** Đúng như phân
tích cấu trúc trước khi chạy thật: `git diff --stat 3edc6d4 HEAD -- core/`
chỉ có `core/signal_generator.py` đổi (25 dòng — `generate()` đổi kiểu
trả về thành `SignalGeneratorResult`), và `_run_golden_pipeline()` KHÔNG
dùng `SignalGenerator` — nó gọi thẳng
`hmm_engine`/`orchestrator`/`trend_gate`/`compose_layer_allocations`,
bỏ qua lớp bọc đó hoàn toàn. `main.py` (file mới), `broker/order_executor.py`
(chỉ thêm `restore_known_stop()`, không sửa method cũ), `config/settings.yaml`
(chỉ thêm section `execution`, không đổi giá trị `hmm`/`trend_gate`/
`strategy` mà golden test dùng) — không file nào trong ba file đó ảnh
hưởng pipeline forward. Thí nghiệm forward hiện tại (`forward/log.csv`)
**còn nguyên vẹn**, không cần regenerate golden, không cần ghi mục "thí
nghiệm kết thúc" vào `docs/DECISIONS.md` (không có gì kết thúc). Đã dọn
worktree (`git worktree remove /tmp/pre-p10`).

**Mutation (CLAUDE.md #16), làm lại lần nữa cho lần giao này:** sửa
`core/regime_strategies.py::_EMA_PERIOD` (50 → 40), golden test FAIL
đúng bar_index=153 (`hmm_allocation` lệch `0.95` → `0.60`, cùng vị trí cả
hai lần chạy), revert sạch (`git diff --stat` rỗng), full suite 227
passed lại.

## Phase 11 — Monitoring (`monitoring/`) — 2026-08-07

Xây theo `prompts/phase-11-monitoring.md` + `docs/Brain-Crypto-Bybit.md` §8.
`monitoring/logger.py`/`dashboard.py`/`alerts.py` tồn tại từ trước (scaffold
— dataclass/enum đầy đủ, mọi method `raise NotImplementedError`) — phiên
này implement thật, không viết lại scaffold.

**Xong, có test, mutation-verified (CLAUDE.md #16):**
- `monitoring/logger.py`: `get_logger()` JSONL thật (mỗi dòng MỘT object
  JSON hợp lệ, khác quy ước tạm "asctime + json" của `monitoring/watchdog.py::_log_event`
  — TODO của chính watchdog.py giờ có thể đóng, chưa làm ở phiên này),
  `RotatingFileHandler` 10MB/30 backup (xem docstring module cho lý do đây
  là proxy dung lượng, không phải lịch 30 ngày thật). Bug THẬT bắt được
  qua chính test (không phải mutation cố ý): `logging.getLogger(name)`
  dùng registry TOÀN CỤC theo tên — hai `log_dir` khác nhau cùng `name`
  cộng dồn handler trên CÙNG object. Sửa bằng dựng `logging.Logger(...)`
  trực tiếp, không qua registry.
- `monitoring/alerts.py`: `AlertManager` — rate limit 1/loại/15 phút (mặc
  định, cấu hình qua `monitoring.alert_rate_limit_seconds`) áp dụng CHUNG
  cho mọi kênh của MỘT alert; console qua `logging.StreamHandler` riêng
  (KHÔNG `print()` — nghiệm thu grep, xem dưới); Telegram (Bot API
  `sendMessage`, credential từ env); email (SMTP, tuỳ chọn); webhook (tuỳ
  chọn). `send()` cam kết không bao giờ raise — bắt RỘNG (`Exception`,
  không chỉ `requests.RequestException`) ở cả ba kênh mạng, phát hiện qua
  test tự đỏ khi mock ném `OSError` thay vì đúng loại hẹp đã đoán trước.
  Thêm `AlertType.TREND_GATE_CHANGE` — scaffold gốc thiếu, dù
  phase-11-monitoring.md liệt kê "đổi trạng thái trend gate" là một trigger
  riêng.
- `monitoring/dashboard.py`: `Dashboard` (rich) — 6 panel đúng §8.2
  (REGIME/PORTFOLIO/VỊ THẾ/SIGNAL GẦN ĐÂY/RISK/HỆ THỐNG; scaffold gốc
  thiếu hẳn field cho hai panel VỊ THẾ/SIGNAL GẦN ĐÂY, đã bổ sung vào
  `DashboardState`). "Phí tháng này" luôn hiển thị kể cả 0. `render_text()`
  (Console record=True) — dùng cho test không cần TTY và cho "chụp màn
  hình dạng text".
- Test mới: `tests/test_monitoring_logger.py` (8), `tests/test_monitoring_alerts.py`
  (18), `tests/test_monitoring_dashboard.py` (10) — cộng thêm mở rộng
  `tests/test_main_loop.py` (+12: fee tracking, `_fire_bar_alerts`,
  `_check_spread_and_alert`, wiring end-to-end).

**Wire vào `main.py` (thật, không phải để đó chưa gọi):**
- `LiveLoopState` +2 field: `cumulative_fees_paid` (str(Decimal), cộng dồn
  suốt phiên, đọc THẬT từ `OrderResult.raw_response["fee"]`/`["fees"]` —
  KHÔNG ước lượng bằng `costs.taker_fee_pct`, xem `_extract_fee_paid()`),
  `current_trend_structure` (để phát hiện đổi trend-gate-state). Cả hai có
  default — snapshot cũ (trước Phase 11) vẫn load được, xác nhận bằng test
  riêng. Bug THẬT bắt được qua test: `log_state()` ban đầu gọi TRƯỚC khi
  phí bar hiện tại được cộng vào — log trễ một bar so với hành động thật;
  sửa bằng dời lệnh gọi xuống sau khi `cumulative_fees` đã cập nhật xong.
- `process_one_bar()`: +3 tham số optional (`alert_manager`,
  `regime_state_logger`, `large_pnl_alert_pct`, mặc định `None`/`None`/`2.0`)
  — KHÔNG đổi hành vi 23 test Phase 10 đã có (không truyền, giữ nguyên).
  Khi được truyền (chỉ `run_live_loop()` truyền): ghi `regime.log` mỗi bar
  KHÔNG breach, phát `_fire_bar_alerts()` (REGIME_CHANGE, TREND_GATE_CHANGE,
  FLICKER_THRESHOLD_EXCEEDED, CIRCUIT_BREAKER, LARGE_PNL — chiều LỖ, xem
  dưới) và `_check_spread_and_alert()` (ABNORMAL_SPREAD, DATA_FEED_LOST).
- `run_live_loop()`: build `regime_state_logger`/`alert_manager`, truyền
  vào mỗi `process_one_bar()`; thêm `AlertType.HMM_RETRAINED` sau retrain
  thành công, `AlertType.API_LOST` ở catch-all vòng ngoài (rộng, không
  phân loại — lưới hứng cho mọi lỗi chưa có nhánh riêng).

**CHƯA wire — khoảng trống đã biết, không âm thầm giả định:**
- `AlertType.STABLECOIN_DEPEG`: `RiskManager.check_stablecoin_peg()` đã có
  sẵn từ trước (Phase 5/§5.4) nhưng cần một nguồn giá USDT/USD đáng tin —
  chưa chọn/xác nhận nguồn nào trên Binance spot, không bịa bằng cặp proxy
  (vd. USDC/USDT) chưa kiểm chứng.
- **[2026-08-07, sau] `AlertType.CLOCK_DRIFT` liên tục mỗi bar — XONG.**
  `ExchangeClient.get_server_time()` (`broker/base.py`, NON-abstract, mặc
  định `NotImplementedError` — cố ý không `@abstractmethod` để không buộc
  sửa `broker/bybit_client.py` deprecated) + `CCXTClient.get_server_time()`
  (`exchange.fetch_time()` thật) + `monitoring/clock.py::measure_clock_drift()`
  (hiệu chỉnh round-trip kiểu NTP, median-of-3, **KHÔNG** dùng công thức
  ngây thơ `server - now()` mà `ops/health_check.py::check_exchange_reachable`
  vẫn dùng — check đó GIỮ NGUYÊN, chỉ còn vai trò heads-up WARN sớm,
  KHÔNG còn là nguồn quyết định chính cho ngưỡng dừng lệnh). Wire: khởi
  động (`run_live_loop`, FAIL cứng exit 1 nếu |drift| > 2500ms) + mỗi bar
  (`process_one_bar`, qua `_check_clock_drift()` — log MỌI bar vào
  `regime.log` (`event: clock_check`), cảnh báo ở 1000ms, DỪNG gửi lệnh
  mới ở 2500ms giữ nguyên vị thế/stop hiện có — xem docstring
  `process_one_bar` cho đánh đổi CÓ CHỦ Ý: một breach stop-loss thật đúng
  bar bị halt sẽ KHÔNG được enforce). Ngưỡng trong
  `config/settings.yaml: monitoring.clock_drift_alert_ms/clock_drift_halt_ms`.
  `ops/RUNBOOK.md` có mục CLOCK_DRIFT đầy đủ (triệu chứng giống hệt "xác
  thực thất bại", nguyên nhân, cách sửa macOS/Linux). TUYỆT ĐỐI KHÔNG bật
  ccxt `adjustForTimeDifference` — lý do đầy đủ trong docstring
  `monitoring/clock.py`. Test mới: `tests/test_monitoring_clock.py` (7,
  mutation-verified: bỏ hiệu chỉnh round_trip/2, 2 test đỏ), +9 trong
  `tests/test_main_loop.py` (ngưỡng alert/halt, mutation-verified: ép
  `halted=False` cố định, 2 test halt-behavior đỏ), +2 trong
  `tests/test_ccxt_client.py`, +1 `tests/test_bybit_client.py` (default
  ABC raise). Xem docs/DECISIONS.md mục cùng ngày.
- `LARGE_PNL` chỉ phát hiện chiều LỖ (đọc `CircuitBreaker.check().daily_dd`,
  vốn chỉ đo drawdown) — chưa có theo dõi equity TĂNG bar-over-bar để phát
  hiện P&L DƯƠNG lớn bất thường.
- **[2026-08-07, sau] Schema `ws_connected`/`ws_last_message_seconds_ago` đã
  SỬA** — hai field đó mô tả kiến trúc WebSocket không còn tồn tại (hệ
  thống đã là REST polling từ đợt đổi sàn Bybit -> Binance). Thay bằng
  `poll_latency_ms`/`last_poll_at` (persist trong `LiveLoopState`/
  `state_snapshot.json`, cập nhật mỗi lần `run_live_loop()` thật sự gọi
  `history_loader.load()` — không phải mỗi lần lặp vòng poll) và
  `bars_behind` (`main.py::compute_bars_behind()`, THUẦN, tính lại mỗi lần
  gọi từ `last_processed_bar` + đồng hồ hiện tại, CỐ Ý không persist —
  một giá trị lưu sẵn sẽ đứng yên "0" đúng lúc tiến trình chính đã chết).
  `monitoring/dashboard.py::DashboardState` + `_system_panel` đã cập nhật,
  mutation-verified (CLAUDE.md #16). Xem docs/DECISIONS.md mục cùng ngày.
- `main.py --dashboard` VẪN `raise NotImplementedError` — KHÔNG đổi. Sửa
  schema WS ở trên gỡ MỘT trong ba lý do chặn đã ghi trước đây; còn lại
  HAI: `regime_probability` chính xác tại thời điểm gần nhất và chi tiết
  cửa sổ flicker (`flicker_count`/`flicker_window`/`stability_bars`) vẫn
  KHÔNG được `LiveLoopState`/`state_snapshot.json` lưu. Quyết định CÓ CHỦ
  Ý: không fabricate giá trị placeholder cho hai nhóm field này chỉ để
  `--dashboard` "chạy được" — cần thiết kế riêng (lưu thêm gì vào
  snapshot, hay tính lại từ đầu mỗi lần render) trước khi wire.

**KHÔNG xác nhận được (cần testnet thật, đang bị chặn — xem mục dưới):**
- Nghiệm thu "Dashboard chạy được với dữ liệu thật từ testnet, chụp lại
  màn hình dạng text" — CHƯA làm được (phụ thuộc mục `--dashboard` ở trên,
  và phụ thuộc testnet).
- Nghiệm thu "Kích hoạt thủ công từng loại alert, xác nhận nhận được trên
  Telegram" — `AlertManager._send_telegram()` có test mock đầy đủ (payload
  đúng, không raise khi lỗi mạng, không log token) nhưng CHƯA gửi được một
  tin nhắn Telegram THẬT (chưa có `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`
  thật để thử — không phải bị chặn bởi testnet, độc lập với Binance, có
  thể làm bất cứ lúc nào có token thật).
- `grep -rn "print(" monitoring/ core/ broker/`: có kết quả, nhưng KHÔNG
  phải vi phạm — đọc kỹ từng dòng: (1) nhắc tới chuỗi `print(` trong
  DOCSTRING của `monitoring/alerts.py`/`logger.py`/`watchdog.py` (mô tả
  quy tắc, không phải lời gọi); (2) `monitoring/dashboard.py`:
  `self.console.print(...)`/`capture.print(...)` — method `rich.console.Console.print()`,
  không phải hàm `print()` built-in, đúng cách rich vẽ ra terminal; (3)
  `broker/base.py:51`/`broker/bybit_client.py:182` — `print("⚠️ LIVE
  TRADING...")` trong `require_live_confirmation()`, CÓ TỪ TRƯỚC Phase 11
  (không phải tôi thêm), lời nhắc tương tác cho người gõ xác nhận mainnet,
  cùng tinh thần `ops/health_check.py::main()` in kết quả CLI. Không tự ý
  sửa hai chỗ pre-existing này trong phiên này — ngoài phạm vi Phase 11.

## Git remote — đã đổi tài khoản, ĐÃ GIẢI QUYẾT (2026-08-07)

`origin` đổi từ `tuananh12022/roi-trading-bot` (SSH) sang
`peach3tiger/roi-trading-bot` (HTTPS, credential trong keychain macOS) —
không phải tôi đổi, đã xảy ra giữa phiên. Từng gặp `Permission denied
(publickey)` khi remote còn ở dạng SSH trỏ tài khoản mới (key SSH cục bộ
không được tài khoản đó cấp quyền). Đã đổi URL sang HTTPS theo hướng dẫn
trực tiếp từ người dùng — `git push origin main` giờ chạy được, không cần
prompt (keychain tự cấp credential). Toàn bộ commit của phiên này (tới
Phase 10) đã có trên remote, xác nhận bằng `git push` trả "Everything
up-to-date".

## Testnet đang bị chặn — KHÔNG PHẢI lỗi Binance, KHÔNG PHẢI lỗi code

Chặn ở tầng tài khoản GitHub (khác — hoặc liên quan tới — việc đổi tài
khoản git ở trên, chưa xác nhận có cùng nguyên nhân). Đã xác nhận bằng
gọi thật: `exchange_reachable` OK (mạng/API Binance testnet sống bình
thường, 155-178ms qua nhiều lần chạy), vấn đề nằm ngoài cả hai lớp (sàn,
code). Không debug thêm ở hướng "sửa CCXTClient"/"sửa health_check"/"sửa
main.py" cho việc này — không phải chỗ hỏng.

## SỰ CỐ 2026-08-08 — forward test dừng im lặng 3 ngày, đã sửa

Chi tiết đầy đủ: `docs/DECISIONS.md` mục 2026-08-08. `forward/SCHEMA.md`
là tài liệu vận hành. Tóm tắt để không lặp lại:

**Chuyện gì.** `warning_count` được thêm vào `_CSV_FIELDNAMES` của
`forward/logger.py` (file ĐÓNG BĂNG) lúc 08-06T13:10, 7 giờ sau khi bar
đầu tiên đã ghi. `append_row()` chỉ ghi header khi file chưa tồn tại, nên
`log.csv` giữ header 31 cột trong khi dòng mới ghi 32 cột →
`read_existing_log()` chết mọi lần chạy. launchd vẫn chạy đều, exit 1 mỗi
lần, **không có gì báo** trong 3 ngày. Phát hiện tình cờ.

Chẩn đoán ban đầu ("launchd chưa nạp được") **sai** — `launchctl print`
cho thấy `runs = 1, last exit code = 1`. Đọc `launchd.err.log` là bước
tìm ra nguyên nhân thật.

**Đã xử lý bằng cuộn file, không sửa file cũ:**

- `forward/log.csv` = schema v1 (31 cột, 1 bar 2026-08-05), **ĐÃ ĐÓNG**,
  giữ nguyên byte-for-byte, SHA256 ghim trong `frozen_hashes.json`.
- `forward/log_v2.csv` = schema v2 (32 cột, từ 2026-08-06), **đang chạy**.
- `forward/runner.py` = entry point mới, trỏ `logger._LOG_PATH` sang file
  đang hoạt động. **`forward/logger.py` KHÔNG đổi một byte** — hash vẫn
  `20a9474d…`. LaunchAgent chạy `python -m forward.runner`.
- Đọc dữ liệu phân tích: **`load_all_bars()`**, không đọc file trực tiếp
  (mất bar v1). `warning_count` của v1 là `NaN`, không phải `0`.
- `tests/test_forward_log_append_only.py` (13 test) kiểm FILE THẬT trên
  đĩa cho cả hai file — khoảng trống cũ là mọi test chỉ kiểm HÀM trên
  `tmp_path`, nên trạng thái header-lệch-dòng không ai thấy.
- `monitoring/forward_watchdog.py` + LaunchAgent riêng 09:00 — canh độ
  tươi, ngưỡng > 2 ngày, tín hiệu là `max(date)` (không phải mtime/số
  dòng). Hỏi `runner.ACTIVE_LOG_PATH`, không hardcode tên file.

**Bài học đã đưa vào quy trình:** mọi thay đổi vào `forward/` — kể cả chỉ
thêm một cột log — phải có entry `DECISIONS.md` **tại thời điểm thay
đổi**. Quy trình cuộn schema: 5 bước trong `forward/SCHEMA.md`.

## Bất biến/quy trình MỚI trong CLAUDE.md (2026-08-14)

Hai bất biến mới, một bổ sung, một cổng cơ giới hoá — KHÔNG phải năm cái
mới: **#16 và #17 đã có từ trước phiên này** (commit `cb13932`).

| | Nội dung | Trạng thái |
|---|---|---|
| #16 | Đột biến trước khi tin một phép kiểm mới | **CÓ TỪ TRƯỚC.** Bổ sung 2026-08-08: bắt buộc commit/`git stash` trước khi chạy kịch bản đột biến; timeout ≤60s mỗi bước; khôi phục trong `finally` của TỪNG bước; assert không còn dấu vết |
| #17 | Không đọc exit code sau pipe | **CÓ TỪ TRƯỚC**, không đổi |
| **#18** | **Không đặt ngưỡng trước khi đo phân phối nền** | MỚI. Quy trình 4 bước: trượt cửa sổ cùng kích thước qua baseline → đo phân phối → đặt theo phân vị → BÁO CÁO tỷ lệ báo động giả |
| **#19** | **Mọi khẳng định "sạch" phải kèm PHẠM VI đã kiểm** | MỚI. Cơ giới hoá bằng `ops/verify_scope.py` |
| **§E** | **`pytest -m slow` BẮT BUỘC khi diff chạm `core/` hoặc `backtest/`** | MỚI. `ops/readiness_gate.py` + CI. Bảng cổng: `docs/READINESS_GATE.md` |

**Cổng §E hoạt động thế nào:** biên lai `.slow_receipt.json` (gitignore)
sinh TỰ ĐỘNG từ `tests/conftest.py` sau một phiên `-m slow` xanh hoàn
toàn; nó ghi **SHA256 nội dung `core/`+`backtest/`**, không phải commit
SHA — chạy slow xong rồi sửa tiếp `core/` mà chưa commit sẽ cho biên lai
"khớp HEAD" nhưng vô giá trị. Chạy tay:
`pytest -m slow && python ops/readiness_gate.py --base origin/main`.

---

## Ba ngưỡng đã hiệu chỉnh bằng ĐO (2026-08-14) — đọc trước khi đụng cảnh báo

Áp dụng CLAUDE.md #18. Chi tiết + bảng phân vị: `docs/DECISIONS.md` mục
"ĐO #1/#2/#3" và "Phân loại 8 ngưỡng còn lại".

| Ngưỡng | Cũ | Mới | Báo động giả đo được |
|---|---|---|---|
| `monitoring.large_pnl_alert_pct` | 2.0 (p82.4) | **2.93** (p90) | 32.0 → 18.2 lần/năm |
| `drift.WARNING_TREND_LEN` | 3 | **4** | 1 lần mỗi 6.0 tuần → mỗi 24 tuần |
| `drift` cửa sổ phân bố allocation | 30 bar | **`ALLOCATION_WINDOW_DAYS` = 365** | 99.7% → 3.22% |

**Điều quan trọng nhất phải nhớ về drift:** ở cửa sổ 30–182 bar, chỉ số
phân bố allocation **KHÔNG phân biệt được một bot hỏng hoàn toàn với hoạt
động bình thường** (đo trực tiếp, không suy luận). Đừng đọc "drift im
lặng" thành "hành vi khớp baseline". Nó chỉ có sức phát hiện từ 365 bar,
tức là forward test phải chạy tới 2027-08-06 mới có dữ liệu đầy đủ cho chỉ
số này.

Còn 5 ngưỡng chưa đo, đã phân loại: 2 có căn cứ từ ràng buộc sàn
(`clock_drift_*`), 1 hoãn vì chưa có baseline
(`unfilled_order_degraded_seconds` — backtest KHÔNG mô phỏng độ trễ khớp
nên không có gì để trượt cửa sổ), 2 là lựa chọn vận hành không có sự thật
nền (`alert_rate_limit_seconds`, `_DEFAULT_DEGRADED_AFTER`).

---

## `mypy .` chưa từng chạy hết repo cho tới 2026-08-14

Thiếu `tests/__init__.py` trong khi ba file test dùng
`from tests.test_main_loop import ...` → mypy dừng ở
`"Source file found twice under different module names"` **sau khi kiểm 0
file**. Xác minh có từ trước Phase 12b (checkout `5c49fa5`, `bdae60d`).
Nghĩa là mọi lần "mypy sạch" trước đây nghĩa là "mypy chưa kiểm được gì" —
lỗi XÁC MINH lần thứ tư trong dự án (xem CLAUDE.md #16).

Sau khi thêm `tests/__init__.py`: mypy lộ 15 lỗi tiềm ẩn, **không cái nào
là bug thật** (9 lỗi `warn_return_any` từ stub pandas/yaml → tắt riêng cho
`tests.*`; 3 annotation thiếu → thêm; 3 `# type: ignore[...]` có địa chỉ
kèm lý do). `disallow_untyped_defs` VẪN bật cho `tests/`.

---

## Mục nghiệm thu ĐẠT một cách RỖNG — đã phát hiện, chưa đóng

`ops/verify_scope.py` rà mọi công cụ trong danh sách nghiệm thu và in
PHẠM VI thay vì chỉ "sạch". Nó tìm ra:

- **`grep -rn "order_executor|submit_order" ops/shadow_runner.py`
  (nghiệm thu Phase 12c) hiện ĐẠT vì `ops/shadow_runner.py` KHÔNG TỒN
  TẠI.** Nó chứng minh file không có, không chứng minh gì về nội dung.
  Đóng khi xây Phase 12c.
- `pytest` mặc định thu 731/737 test — 6 test `slow` bị loại bởi
  `addopts = "-m 'not slow'"`. "Toàn bộ xanh" từ một lệnh là **đúng một
  nửa**.

Chạy: `python ops/verify_scope.py` (thoát khác 0 nếu một đường dẫn nghiệm
thu không tồn tại).

---

## Việc còn treo, theo thứ tự ưu tiên

0. **`.env` có `TELEGRAM_BOT_TOKEN=`/`TELEGRAM_CHAT_ID=` GIÁ TRỊ RỖNG** →
   watchdog phát hiện đúng nhưng **không gửi được cảnh báo nào**, chỉ ghi
   vào `forward/watchdog.err.log` (file không ai đọc). Đây là điểm mù còn
   lại của chính cơ chế vừa dựng để chống điểm mù. `telegram_configured`
   xuất hiện trong `watchdog.out.log` mỗi ngày để nhìn thấy được, nhưng
   **chưa đóng cho tới khi điền credential thật**.
1. Điền `EXCHANGE_API_KEY`/`EXCHANGE_API_SECRET` + nghiệm thu qua mạng
   thật (CCXTClient submit_order/cancel_order/idempotency; main.py
   kill+restart thật; `--dry-run` 24h; dashboard/Telegram thật, xem mục
   Phase 11 ở trên) — **TẠM DỪNG**, chờ testnet hết bị chặn ở tầng tài
   khoản GitHub.
2. `main.py --dashboard` — thiết kế dữ liệu cần lưu thêm vào
   `LiveLoopState`/`state_snapshot.json` (hoặc tính lại từ đầu mỗi lần
   render) trước khi wire `Dashboard` thật vào CLI, xem mục Phase 11 "CHƯA
   wire" ở trên cho chi tiết field còn thiếu.
3. `AlertType.STABLECOIN_DEPEG`/`CLOCK_DRIFT` liên tục mỗi bar — cần chọn
   nguồn giá USDT/USD và thêm `ExchangeClient.get_server_time()` (xem mục
   Phase 11 ở trên).
4. **Phase 12b — XONG 2026-08-14.** Phase 12c
   (`prompts/phase-12c-shadow-deploy.md`) — đã có trong `prompts/`, chưa
   bắt đầu xây. Xây nó cũng đóng luôn mục nghiệm thu ĐẠT-một-cách-rỗng ở
   trên.
5. `prompts/phase-12d-operational-safety.md` — **được nhắc tới hai lần
   nhưng KHÔNG TỒN TẠI** trong `prompts/`, không có trong lịch sử git,
   không được file nào tham chiếu. Cần bản gốc trước khi thực hiện.
6. `.github/workflows/ci.yml` **chưa từng chạy trên GitHub thật** — viết
   xong ở phiên 2026-08-14 nhưng không có cách kiểm chứng tại máy. Lần
   push tiếp theo là lần đầu nó chạy; đọc kết quả trước khi tin.

## Việc tiếp theo

[testnet hết bị chặn] -> nghiệm thu CCXTClient + main.py + dashboard/Telegram
qua mạng thật -> Phase 12c (shadow deploy).

Phase 12b đã xong và KHÔNG phụ thuộc testnet — đó là lý do nó chạy trước
trong lúc testnet còn bị chặn.

## Quy tắc đã học, không lặp lại

- Mọi số đo thị trường lấy từ **testnet không dùng để hiệu chỉnh tham số**.
  Thanh khoản testnet là nhân tạo.
- Không bao giờ log giá trị key/secret, kể cả một phần.
- Không bao giờ hai tiến trình cùng khả năng đặt lệnh trên một tài khoản.
- Khả năng truy cập theo khu vực/tài khoản có thể chặn bất kỳ lớp nào
  (sàn — Bybit; hạ tầng — GitHub) bất kỳ lúc nào, không cảnh báo trước.
  Khi bị chặn, xác định ĐÚNG lớp bị chặn trước khi debug — chuyển sang
  việc không phụ thuộc lớp đó, quay lại khi hết chặn.
- Trước khi "xây lại" một file bị báo là thiếu/chưa có: kiểm tra thật
  (file tồn tại? đã commit? có trên remote? test pass?) rồi mới tin.
- Thư viện ngoài có thể đổi hành vi giữa các phiên bản theo cách âm thầm
  đúng-ngữ-pháp-sai-ngữ-nghĩa (hmmlearn's `covars_` luôn trả full matrix
  bất kể `covariance_type`) — viết test đọc lại GIÁ TRỊ THẬT từ một lần
  fit/gọi thật, không chỉ test "không crash", là cách duy nhất bắt được.
- Với file **append-only**, đổi schema không phải thay đổi tương thích
  ngược — nó là thay đổi **phá vỡ**, và nó phá ở lần **ĐỌC** tiếp theo,
  không phải lần ghi. Thêm cột vào một log đã bắt đầu thì file cũ không
  bao giờ học được header mới. Cuộn sang file mới, đừng sửa file cũ.
- Một job đã lên lịch **chạy đều** không có nghĩa là nó **chạy được**.
  `launchctl print` có `runs`/`last exit code`; đọc chúng và đọc file
  stderr trước khi kết luận job "chưa được nạp". Job không có
  `StandardErrorPath` thì lỗi không để lại dấu vết nào — đó là cách sự cố
  này ẩn được 3 ngày.
- "Parse không ném lỗi" ≠ "parse đúng". pandas im lặng lấy cột đầu làm
  index khi mọi dòng dư đúng một trường so với header — mọi tín hiệu
  (số dòng, kiểu dữ liệu, không NaN bất thường) đều trông lành lặn trong
  khi dữ liệu đã lệch ô. Ghim danh sách CỘT, không chỉ bọc try/except.
- Restart tiến trình là nơi bất biến dễ vỡ nhất trong im lặng nhất
  (`modify_stop()` sau restart không biết stop cũ nếu không nạp lại tường
  minh — CLAUDE.md #5 có thể bị vi phạm mà không có exception nào báo).
  Mọi trạng thái trong bộ nhớ ảnh hưởng tới một bất biến an toàn PHẢI có
  đường khôi phục tường minh sau restart, không được ngầm định "restart =
  trạng thái sạch".
