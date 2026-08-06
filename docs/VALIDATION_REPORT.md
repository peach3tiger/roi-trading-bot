# docs/VALIDATION_REPORT.md — Báo cáo đóng giai đoạn kiểm định (Phase 1–7)

Ngày đóng: 2026-08-06. Tài liệu này tổng kết toàn bộ Phase 1–7 (scaffold →
HMM/feature → strategy/trend-gate → backtest §4.9 → thí nghiệm mở rộng) và
ghi quyết định đi tiếp. Đây không phải bản nháp — số liệu dưới đây là số
cuối cùng dùng để quyết định, có đường dẫn `reports/` kèm theo để tái tạo.
Chi tiết từng phép đo, xem `docs/DECISIONS.md`.

Cấu hình được đánh giá xuyên suốt tài liệu này, trừ khi ghi chú khác:
**pruned-8** (`log_return_1,log_return_5,realized_vol_20,vol_ratio_5_20,
adx_14,sma50_slope,trade_count_zscore_50,trade_count_sma10_slope`),
BTCUSDT, bar-offset 0, `2018-02-09→2026-08-04`, `is_bars=365,
oos_bars=182, step_bars=182, covariance_type=full`, `uncertainty_mode=
"halve"`. Báo cáo gốc: `reports/pruned8_base/`.

---

## 1. §4.9 — 8 tiêu chí đi tiếp

| # | Tiêu chí | Số liệu | Kết quả |
|---|---|---|---|
| 1 | Sharpe OOS > 1.0 sau chi phí | 0.9411 | **FAIL** |
| 2 | Calmar > buy-and-hold | 0.5996 vs 0.5217 | PASS |
| 3 | Sharpe > vol-target tĩnh ít nhất +0.2 | 0.9411 vs 0.9142, chênh +0.027 | **FAIL** (cần +0.2) |
| 4 | Ngoài 2 độ lệch chuẩn của random allocation | z = (0.9411−0.6251)/0.1065 = 2.97σ | PASS |
| 5 | 2022 không lỗ nặng hơn buy-and-hold | chiến lược −28.80% vs BH −65.42% | PASS |
| 6 | Sharpe 4 lần bar-offset chênh ≤ 0.3 | offset0=0.9411, offset6=0.8404, offset12=1.0574, offset18=0.8801 — spread 0.217 | PASS |
| 7 | ETH không tune: Sharpe > 0.5 | 0.9278 | PASS |
| 8 | Phí < 30% lợi nhuận gộp | 11.68% (4445.04 + 1333.51 = 5778.55 USDT / gross 49489.51 USDT) | PASS |

**Kết quả: 6/8 PASS.** Theo CLAUDE.md bất biến #12, không đủ 8/8 thì không
xây tầng thực thi. Nguồn: `reports/pruned8_base`, `reports/pruned8_eth`
(tiêu chí 7), `reports/pruned8_period2022` (tiêu chí 5),
`reports/pruned8_bar_offset` (tiêu chí 6).

---

## 2. Bốn benchmark

| | Sharpe | Calmar | Max DD |
|---|---|---|---|
| **strategy** | 0.9411 | **0.5996** | **-54.80%** |
| buy_and_hold | 0.8719 | 0.5217 | -76.64% |
| sma200_trend | **0.9567** | 0.5897 | -64.26% |
| static_vol_target | 0.9142 | 0.5223 | -65.38% |

Nguồn: `reports/pruned8_base/benchmark_comparison.csv`.

**Strategy thắng Calmar cả ba benchmark** (0.5996 so với 0.5217 / 0.5897 /
0.5223) — drawdown-điều-chỉnh, đây là kết quả rõ ràng, không sát biên.

