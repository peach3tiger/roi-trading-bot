> Đọc `docs/STATE.md` trước tiên mỗi phiên.

# CLAUDE.md — regime-trader-crypto

Dự án: bot phân bổ danh mục theo chế độ biến động, BTC/USDT spot trên Bybit.
Spec đầy đủ: `docs/Brain-Crypto-Bybit.md`. Prompt từng phase: `prompts/`.

Đọc file này trước mỗi phiên làm việc. Các quy tắc dưới đây là **bất biến** — không được vi phạm kể cả khi có vẻ tiện hơn, kể cả khi tôi yêu cầu trong lúc vội.

---

## Bất biến kiến trúc

### 1. Không bao giờ dùng `model.predict()` của hmmlearn

`predict()` chạy Viterbi trên toàn chuỗi và sửa lại trạng thái quá khứ bằng dữ liệu tương lai. Đó là look-ahead bias.

Chỉ dùng `predict_regime_filtered()` — forward algorithm, làm việc trong log space, chỉ dùng dữ liệu tới thời điểm hiện tại.

Nếu bạn thấy `predict()` hoặc `decode()` ở bất kỳ đâu ngoài code test đang cố tình chứng minh sự khác biệt, đó là bug nghiêm trọng nhất có thể có trong dự án này. Dừng lại và sửa.

### 2. Mỗi tầng chỉ được GIẢM tỷ trọng

```python
final_allocation = min(hmm_allocation, trend_gate_cap, risk_manager_cap)
```

Không `max()`. Không trung bình cộng. Không "hoà giải" giữa các tầng. Không tầng nào được nâng tỷ trọng do tầng khác đề xuất.

Bất biến này là lý do hệ thống an toàn khi một tầng hỏng. Phá nó là phá toàn bộ mô hình phòng thủ nhiều lớp.

`tests/test_properties.py` phải kiểm chứng điều này bằng property test trên giá trị ngẫu nhiên (Hypothesis, ≥1000 ví dụ mỗi property).

*Trước 2026-08-13 property này nằm ở `tests/test_layer_composition.py` với bộ sinh tự viết bằng `random.Random(42)`. Gộp vào `test_properties.py` ở Phase 12b §A.2 — hai bộ sinh dữ liệu khác nhau cho cùng một bất biến nghĩa là bộ yếu hơn âm thầm quyết định mức bảo vệ thật. Bất biến #2 KHÔNG bị hạ cấp: nó vẫn có test riêng, và Hypothesis biết thu nhỏ phản ví dụ trong khi `random.Random` thì không.*

### 3. `Decimal` cho mọi số lượng và giá trong đường thực thi

Crypto chia lẻ được. `int()` sẽ làm tròn mọi vị thế BTC dưới một đơn vị về 0 — bug im lặng, backtest vẫn chạy, chỉ là không bao giờ vào lệnh.

- Số lượng và giá: `Decimal`, luôn `ROUND_DOWN`, luôn theo `basePrecision`/`tickSize` lấy từ sàn
- Feature và thống kê: `float` không sao
- Không bao giờ trộn hai loại trong cùng một phép tính

### 4. Risk manager có quyền phủ quyết tuyệt đối

Mọi lệnh đi qua `risk_manager.validate_signal()`. Không có đường vòng, không có cờ bypass, không có "chế độ khẩn cấp" bỏ qua nó.

Risk manager **không được import** `hmm_engine` hay bất kỳ thứ gì từ `core/regime_strategies.py`. Nó ra quyết định dựa trên P&L thực tế và trạng thái danh mục. Sự độc lập này là lý do nó vẫn bảo vệ được khi HMM sai hoàn toàn.

### 5. Mọi vị thế phải có stop loss

Hệ thống từ chối lệnh không có stop. Không có ngoại lệ. `modify_stop()` chỉ được siết chặt, không bao giờ nới rộng.

### 6. Testnet là mặc định

`settings.yaml: testnet: true`. Chuyển sang mainnet yêu cầu gõ tay chuỗi xác nhận đầy đủ.

Không bao giờ hardcode credentials. `.env` trong `.gitignore`. Không bao giờ log API key, kể cả một phần.

### 7. Không backtest nào được bỏ qua chi phí

