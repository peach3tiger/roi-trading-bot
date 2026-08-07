# STATE — bàn giao trạng thái

Đọc file này đầu mỗi phiên. Tối đa một trang. Chi tiết ở `DECISIONS.md` và
`VALIDATION_REPORT.md`. Cập nhật ở cuối mỗi phase, ghi đè, không phụ lục thêm.

## Đang ở đâu

- Phase 1–10 xong (Phase 10 = main loop, 2026-08-07, xem dưới). 230 passed
  / 0 skipped.
- **`StrategyOrchestrator.generate_signal()` thuần — khoá bằng assertion,
  không chỉ đọc code (2026-08-07):**
  `tests/test_strategies.py::test_generate_signal_is_idempotent_no_hidden_state`
  gọi 3 lần trên cùng instance, cùng input, khẳng định `Signal` trả về
  bằng nhau tuyệt đối. `tests/test_wiring_equivalence.py`/
  `tests/test_bars_window_sensitivity.py` cả hai dựa vào giả định này khi
  gọi `generate_signal()` nhiều lần — giờ có test giữ giả định đó thay vì
  phải đọc lại code mỗi lần nghi ngờ. Xác nhận bằng mutation (CLAUDE.md
  #16): thêm `_call_count` rò rỉ vào `reasoning` — đỏ ngay lần gọi thứ
  hai, revert sạch.
- **Khoảng trống đã biết — GIẢM NHẸ, chưa đóng hẳn (2026-08-07):**
  `core/signal_generator.py::SignalGenerator` (dùng bởi
  `main.py::run_live_loop`, Phase 10) là đường nối dây thứ BA, độc lập với
  `forward/logger.py`/`tests/test_forward_golden.py` (cả hai gọi thẳng
  HMM/strategy/trend_gate/`compose_layer_allocations`, bỏ qua class
  `SignalGenerator` hoàn toàn). Ba đường này CỐ TÌNH không hợp nhất
  (`forward/logger.py` đóng băng, không sửa được) — thêm
  `tests/test_wiring_equivalence.py` để BẢO ĐẢM không trôi lệch thay vì
  hợp nhất: chạy cả ba đường trên cùng input (đồng bộ bằng hai
  `HMMRegimeEngine` train giống hệt + xác nhận từng bar, không giả định
  suông), khẳng định `hmm_allocation`/`trend_gate_cap`/`final_allocation`
  khớp tuyệt đối. Xác nhận bằng mutation (CLAUDE.md #16): đổi `min()` ->
  `max()` trong `SignalGenerator._apply_layer_caps()`, test đỏ ngay bar
  đầu tiên, revert sạch. Đã thêm vào CLAUDE.md #15.
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

## Việc còn treo, theo thứ tự ưu tiên

1. Copy `phase-12b-harness-engineering.md` và `phase-12c-shadow-deploy.md`
   vào `prompts/` (đã soạn, chưa có trong repo).
2. Điền `EXCHANGE_API_KEY`/`EXCHANGE_API_SECRET` + nghiệm thu qua mạng
   thật (CCXTClient submit_order/cancel_order/idempotency; main.py
   kill+restart thật; `--dry-run` 24h) — **TẠM DỪNG**, chờ testnet hết bị
   chặn ở tầng tài khoản GitHub.
3. Phase 11 (monitoring/dashboard, `--dashboard` hiện raise
   `NotImplementedError` trong `main.py`).

## Việc tiếp theo

[testnet hết bị chặn] -> nghiệm thu CCXTClient + main.py qua mạng thật ->
Phase 11 (monitoring/dashboard).

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
- Restart tiến trình là nơi bất biến dễ vỡ nhất trong im lặng nhất
  (`modify_stop()` sau restart không biết stop cũ nếu không nạp lại tường
  minh — CLAUDE.md #5 có thể bị vi phạm mà không có exception nào báo).
  Mọi trạng thái trong bộ nhớ ảnh hưởng tới một bất biến an toàn PHẢI có
  đường khôi phục tường minh sau restart, không được ngầm định "restart =
  trạng thái sạch".