**Strategy không thắng Sharpe:** thua `sma200_trend` (0.9411 < 0.9567), chỉ
nhỉnh hơn `static_vol_target` +0.027 (đây chính là tiêu chí 3 FAIL — biên
+0.027 nhỏ hơn nhiều so với yêu cầu +0.2 và nhỏ hơn sai số đo ở mục 3 bên
dưới), và chưa vượt mốc tuyệt đối 1.0 (tiêu chí 1 FAIL). Nói cách khác:
strategy quản lý rủi ro đuôi tốt hơn nắm giữ thụ động và tốt hơn cả
benchmark trend-following đơn giản nhất, nhưng chưa chứng minh được lợi
suất-điều-chỉnh-rủi-ro (Sharpe) vượt trội — kể cả so với `static_vol_target`,
vốn không dùng HMM, không dùng feature nào, chỉ scale ngược volatility.

---

## 3. Giới hạn của phép đo

Đây là mục quan trọng nhất của tài liệu này.

**3.1 — Cả hai FAIL đều nằm trong sai số đo.** Biên độ Sharpe do riêng việc
đổi mốc đóng bar (không đổi gì khác) trên cửa sổ dài là **0.217** (tiêu chí
6). Khoảng cách còn thiếu của tiêu chí 1 là **0.059** (1.0 − 0.9411) và của
tiêu chí 3 là **0.173** (0.2 − 0.027). Cả hai đều **nhỏ hơn** 0.217. Nghĩa
là: nếu chạy lại đúng hệ thống này với mốc đóng bar khác — không đổi feature,
không đổi tham số, không đổi gì về mô hình — cả hai FAIL có thể lật thành
PASS, hoặc một PASS khác có thể lật thành FAIL, chỉ vì nhiễu đo lường vốn
có của chính setup walk-forward này. Hai con số FAIL không phân biệt được
với nhiễu.

**3.2 — Tiêu chí 6, như đang viết, bị lẫn với số lượng window — đây là lỗi
thiết kế tiêu chí, không phải lỗi hệ thống.** Đo trực tiếp bằng cách chạy
đúng cùng một hệ thống (pruned-8, mọi tham số giống hệt) trên hai độ dài
cửa sổ khác nhau:

| | số window walk-forward | biên độ Sharpe theo bar-offset |
|---|---|---|
| cửa sổ dài (2018-02-09→2026-08-04) | 13 | **0.217** (PASS, ngưỡng ≤0.3) |
| cửa sổ ngắn (2020-08-05→2026-08-04, dữ liệu ép ngắn lại do funding chỉ có từ ~2020) | 8 | **0.4909** (tự FAIL, ngưỡng ≤0.3) |

Cùng một hệ thống, cùng feature, chỉ đổi độ dài cửa sổ (→ đổi số window),
biên độ đổi từ PASS thoải mái sang FAIL rõ ràng. Ngưỡng cố định 0.3 không
phải thước đo độc lập với quy mô mẫu — nhiều window hơn tự nhiên pha loãng
(pool) biên độ của một window bất ổn xuống, ít window hơn để lộ nó ra
nguyên vẹn. Một tiêu chí go/no-go không nên phụ thuộc vào số window walk-forward
theo cách này mà không có cơ chế chuẩn hoá theo cỡ mẫu.

**3.3 — Window ETF-rally (Q4/2023–Q1/2024) là window nhạy bar-alignment
nhất ở cả hai cửa sổ, và cửa sổ dài chỉ pha loãng được nó, không bền hơn
về bản chất.**

| | biên độ Sharpe của window ETF-rally theo offset | biên độ window đứng thứ nhì |
|---|---|---|
| cửa sổ ngắn (window 2/8) | **2.095** (2.443→4.538) | 1.034 (window 4) |
| cửa sổ dài (window 7/13) | **1.193** (2.457→3.650) | 1.048 (window 0) |