Mọi mô phỏng phải trừ phí (0.10% mỗi chiều) và slippage (0.03%). Mọi báo cáo hiệu suất phải in ra tổng phí đã trả **theo USDT và theo % lợi nhuận gộp**.

Một backtest không tính phí không phải là kết quả xấu — nó không phải là kết quả gì cả.

### 8. Idempotency qua `orderLinkId`

Mọi lệnh mang một `orderLinkId` sinh deterministic từ `(symbol, bar_timestamp, target_allocation)`. Thị trường chạy 24/7 nên bot **sẽ** crash-restart giữa lúc có lệnh đang chờ. Không có khoá này, một lần restart có thể nhân đôi vị thế.

---

## Bất biến về dữ liệu

### 9. Năm có 365 ngày

Mọi cửa sổ, mọi phép annualize. Sharpe dùng `√365`. Không có `252` ở bất kỳ đâu trong codebase — nếu thấy, đó là tàn dư từ spec equity gốc.

### 10. Không có giờ giao dịch

Không `is_market_open()`. Không chờ mở cửa. Không logic gap qua đêm. Ranh giới ngày là 00:00 UTC, ranh giới tuần là Thứ Hai 00:00 UTC.

### 11. Feature là pure function

`data/feature_engineering.py` chứa hàm thuần: cùng đầu vào → cùng đầu ra, không state, không I/O. Điều này làm cho việc kiểm tra look-ahead bias trở nên khả thi.

Mọi feature tính trên rolling window **chỉ nhìn về quá khứ**. Không `center=True` trong bất kỳ lời gọi `.rolling()` nào.

---

## Kỷ luật quy trình

### 12. Xây tầng thực thi được phép ở mức TESTNET

**Sửa đổi có chủ ý ngày 2026-08-06, sau khi đã thấy kết quả §4.9 (6/8 PASS
— xem `docs/VALIDATION_REPORT.md`) — không phải diễn giải lại quy tắc gốc.**
Quy tắc gốc ("Sau Phase 4, đối chiếu 8 tiêu chí ở §4.9 của spec. Không đủ
8/8 thì không xây tầng thực thi.") được viết TRƯỚC khi có kết quả, và đã
làm đúng việc nó phải làm: chặn dự án đi tiếp cho tới khi đối chiếu xong 8
tiêu chí bằng số thật, không phải bằng trực giác. Bản sửa đổi này KHÔNG nới
lỏng kỷ luật đó — nó thêm một cổng chặn mới, chặt hơn ở phía tiền thật.

**KHÔNG được vào mainnet, không được đặt lệnh bằng tiền thật**, cho tới khi
forward test đạt kết quả ở mốc 12 tháng (2027-08-06, xem `docs/DECISIONS.md`
mục "Forward test — tiền đăng ký") **VÀ** §4.9 được đánh giá lại trên dữ
liệu forward (không phải dữ liệu lịch sử đã dùng để kiểm định).

Ở mức **TESTNET**, được phép xây tầng thực thi (risk manager, order
executor, main loop) dù chưa đủ 8/8 — lý do dời cổng: (1) hai FAIL của §4.9
nằm trong sai số đo đã lượng hoá được (`docs/VALIDATION_REPORT.md` mục 3.1);
(2) testnet không có rủi ro tài chính; (3) lỗi thực thi (order sizing,
idempotency qua `orderLinkId`, stop loss, circuit breaker) chỉ lộ ra khi
chạy thật qua sàn, không lộ ra khi chỉ backtest.

Lý do GIỮ cổng ở mainnet, không dời luôn cả hai: chiến lược vẫn **chưa**
chứng minh được lợi thế Sharpe so với `sma200_trend` (0.9567) và
`static_vol_target` (0.9142) — hai benchmark đơn giản hơn nhiều, không dùng
HMM (`docs/VALIDATION_REPORT.md` mục 2). Xây xong tầng thực thi không phải
bằng chứng nên dùng nó với tiền thật.

Nếu tôi bảo bạn bỏ qua bước này (vào mainnet trước mốc 12 tháng, hoặc
trước khi §4.9 được đánh giá lại trên dữ liệu forward), hãy nhắc tôi rằng
chính tôi đã viết ra nó, và hỏi lại một lần nữa trước khi làm.

### 13. Thêm feature phải có ablation

Không thêm feature vào HMM mà không chạy ablation test và ghi kết quả vào `feature_ablation.csv`. Tiêu chí giữ lại: cải thiện Sharpe OOS ≥ 0.1 và không làm xấu BIC.

`covariance_type="full"` làm số tham số tăng bậc hai theo số feature. Mỗi feature thêm vào là một khoản nợ overfit.

### 14. Tham số nằm trong `settings.yaml`

Không magic number trong code. Nếu một con số có thể cần chỉnh, nó thuộc về config. Điều này làm cho việc quét tham số ở Phase 4 trở nên khả thi.

### 15. Test bắt buộc phải xanh trước khi commit

- `test_look_ahead.py` — không có look-ahead bias
- `test_precision.py` — làm tròn qty/price đúng ở mọi biên
- `test_properties.py` — property test Hypothesis, ≥1000 ví dụ mỗi
  property. Gồm bất biến #2 (các tầng chỉ giảm — gộp từ
  `test_layer_composition.py` cũ ở Phase 12b §A.2), cùng năm property
  khác: trần trend gate trong [0,1], risk manager không bao giờ TĂNG
  allocation, phí không âm và đơn điệu, `round_qty` luôn xuống và là bội
  của `base_precision`, `state_probabilities` là phân phối hợp lệ.