Ở cửa sổ ngắn, window này áp đảo gần gấp đôi window bất ổn thứ nhì và một
mình quyết định phần lớn biên độ tổng 0.4909. Ở cửa sổ dài, cùng window đó
(cùng giai đoạn lịch, OOS ~2023-10 → 2024-04) vẫn là window bất ổn nhất
trong 13 window, nhưng nằm sát một cụm 3-4 window có biên độ tương đương
(0.95–1.05) thay vì áp đảo — nên khi pool 13 window lại, biên độ tổng chỉ
còn 0.217. Đây là hiệu ứng pha loãng theo số mẫu, không phải bằng chứng
cửa sổ dài "ổn định hơn" theo nghĩa cơ chế. Nguồn gốc bất ổn — độ nhạy cực
cao với mốc đóng bar trong đúng giai đoạn rally một chiều mạnh nhất của
dữ liệu — có mặt như nhau ở cả hai.

**3.4 — Tập kiểm định đã bị nhìn nhiều lần trong quá trình chẩn đoán, không
còn tính ngoài mẫu.** Trong quá trình đưa tới bảng ở mục 1, cùng một giai
đoạn OOS (về cơ bản `2018-02-09→2026-08-04`, hoặc các cửa sổ con của nó) đã
được đánh giá lặp lại qua: bộ 14→8 feature pruning, ablation 7 feature
(`reports/ablation8`), sweep 8 mốc bắt đầu (`reports/pruned8_start_*`),
sweep 4 bar-offset (`reports/pruned8_bar_offset`), tách theo confidence
bucket, `period2022`, `eth`, ba biến thể `uncertainty_mode`
(`reports/uncertainty_B_hold_previous`, `_C_none`), và cửa sổ ngắn tầng 2
(`reports/tier2_shortwin_baseline*`). Mỗi lần nhìn số OOS để quyết định
giữ/bỏ một lựa chọn thiết kế (giữ pruned-8 thay vì 14 cột, giữ
`uncertainty_mode="halve"`, bỏ thí nghiệm tầng 2) là một bậc tự do nghiên
cứu tiêu tốn từ đúng tập dữ liệu đang được dùng để báo cáo Sharpe cuối
cùng — dù không có bước nào tinh chỉnh tham số trực tiếp theo Sharpe OOS
bằng grid search, việc quyết định dựa trên nhìn thấy kết quả nhiều lần vẫn
làm giảm độ tin cậy của tuyên bố "ngoài mẫu". Con số 0.9411 nên được đọc là
kết quả trên một tập đã được chẩn đoán kỹ, không phải một lần chạy sạch
duy nhất trên dữ liệu chưa từng thấy.

---

## 4. Thí nghiệm đã chạy và kết quả

**Ablation feature (`reports/ablation8/feature_ablation.csv`)** — bộ pruned-8
là kết quả pruning tương quan tham lam (|r|>0.5) từ 14 cột Tầng 1 gốc ở
phiên trước Phase 6. Trên chính bộ 8 đó, ablate từng feature (trừ
`log_return_1`, bắt buộc cấu trúc cho `_build_regime_infos`, đánh dấu
`SKIPPED_STRUCTURAL_REQUIRED`): **chỉ `log_return_5` vượt ngưỡng nhiễu**
(Δsharpe +0.235 khi bỏ, tương đương **+2.6σ** so với noise floor 0.089 đo
từ sweep 8 mốc bắt đầu) → `KEEP`. 6 feature còn lại có Δsharpe trong
khoảng ±0.06 (±0.4–1.1σ) — không phân biệt được với nhiễu từ một lần chạy
duy nhất, verdict `DROP_CANDIDATE` ban đầu bị coi là overclaim, giữ nguyên
bộ 8 vì chưa đủ bằng chứng để bỏ bất kỳ cột nào trong số đó.

**Uncertainty mode A/B/C (2026-08-05)** — so `halve` (mặc định, giảm nửa
allocation khi bất định) với `hold_previous` và `none`:

| | A: halve | B: hold_previous | C: none |
|---|---|---|---|
| Sharpe | 0.9411 | 0.9661 | 0.9646 |
| Calmar | **0.5996** | 0.5961 | 0.5978 |
| Max DD | **-54.80%** | -60.50% | -60.35% |
| Bucket <50% conf. Sharpe | 0.489 | 0.798 | 0.842 |