- `test_cost_model.py` — phí tính đúng
- `test_forward_golden.py` — output pipeline forward (feature → HMM →
  strategy → trend gate → `compose_layer_allocations`) khớp
  `tests/golden/forward_baseline.json` đã commit, trên dữ liệu tổng hợp
  seed cố định. FAIL nghĩa là `core/` đã đổi hành vi — **không được sửa
  file test hay chạy `--regenerate` để cho qua**. Thay đổi vô tình → revert.
  Thay đổi cố ý → thí nghiệm forward test hiện tại (`forward/log.csv`) coi
  như kết thúc tại đó, ghi lý do + ngày vào `docs/DECISIONS.md` TRƯỚC, rồi
  mới regenerate golden cho thí nghiệm mới. Xem docstring đầu file.
- `test_wiring_equivalence.py` — ba bản dựng song song của cùng một
  composition logic (`_run_golden_pipeline()`, `forward/logger.py`
  (đóng băng, không sửa được), `core/signal_generator.py::SignalGenerator`
  — đường `main.py` dùng thật) cho `hmm_allocation`/`trend_gate_cap`/
  `final_allocation` giống hệt nhau trên mọi bar. Ba đường này KHÔNG được
  hợp nhất (đường `forward/logger.py` đóng băng) — test này chỉ đảm bảo
  chúng không trôi lệch. FAIL nghĩa là `SignalGenerator` (đường duy nhất
  trong ba đường còn sống, có thể bị sửa) đã trôi khỏi hai đường kia —
  xem docstring đầu file để biết cách chẩn đoán.
- `test_frozen_files.py` — ghim SHA256 của `forward/logger.py` và
  `forward/config_frozen.yaml` (`tests/golden/frozen_hashes.json`). FAIL
  nghĩa là một trong hai file đã đổi — DÙ VÔ TÌNH HAY CỐ Ý, dù chỉ một
  dòng comment. **Không được sửa hash để cho qua.** Đổi hash CHỈ khi CỐ Ý
  kết thúc thí nghiệm forward hiện tại (bắt đầu 2026-08-06) và bắt đầu
  thí nghiệm mới — ghi lý do + ngày vào `docs/DECISIONS.md` TRƯỚC. Xem
  docstring đầu file.

Bảy file này không được skip, không được xfail, không được comment out.

### 16. Mọi phép kiểm tra mới phải được chứng minh bằng đột biến trước khi tin

Cố tình phá thứ nó đáng lẽ bắt được, xác nhận nó đỏ, rồi revert.

Chế độ hỏng chủ đạo của dự án này là lỗi xác minh, không phải lỗi logic — ba lần đã xảy ra: health_check chỉ gọi public endpoint, health_check hardcode sàn cũ, và pipe qua tail nuốt mất exit code.

#### Trước khi chạy bất kỳ kịch bản đột biến nào: commit hoặc `git stash`

Đột biến **sửa file thật trong cây làm việc**. Nếu tiến trình bị giết
trước khi `finally` chạy — timeout của harness, `Ctrl-C`, OOM — file ở
lại trạng thái đã phá. `git checkout <file>` phải là đường khôi phục
**luôn dùng được**, và nó chỉ dùng được khi mọi thay đổi thật đã nằm
trong commit hoặc stash.