Bỏ `halve` cải thiện Sharpe cục bộ trên bar confidence thấp (<50%,
n=371) nhưng làm max drawdown toàn kỳ sâu thêm ~5.6pp và Calmar xấu đi.
**Quyết định: `halve` đang làm việc phòng vệ đuôi thật, không phải lỗi
thiết kế — giữ nguyên làm mặc định.** Không có biến thể nào đạt tiêu chí 1
hay 3.

**Tầng 2 (funding/OI/basis)** — **không đánh giá được**, không phải "không
có tín hiệu". Dữ liệu derivatives trên Bybit chỉ có từ ~2020, buộc cửa sổ
backtest ngắn lại còn ~6 năm (13→8 window). Baseline pruned-8 (chưa thêm
cột tầng 2 nào) trên đúng cửa sổ ngắn đó đã tự FAIL tiêu chí 6 (biên độ
0.4909, xem mục 3.2–3.3) — nền walk-forward không đủ ổn định để phân biệt
tín hiệu tầng 2 thật với bất ổn window-alignment sẵn có. Dừng trước khi
chạy bất kỳ ablation tầng 2 nào. Code hạ tầng
(`compute_tier2_features`, `DerivativesLoader`) giữ nguyên trong codebase,
lint/mypy/pytest xanh, không dùng trong cấu hình hiện tại.

---

## 5. Kết luận

Hệ thống giảm drawdown thật (-54.8% so với -76.6%) và không chứng minh
được lợi thế Sharpe so với các phương án đơn giản hơn.

6/8 tiêu chí §4.9 pass, nhưng hai FAIL còn lại (mục 1) nằm trong sai số đo
đã lượng hoá được (mục 3.1), và bản thân tiêu chí dùng để đo sai số đó
(tiêu chí 6) có lỗi thiết kế phụ thuộc quy mô mẫu (mục 3.2). Ưu thế rõ
ràng, lặp lại nhất quán qua mọi thí nghiệm (benchmark, uncertainty mode
A/B/C) là **Calmar/max drawdown**, không phải Sharpe — chiến lược thắng
Calmar cả ba benchmark nhưng thua Sharpe so với `sma200_trend`, một
benchmark không dùng HMM. Đây là hệ thống quản lý rủi ro đuôi có hiệu quả
đo được, không phải hệ thống tạo alpha đã chứng minh được.

---

## 6. Quyết định

**Không xây Phase 8–12** (monitoring mở rộng, thực thi live, perps, và mọi
tầng sau đó). **Chuyển sang forward-test: chạy hệ thống ghi log tín hiệu và
allocation mỗi bar, không đặt lệnh, không kết nối vốn thật hay testnet ở
chế độ đặt lệnh.**

Lý do: tập kiểm định lịch sử đã cạn theo nghĩa dùng được cho quyết định
go/no-go (mục 3.4) — mọi cửa sổ, mọi mốc bắt đầu, mọi bar-offset trong dữ
liệu hiện có đã được nhìn ít nhất một lần trong quá trình chẩn đoán ở trên.
Tiếp tục quét thêm biến thể trên cùng dữ liệu lịch sử chỉ làm giảm thêm độ
tin cậy "ngoài mẫu" của bất kỳ kết quả nào, không tăng thêm bằng chứng thật.
Nguồn bằng chứng độc lập duy nhất còn lại là **thời gian** — bar mới chưa
từng được dùng để chẩn đoán hay chỉnh bất kỳ lựa chọn thiết kế nào ở trên.
Forward-test ghi log (không đặt lệnh) là cách duy nhất thu thập bằng chứng
đó mà không tạo thêm rủi ro vốn, và không vi phạm CLAUDE.md bất biến #12
(chưa đủ 8/8 → chưa xây tầng thực thi).