Đã xảy ra 2026-08-08: kịch bản đột biến `run_live_loop` đặt timeout 600s,
hai đột biến làm vòng lặp vô hạn, harness giết tiến trình trước `finally`,
và `main.py` ở lại với một dòng `# MUTANT`. Lúc đó `git checkout` KHÔNG
dùng được vì toàn bộ công việc chưa commit — phải khôi phục thủ công
bằng cách đọc lại diff và sửa tay. Phát hiện được chỉ vì có bước
`grep MUTANT` sau khi chạy; không có bước đó thì một dòng đột biến đã đi
thẳng vào commit.

Kịch bản đột biến bắt buộc có đủ ba thứ:

1. **Timeout ≤ 60s mỗi bước.** Một test suite bình thường chạy vài giây;
   60s đã quá rộng rãi. Timeout dài không giúp gì cho đột biến làm treo
   vòng lặp — nó chỉ kéo dài thời gian file nằm ở trạng thái đã phá.
2. **Khôi phục trong `finally` của TỪNG bước**, không phải `finally`
   ngoài cùng. Ngoài cùng chỉ chạy nếu tiến trình sống tới đó.
3. **Assert không còn dấu vết đột biến ở cuối** (`assert "MUTANT" not in
   src`). Đây là thứ duy nhất phát hiện được trường hợp khôi phục đã
   thất bại một cách im lặng.

Đánh dấu mọi chỗ chèn bằng một chuỗi cố định (`# MUTANT`) để `grep` sau
khi chạy là phép kiểm rẻ và chắc chắn.

### 17. Không bao giờ đọc exit code sau pipe

`cmd | tail; echo $?` trả về exit code của tail. Dùng `PIPESTATUS`, hoặc bỏ pipe khi cần exit code.

---

### 18. Không đặt ngưỡng trước khi đo phân phối nền

Mọi ngưỡng cảnh báo phải kèm **bằng chứng về tỷ lệ báo động giả khi áp lên
chính dữ liệu baseline**. Ngưỡng đặt bằng trực giác hoặc bằng "con số
tròn" đã sai nhiều lần trong dự án này.

**Quy trình bắt buộc:**

1. Trượt cửa sổ **cùng kích thước** với cửa sổ mà cảnh báo sẽ dùng qua
   toàn bộ baseline.
2. Đo phân phối chỉ số trên các cửa sổ đó.
3. Đặt ngưỡng **theo phân vị**, không theo số tròn.
4. **Báo cáo tỷ lệ báo động giả đo được** — con số này phải nằm trong
   commit hoặc `docs/DECISIONS.md`, không phải trong đầu người viết.

Bước 1 là bước hay bị bỏ nhất và là bước quan trọng nhất: so một cửa sổ 30
bar với con số trung bình toàn kỳ là so hai thứ có phương sai khác nhau
hàng chục lần, và mọi ngưỡng đặt trên phép so đó đều vô nghĩa.

**Bằng chứng đã ghi trong `docs/DECISIONS.md`:**

| Ngưỡng | Số tròn ban đầu | Sau khi đo | Ghi ở |
|---|---|---|---|
| `circuit_breaker` daily/weekly reduce+halt | 4.0 / 6.0 / 10.0 / 14.0 | 3.85 / 6.34 / 9.48 / 13.72 (p2.5, p0.5) | "Phase 8 — hiệu chỉnh ngưỡng circuit breaker" |
| Drift §C.1 (allocation 15 điểm %, trend gate 20 điểm %) | 15 / 10 / 20 | giữ ngưỡng, **thêm** dải p1–p99 vì FP đo được là **99.7%** | "Ngưỡng drift §C.1 quá chặt so với nhiễu cửa sổ 30 bar" |

*Hai dòng trên là những lần đã ĐO và ghi lại. Còn một loạt ngưỡng khác
trong `config/settings.yaml` vẫn là số tròn chưa có bằng chứng phân phối
— `clock_drift_alert_ms` 1000, `clock_drift_halt_ms` 2500,
`large_pnl_alert_pct` 2.0, `unfilled_order_degraded_seconds` 300,
`alert_rate_limit_seconds` 900, `_DEFAULT_DEGRADED_AFTER` 3,
`WARNING_TREND_LEN` 3, `WINDOW_DAYS` 30. Chúng chưa sai theo cách đo được,
nhưng cũng chưa được chứng minh là đúng. Quy tắc này áp dụng cho ngưỡng
MỚI; ngưỡng cũ nào được đụng tới thì phải đo lúc đó.*

### 19. Mọi khẳng định "sạch" phải kèm PHẠM VI đã kiểm

Một công cụ báo "không có vấn đề" không nói được nó đã nhìn vào đâu. Bốn
chế độ hỏng, cả bốn đều làm một cổng rỗng trông như một cổng xanh:

- `grep -rn "..." duong/dan/` trên thư mục **không tồn tại** → rỗng, exit
  1, và trong một checklist thủ công thì "không có kết quả" là ĐẠT.
- `pytest` với `addopts = "-m 'not slow'"` → "toàn bộ xanh" chỉ đúng với
  phần đã chọn.
- `mypy .` dừng ở lỗi phân giải module đầu tiên → "Found 1 error" thay vì
  kiểm 84 file. Đã xảy ra, phát hiện 2026-08-14.
- `cmd | tail` nuốt exit code (xem #17).

**Bắt buộc:** mỗi mục nghiệm thu dạng "không có kết quả / sạch" phải in ra
phạm vi kèm theo — số file đã kiểm, số test đã collect, đường dẫn đã tồn
tại. `ops/verify_scope.py` cơ giới hoá điều này; `tests/test_verify_scope.py`
ghim nó. Đường dẫn trong một mục nghiệm thu mà không tồn tại là **FAIL**,
không phải "sạch".

### 20. Không bao giờ hai tiến trình cùng khả năng đặt lệnh trên một tài khoản

Đây là **bất biến**, không phải lựa chọn triển khai.

Blue-green giả định các instance **không chia sẻ trạng thái**. Với bot
giao dịch, trạng thái thật nằm ở **SÀN**, không nằm trong tiến trình. Hai
instance cùng quản một tài khoản sẽ tính rebalance độc lập và cùng gửi
lệnh.

`orderLinkId` sinh deterministic (#8) chỉ chặn được lệnh trùng khi hai
instance tính ra **CÙNG** allocation. Nếu chúng khác nhau — mà khác nhau
chính là lý do bạn đang deploy — cả hai lệnh đều qua, và **vị thế nhân
đôi**.

Hệ quả cụ thể:

- Không blue-green, không rolling deploy, không "chạy song song để so".
- Chuyển đổi phiên bản = **dừng instance cũ, rồi mới khởi động instance
  mới**. Bàn giao qua `state_snapshot.json`.
- Muốn so hai phiên bản: `ops/compare_versions.py` (ngoại tuyến, Phase
  12c §A) hoặc shadow mode CHỈ-ĐỌC (`ops/shadow_runner.py`, không có
  đường nào tới `broker/order_executor.py`).
- `ops/shadow_runner.py` chặn bằng **kiến trúc** (không import), không
  bằng cờ boolean. Một cờ `dry_run=True` có thể bị lật nhầm; một import
  không tồn tại thì không.

Quy trình triển khai đầy đủ: `ops/RUNBOOK.md` mục "Triển khai phiên bản
mới". Điều kiện thời điểm: `ops/deploy_conditions.py` (§E).

## Phong cách code

- Python 3.11+, type hint đầy đủ
- `dataclass` cho mọi cấu trúc dữ liệu, `frozen=True` khi có thể
- ABC cho mọi interface có nhiều implementation (`ExchangeClient`, `Strategy`)
- Tầng strategy và risk **không bao giờ** import `pybit` trực tiếp — chỉ qua `broker/base.py`
- Log JSON có cấu trúc, không `print()` trong code production
- Docstring giải thích **tại sao**, không phải **cái gì**

---

## Khi bạn không chắc

Nếu một yêu cầu của tôi mâu thuẫn với file này, hãy nói ra thay vì im lặng làm theo. Các bất biến ở trên được viết trong lúc tỉnh táo, không phải giữa lúc đang debug.

Nếu spec không nói rõ về một tình huống, chọn phương án **giảm rủi ro** và ghi lại lựa chọn đó vào code comment kèm lý do.
