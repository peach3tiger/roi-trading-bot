# docs/DECISIONS.md — nhật ký quyết định & kết quả §4.9

Ghi lại **con số cụ thể** đằng sau mỗi quyết định go/no-go, để không phải
chạy lại hay nhớ lại từ đầu mỗi phiên. Cập nhật file này mỗi khi có kết quả
mới ảnh hưởng tới §4.9 (`docs/Brain-Crypto-Bybit.md`).

---

## Cấu hình hiện tại đang đánh giá: "pruned-8"

`--feature-subset log_return_1,log_return_5,realized_vol_20,vol_ratio_5_20,adx_14,sma50_slope,trade_count_zscore_50,trade_count_sma10_slope`

- Symbol: BTCUSDT, bar-offset 0 (00:00 UTC)
- `--start 2018-02-09 --end 2026-08-04`
- `is_bars=365, oos_bars=182, step_bars=182, covariance_type=full`
- BIC chọn `n_components` mỗi window riêng (dao động 4–7 tuỳ window trong các
  lần chạy đã có)
- Báo cáo gốc: `reports/pruned8_base/`

### Vì sao pruned-8, không phải 14 cột Tầng 1 đầy đủ

`covariance_type=full` làm số tham số HMM tăng **bậc hai** theo số feature.
Với 14 feature và `is_bars=365`, `samples_per_param` (xem `model_selection.csv`)
nằm ở 0.41–0.59 — **dưới 1 ở MỌI window** đã kiểm tra. Model bị thiếu dữ
liệu nghiêm trọng so với số tham số, khiến BIC chọn `n_components` không ổn
định giữa các window, và biểu hiện ra ngoài như "chênh lệch mốc bắt đầu 1
tuần → return chênh 15.8 lần" và ETH OOS Sharpe 0.235 (fail rõ ràng, cần
>0.5).

Bộ 8 feature (chọn bằng greedy correlation pruning, |r|>0.5, từ phiên trước
Phase 6) sửa triệt để vấn đề này:

| | 14 feature (gốc) | 8 feature (pruned) |
|---|---|---|
| `samples_per_param` (min qua mọi window) | 0.41–0.59 | **≥1.02, 0/104 window dưới 1** (8 lần sweep mốc bắt đầu) |
| Base backtest Sharpe / Calmar | 0.334 / 0.103 | **0.941 / 0.600** |
| Sweep mốc bắt đầu: total_return spread | 15.8x | **1.96x** |
| Sweep: Sharpe range | — | 0.763–0.979 (mean 0.875, std 0.089) |
| ETH OOS Sharpe | 0.235 (fail) | **0.928** (pass) |

Kết luận: pruning giải quyết đúng vấn đề nó nhắm tới (bất ổn window-alignment,
fail ETH). Không cần thêm lever nào khác (tăng `is_bars`, giới hạn
`n_candidates`, neo window theo lịch).

### Ablation trên bộ 8 feature (`reports/ablation8/feature_ablation.csv`)

`log_return_1` **không** ablate được — `core/hmm_engine.py::_build_regime_infos`
dùng đúng cột này (`means_[:, return_idx]`) để xếp hạng state theo mean
return và gán nhãn bull/bear. Đây là trục cấu trúc của toàn bộ sơ đồ gán
nhãn, không phải một feature HMM học được trong số nhiều feature — bỏ nó ra
không đo được "tín hiệu return có giúp Sharpe không", chỉ làm crash
(`ValueError: not in list`). Đánh dấu `SKIPPED_STRUCTURAL_REQUIRED` thay vì
chạy hoặc gán verdict sai.

| dropped_feature | Δsharpe | Δsharpe / noise_std (0.089*) | verdict |
|---|---|---|---|
| log_return_1 | — | — | SKIPPED (bắt buộc cấu trúc) |
| log_return_5 | +0.235 | **+2.6σ — resolvable** | KEEP |
| trade_count_sma10_slope | +0.060 | +0.7σ | trong noise |
| realized_vol_20 | +0.051 | +0.6σ | trong noise |
| trade_count_zscore_50 | +0.039 | +0.4σ | trong noise |
| sma50_slope | -0.036 | -0.4σ | trong noise |
| vol_ratio_5_20 | -0.048 | -0.5σ | trong noise |
| adx_14 | -0.097 | -1.1σ | trong noise |

\* noise_std = độ lệch chuẩn Sharpe từ sweep 8 mốc bắt đầu (single-run
noise floor). Tiêu chí `delta_sharpe >= 0.1` của CLAUDE.md #13 không tính
tới noise này — chỉ `log_return_5` là tín hiệu thật (2.6σ), 6 feature còn
lại **không phân biệt được với nhiễu** từ một lần chạy duy nhất. Verdict
`DROP_CANDIDATE` gán ban đầu cho 6 feature đó bị overclaim; cần lặp lại
ablation qua nhiều mốc (như sweep) mới kết luận được, hoặc chấp nhận không
biết và giữ nguyên bộ 8.

### Chẩn đoán bổ sung (không sửa gì, chỉ đo)

**Confidence bucket (§4.5)** — `reports/pruned8_base`:

| bucket | n_bars | strategy sharpe | buy-and-hold sharpe (cùng đúng những bar đó) | chênh lệch |
|---|---|---|---|---|
| <50% | 371 | 0.489 | **1.204** | **-0.715** |
| 50-60% | 19 | 16.03 | — | n quá nhỏ, bỏ qua |
| 60-70% | 18 | 0.618 | — | n quá nhỏ, bỏ qua |
| 70%+ | 1882 | 0.983 | 0.759 | **+0.224** |

Trên các bar HMM tự tin thấp, chiến lược không chỉ kém các bar tự tin cao —
nó kém **cả buy-and-hold trên đúng những bar đó** (-0.715 Sharpe). Trên bar
tự tin cao, chiến lược thắng buy-and-hold (+0.224). Tín hiệu confidence
đang xác định đúng những thời điểm mà giảm tỷ trọng chủ động gây thiệt hại
so với việc không làm gì.

**Phân bố allocation** — không suy biến thành buy-and-hold trá hình:
tier lớn nhất (0.95, low-vol bull) chỉ chiếm 34.8% số bar; 739 lần rebalance
/ 2291 bar (32.3%).

| tier | ý nghĩa | % thời gian |
|---|---|---|
| 0.30 | trend-gate cap (bear structure) | 30.6% |
| 0.50 | high-vol defensive (strategy target) | 18.1% |
| 0.60 | transition cap / mid-vol target | 16.5% |
| 0.95 | low-vol bull (strategy target) | 34.8% |

---

## §4.9 — Tiêu chí đi tiếp (8 tiêu chí, spec dòng 720–734)

Đánh giá trên cấu hình pruned-8, `uncertainty_mode="halve"` (baseline,
không đổi gì) — `reports/pruned8_base` (+ `reports/pruned8_eth` cho tiêu
chí 7, `reports/pruned8_period2022` cho tiêu chí 5,
`reports/pruned8_bar_offset` cho tiêu chí 6). **Kiểm đếm: 6 PASS / 2 FAIL /
0 CHƯA CHẠY — cả 8 tiêu chí đã tính bằng số thật.**

> Lưu ý lịch sử: một lần trao đổi trước ghi "3 PASS / 2 FAIL / 3 chưa
> chạy" — con số đó chưa từng được tính ra bằng dữ liệu thật (lệnh tính
> khi đó lỗi `ModuleNotFoundError: pandas` do quên activate venv). Bảng
> dưới là bảng đầy đủ đầu tiên, và nay đã chạy nốt tiêu chí 5/6 (tiêu chí 7
> đã có sẵn từ trước, dùng lại không chạy thêm vì cấu hình không đổi).

| # | Tiêu chí | Số liệu | Kết quả |
|---|---|---|---|
| 1 | Sharpe OOS > 1.0 sau chi phí | 0.9411 | **FAIL** |
| 2 | Calmar > buy-and-hold | 0.5996 vs 0.5217 | PASS |
| 3 | Sharpe > vol-target tĩnh ít nhất +0.2 | 0.9411 vs 0.9142, chênh **+0.027** | **FAIL** (cần +0.2) |
| 4 | Ngoài 2 độ lệch chuẩn của random allocation | z = (0.9411−0.6251)/0.1065 = **2.97σ** | PASS |
| 5 | 2022 không lỗ nặng hơn buy-and-hold | chiến lược **−28.80%** vs BH **−65.42%** (`reports/pruned8_period2022`) | PASS |
| 6 | Sharpe 4 lần bar-offset chênh ≤ 0.3 | offset0=0.9411, offset6=0.8404, offset12=1.0574, offset18=0.8801 — spread **0.217** (`reports/pruned8_bar_offset`) | PASS |
| 7 | ETH không tune: Sharpe > 0.5 | 0.9278 | PASS |
| 8 | Phí < 30% lợi nhuận gộp | 11.68% (fee 4445.04 + slippage 1333.51 = 5778.55 USDT / gross 49489.51 USDT) | PASS |

**Không đủ 8/8 — theo CLAUDE.md bất biến #12, chưa được xây tầng thực thi
(Phase 5 / risk manager).** Nhưng khoảng cách còn lại đã thu hẹp đáng kể so
với bản đánh giá trước: chỉ còn 2 fail (1, 3), cả hai cùng phản ánh một vấn
đề — Sharpe thô (0.941) tốt nhưng chưa đạt 1.0, và biên vượt benchmark khắt
khe nhất (vol-target tĩnh) còn mỏng (+0.027 so với yêu cầu +0.2). 6 tiêu
chí còn lại đều pass rõ ràng, không phải biên sát nút.

## Thí nghiệm: uncertainty-mode (2026-08-05)

**Giả thuyết:** dựa trên phát hiện confidence-bucket ở trên (<50% confidence,
strategy kém buy-and-hold 0.715 Sharpe trên đúng 371 bar đó), thiết kế hiện
tại — giảm nửa allocation khi `probability < min_confidence (0.55)` hoặc
đang flicker (`core/regime_strategies.py::StrategyOrchestrator.generate_signal`,
`is_uncertain` block) — có thể là phản ứng sai.

**Thiết kế:** thêm tham số `uncertainty_mode: Literal["halve","hold_previous","none"]`
vào `StrategyOrchestrator.__init__` (mặc định `"halve"`, không đổi hành vi
cho caller cũ). HMM train dùng `random_state=seed` cố định và không phụ
thuộc uncertainty_mode, nên `regime_probability` mỗi bar **giống hệt nhau**
giữa 3 biến thể (đã kiểm chứng bằng so sánh trực tiếp) — khác biệt duy nhất
là nhánh xử lý `is_uncertain`. Mọi thứ khác (feature subset, ngày, symbol,
offset, trend gate, cost model, windows) giữ nguyên tuyệt đối so với
`pruned8_base`.

| | A: halve (baseline) | B: hold_previous | C: none |
|---|---|---|---|
| Sharpe | 0.9411 | 0.9661 | 0.9646 |
| Calmar | **0.5996** | 0.5961 | 0.5978 |
| Max DD | **-54.80%** | -60.50% | -60.35% |
| Phí (% gross profit) | 11.68% | **3.40%** | **4.00%** |
| n_rebalances | 739 | 618 | 691 |
| Bucket <50% Sharpe (n=371) | 0.489 | 0.798 | 0.842 |
| Bucket <50% vs BH (1.2045) | **-0.715** | -0.407 | -0.363 |
| Bucket 70%+ Sharpe (n=1882) | 0.983 | 0.959 | 0.944 |
| Bucket 70%+ vs BH (0.7589) | +0.224 | +0.201 | +0.185 |

**Kết quả — giả thuyết đúng một phần, sai một phần:**

Trên đúng các bar bị ảnh hưởng, giả thuyết ĐÚNG: bỏ hoặc làm nhẹ việc giảm
tỷ trọng cải thiện rõ rệt hiệu suất trên bucket <50% (Sharpe 0.489 → 0.798/
0.842, thu hẹp khoảng cách với buy-and-hold từ -0.715 xuống -0.36/-0.41) và
giảm phí mạnh (11.68% → 3.4-4.0%, vì việc giảm-rồi-khôi-phục tỷ trọng khi
confidence dao động quanh ngưỡng 0.55 tự nó sinh ra một phần đáng kể trong
739 lần rebalance của baseline).

Nhưng ở cấp độ toàn kỳ, giả thuyết KHÔNG được xác nhận: Calmar giảm nhẹ
(0.600 → 0.596/0.598) và max drawdown XẤU ĐI rõ rệt (-54.8% → -60.5%/-60.4%,
~+5.6pp). Cơ chế giảm tỷ trọng khi bất định, dù tốn Sharpe trên đúng những
bar bị áp dụng, đang làm công việc giảm rủi ro đuôi thực sự có giá trị ở
cấp độ toàn kỳ — khả năng cao là các giai đoạn bất định trùng hoặc đi trước
những đợt sụt giảm lớn hơn nằm ngoài chính 371 bar đó.

Không có biến thể nào đạt tiêu chí 1 (Sharpe > 1.0) hay tiêu chí 3 (vượt
vol-target tĩnh +0.2 Sharpe — B đạt +0.052, C đạt +0.051, A đạt +0.027, cả
ba đều còn xa ngưỡng 0.2).

**Không thay đổi cấu hình mặc định** — `uncertainty_mode="halve"` vẫn là
default của `StrategyOrchestrator`, thí nghiệm này chỉ nhằm kiểm chứng giả
thuyết, quyết định giữ/đổi để ở phiên sau.

### QUYẾT ĐỊNH (2026-08-05)

Đã kiểm chứng xong giả thuyết uncertainty-mode (A/B/C ở trên). Bỏ `halve`
cải thiện cục bộ trên bar confidence thấp (<50%) và giảm phí, nhưng làm
max drawdown sâu thêm 5.6pp và Calmar xấu đi ở mức toàn kỳ.

**Kết luận: cơ chế `halve` làm việc phòng vệ đuôi thật, không phải lỗi
thiết kế. GIỮ NGUYÊN `uncertainty_mode="halve"`.** Không tối ưu lại dựa
trên bảng bucket cục bộ — bảng đó đo đúng một lát cắt (371 bar), không đo
được vai trò phòng vệ đuôi ở cấp toàn kỳ mà `halve` đang thực hiện. Coi
câu hỏi này là đã đóng, không mở lại trừ khi có bằng chứng mới ở cấp toàn
kỳ (Calmar/max DD), không phải bằng chứng cục bộ theo bucket.

## Thí nghiệm tầng 2 — tiền đăng ký (ngày hôm nay)

Ghi lại **trước khi chạy bất kỳ backtest nào** cho thí nghiệm này — đúng
tinh thần cảnh báo của spec §4.9: "Quyết định tiêu chí sau khi nhìn kết quả
là hình thức tự lừa dối phổ biến nhất trong xây dựng hệ thống giao dịch."
Phiên này chỉ ghi đăng ký, **không chạy code, không implement
`compute_tier2_features`, không đổi gì khác.**

### Bối cảnh

§4.9 đạt 6/8. Hai fail (1, 3) có khoảng cách lần lượt **0.059** (1.0 −
0.9411) và **0.173** (0.2 − 0.027) Sharpe — cả hai đều **nhỏ hơn** biên độ
nhiễu bar-offset đã đo được ở tiêu chí 6 (**0.217**, giữa offset6=0.8404 và
offset12=1.0574, cùng một cấu hình, chỉ đổi mốc đóng bar). Nghĩa là: mọi
cải thiện Sharpe dưới ~0.2 không phân biệt được với nhiễu đo lường vốn có
của chính setup này — không cần thêm feature gì để tạo ra chênh lệch cỡ đó,
chỉ cần đổi mốc đóng bar cũng đủ.

### Giả thuyết

8 feature Tầng 1 hiện tại (pruned-8) đều là hàm của OHLCV — cùng nguồn dữ
liệu gốc với cả bốn benchmark đang dùng để so sánh (buy-and-hold, SMA200,
random, vol-target). Feature Tầng 2 (funding, OI, basis) là nguồn thông tin
**độc lập duy nhất chưa thử** — nếu có tín hiệu thật nằm ngoài những gì OHLCV
tự nó chứa, đây là nơi duy nhất còn lại để tìm.

### Feature đề xuất thêm (Tầng 2, §2.3 spec)

5 cột: `funding_rate` (làm mượt 3 chu kỳ), `funding_zscore_90`,
`oi_change_24h`, `perp_spot_basis`, `taker_buy_ratio`. Cộng với 8 cột Tầng 1
hiện có (pruned-8) → bộ 13 cột khi test đầy đủ.

`data/derivatives_loader.py` đã tồn tại (Phase 2, chưa từng dùng) —
`DerivativesLoader.load_funding_rate`/OI qua Bybit v5, category=linear,
không cần API key. `compute_tier2_features` trong
`data/feature_engineering.py` hiện **`raise NotImplementedError`** — đây là
việc cần làm đầu tiên khi bắt đầu chạy, chưa làm trong phiên đăng ký này.

### Ràng buộc bắt buộc

1. **So sánh đúng cửa sổ, không so sánh chéo.** Dữ liệu funding/OI trên
   Bybit chỉ có từ khoảng 04/2020 (ghi trong docstring của
   `derivatives_loader.py`, chưa xác nhận số ngày chính xác — bước đầu tiên
   khi chạy là gọi `get_funding_rate_available_range()` để lấy mốc thật,
   không đoán). Cửa sổ backtest cho thí nghiệm Tầng 2 do đó ngắn hơn hẳn
   cửa sổ 2018-02-09→2026-08-04 đang dùng cho `reports/pruned8_base`
   (Sharpe 0.9411). **BẮT BUỘC chạy lại baseline pruned-8 (8 cột Tầng 1,
   không đổi gì khác — `uncertainty_mode="halve"`, is_bars/oos_bars/
   step_bars giữ nguyên) trên ĐÚNG cửa sổ ngắn đó trước khi đánh giá Tầng
   2.** Số 0.9411 là của cửa sổ dài — so Tầng 2 (cửa sổ ngắn) với nó là so
   sai. Baseline-cùng-cửa-sổ này là mốc so sánh duy nhất hợp lệ cho ngưỡng
   thành công bên dưới.
2. **Ablation từng feature Tầng 2** theo CLAUDE.md bất biến #13 — chạy
   `--ablation` trên bộ 13 cột, ghi `feature_ablation.csv`. `log_return_1`
   vẫn được `SKIPPED_STRUCTURAL_REQUIRED` như bộ 8 (bắt buộc cho
   `_build_regime_infos`, không đổi). Áp lại đúng cảnh báo đã ghi ở phần
   ablation Tầng 1 phía trên: BIC giữa hai model khác số feature **không so
   sánh trực tiếp được**, và một lần chạy ablation duy nhất không phân biệt
   được tín hiệu thật với nhiễu.

### Ngưỡng thành công — chốt trước khi chạy

- ~~Sharpe cải thiện ≥ 0.20 so với baseline pruned-8 cùng cửa sổ ngắn~~ —
  **SỬA ĐỔI (trước khi chạy bất kỳ cấu hình Tầng 2 nào, xem "Sửa đổi
  ngưỡng" bên dưới).** Con số 0.20 gốc suy ra từ sàn nhiễu đo trên cửa sổ
  DÀI (biên độ bar-offset 0.217, std sweep mốc bắt đầu ≈0.089); cửa sổ
  ngắn có ít bar hơn nên sàn nhiễu thật của nó có thể cao hơn — giữ 0.20
  cứng có nguy cơ ngưỡng quá dễ, cho phép nhiễu bị diễn giải nhầm thành tín
  hiệu.
- **Phải giữ được tiêu chí 5, 6, 7, 8** (chạy lại cả bốn trên cấu hình mới
  — `--period 2022` trong phạm vi cửa sổ ngắn cho phép, `--bar-offset
  0,6,12,18`, `--symbol ETHUSDT` không tune, `cost_pct_of_gross_profit`).
  Tiêu chí 1–4 tiếp tục đánh giá trên baseline-cùng-cửa-sổ vs Tầng-2, không
  đổi định nghĩa.
- **Không đạt ngưỡng → kết luận Tầng 2 không mang thêm thông tin, ghi lại
  đúng như vậy vào `docs/DECISIONS.md`, và dừng việc tìm cần gạt** — không
  thử thêm biến thể Tầng 2 khác, không hạ ngưỡng sau khi đã thấy kết quả.

### Sửa đổi ngưỡng (trước khi chạy bất kỳ cấu hình Tầng 2 nào)

Thay ngưỡng cố định 0.20 bằng quy tắc, chốt TRƯỚC khi thấy bất kỳ kết quả
Tầng 2 nào:

```
ngưỡng = max(0.20, biên_độ_bar_offset_của_baseline_cửa_sổ_ngắn)
```

Biên độ đo bằng cách chạy baseline pruned-8 (không Tầng 2) trên cửa sổ
ngắn ở 4 mốc offset (0/6/12/18) — vốn đã bắt buộc cho tiêu chí 6, không
phải phép đo mới ngoài phạm vi đăng ký. Phép đo chỉ dùng baseline, không
chạm tới treatment (Tầng 2), nên không rò rỉ thông tin về kết quả Tầng 2
sắp có — tại thời điểm sửa đổi này, thí nghiệm Tầng 2 (ablation) đã bị
dừng giữa chừng trước khi in ra bất kỳ kết quả nào, nên sửa đổi này chốt
trước khi thấy số Tầng 2 đầu tiên, đúng tinh thần tiền đăng ký.

**Đo xong.** Baseline pruned-8 cùng cửa sổ ngắn (2020-08-05→2026-08-04), 4
mốc offset (`reports/tier2_shortwin_baseline_offsets`):

| offset | Sharpe |
|---|---|
| 0 | 0.7543 |
| 6 | 0.7095 |
| 12 | 0.8915 |
| 18 | 1.2004 |

Biên độ = 1.2004 − 0.7095 = **0.4909**.

**Ngưỡng cuối cùng cho thí nghiệm Tầng 2: max(0.20, 0.4909) = 0.4909.**

### PHÁT HIỆN QUAN TRỌNG — độc lập với Tầng 2, phải xử lý trước

Biên độ 0.4909 này **chính là số của tiêu chí 6**, và **0.4909 > 0.3** —
ngưỡng tiêu chí 6 tự nó. Nghĩa là: **baseline pruned-8 cửa sổ NGẮN tự nó
FAIL tiêu chí 6**, trong khi cửa sổ DÀI pass thoải mái (biên độ 0.217).
Đây không phải vấn đề của Tầng 2 — Tầng 2 chưa chạy dòng nào — đây là bất
ổn walk-forward tái xuất hiện khi thu hẹp cửa sổ, độc lập với việc thêm
feature gì.

Hệ quả: bất kỳ kết quả Tầng 2 nào đo trên cửa sổ ngắn này đều đứng trên một
nền không ổn định sẵn. Một cải thiện Sharpe "vượt ngưỡng 0.4909" có thể chỉ
là cưỡi lên đúng loại bất ổn window-alignment đã thấy ở Tầng 1 (phiên
trước), không phải tín hiệu Tầng 2 thật — và một kết quả "dưới ngưỡng"
cũng khó diễn giải vì bản thân ngưỡng đã bị thổi phồng bởi bất ổn đó.
**Tạm dừng trước khi chạy ablation Tầng 2, chờ quyết định cách xử lý phát
hiện này** — không tự ý chọn hướng đi tiếp.

### KẾT LUẬN — thí nghiệm Tầng 2 BỎ (2026-08-06)

Chọn phương án: **bỏ hẳn cửa sổ ngắn này, kết luận ràng buộc dữ liệu
derivatives (funding/OI chỉ từ ~2020) làm phép so sánh quá nhiễu để rút ra
kết luận gì, ghi lại, dừng ở đây.**

**Quan trọng — phân biệt rõ:** đây KHÔNG phải kết luận "Tầng 2 không mang
thêm thông tin". Chưa từng chạy một cấu hình Tầng 2 nào để biết điều đó.
Kết luận đúng là: **không đo được** — cửa sổ ngắn duy nhất mà dữ liệu
derivatives cho phép có bất ổn walk-forward đủ lớn (biên độ bar-offset
0.4909, tự nó đã fail tiêu chí 6 dù không có Tầng 2) để nuốt chửng bất kỳ
tín hiệu Tầng 2 nào có thể có. Câu hỏi "funding/OI/basis có thêm thông tin
độc lập không" vẫn **để ngỏ**, không phải đã trả lời là không.

Không thử thu hẹp/mở rộng cửa sổ để tìm một cửa sổ ổn định hơn — đó sẽ là
đúng kiểu "tìm cần gạt" mà tiền đăng ký đã cam kết dừng lại khi không đạt
ngưỡng.

**Không ảnh hưởng tới bảng §4.9 hiện có** (6 PASS / 2 FAIL, đo trên cửa sổ
DÀI 2018-02-09→2026-08-04) — cửa sổ dài không có ràng buộc derivatives này,
tiêu chí 6 của nó (biên độ 0.217) không đổi.

### Trạng thái cuối

- Baseline pruned-8 cùng cửa sổ ngắn, cả 4 offset: xong, dùng để phát hiện
  vấn đề rồi dừng (`reports/tier2_shortwin_baseline`,
  `reports/tier2_shortwin_baseline_offsets`).
- Ablation Tầng 2: **không chạy** — bỏ thí nghiệm trước khi có bất kỳ số
  Tầng 2 nào.
- Code hạ tầng (`compute_tier2_features`, `compute_all_features`,
  `DerivativesLoader.load_perp_close`/`load_tier2_bundle`, wiring qua
  `main.py`/`backtest/backtester.py`) **giữ nguyên trong codebase** — đã
  implement đúng, lint/mypy/pytest xanh, smoke-test bằng dữ liệu thật xác
  nhận cơ chế đúng (căn index, z-score hợp lý). Không xoá vì bản thân code
  không sai — vấn đề là dữ liệu derivatives hiện có không đủ dài để đo
  trong điều kiện ổn định, không phải lỗi implementation. Có thể dùng lại
  nếu sau này có cách khác để kiểm định (vd. dữ liệu derivatives dài hơn từ
  nguồn khác, hoặc sửa được bất ổn walk-forward ở cửa sổ ngắn trước).
- Câu hỏi "Tầng 2 có thêm thông tin không" quay lại trạng thái **chưa biết**,
  không phải "không".

## Câu hỏi mở cho phiên sau

- `(full,5)`, `(full,11)`, `(diag,7)` được nhắc tới trong một yêu cầu chẩn
  đoán nhưng không tìm thấy định nghĩa ở đâu trong repo tại thời điểm viết
  file này (`docs/DECISIONS.md` không tồn tại trước bản này). Nếu đây là
  tên cấu hình đã thống nhất ở đâu đó ngoài repo, cần ghi định nghĩa chính
  xác vào đây khi nhắc lại.
- §4.9 còn 2 fail (1, 3), cả hai đều là khoảng cách Sharpe/biên-vượt-benchmark,
  không phải bug hay bất ổn cấu trúc. Hướng nào để thu hẹp — thêm return
  signal (chỉ `log_return_5` đã xác nhận resolvable qua ablation), tăng
  `is_bars`, đổi rebalance threshold — chưa được quyết định, để phiên sau.
- Thí nghiệm Tầng 2 đã bỏ (xem mục ngay trên) — câu hỏi "funding/OI/basis
  có thêm thông tin độc lập không" vẫn mở, chưa có cách đo trong điều kiện
  ổn định với dữ liệu hiện có (funding/OI chỉ từ ~2020).
- Bất ổn walk-forward (biên độ bar-offset) tăng mạnh khi cửa sổ ngắn lại:
  0.217 ở cửa sổ dài (~8.5 năm) nhưng 0.4909 ở cửa sổ ngắn (~6 năm, cùng bộ
  8 feature, cùng mọi tham số khác). Chưa rõ cơ chế — có thể liên quan tới
  ít window walk-forward độc lập hơn (`samples_per_param`-kiểu vấn đề nhưng
  theo trục số WINDOW thay vì số feature). Chưa điều tra, để phiên sau nếu
  cần dùng cửa sổ ngắn cho việc gì khác.

---

## Forward test — tiền đăng ký (2026-08-06)

Ghi lại **trước khi chạy lần đầu tiên** — cùng tinh thần tiền đăng ký đã
dùng cho thí nghiệm Tầng 2 ở trên. Theo `docs/VALIDATION_REPORT.md` mục 6:
tập kiểm định lịch sử đã cạn (bị nhìn nhiều lần trong quá trình chẩn đoán,
không còn ngoài mẫu — mục 3.4), không xây Phase 8-12, chuyển sang forward
test ghi log, không đặt lệnh. Code: `forward/logger.py`,
`forward/config_frozen.yaml`, `forward/README.md`.

**Ngày bắt đầu:** 2026-08-06.

**Hash cấu hình đóng băng** (`forward/config_frozen.yaml`, bản copy nguyên
văn `config/settings.yaml` tại ngày bắt đầu):

```
SHA256: be741c659bc5a11d607955e64ec27cb0b194c1b6c368ca09704b8d056a1ec15c
```

Đóng băng cùng lúc với hash này — coi là MỘT khối duy nhất: `FEATURE_SUBSET`
trong `forward/logger.py` (8 cột pruned-8 đã kiểm định:
`log_return_1, log_return_5, realized_vol_20, vol_ratio_5_20, adx_14,
sma50_slope, trade_count_zscore_50, trade_count_sma10_slope`) — không nằm
trong `settings.yaml` (tham số CLI-only ở `main.py`), nên phải đóng băng
làm hằng số nguồn đã commit.

**Mốc đánh giá:**

| mốc | ngày | mục đích |
|---|---|---|
| 3 tháng | 2026-11-06 | chỉ xem hành vi (regime có đổi hợp lý không, có bug ghi log không) — KHÔNG rút kết luận thống kê, quá ít bar |
| 6 tháng | 2027-02-06 | đọc sơ bộ (xu hướng equity 4 track, có bất thường rõ ràng không) — vẫn KHÔNG đủ bar cho Sharpe đáng tin |
| 12 tháng | 2027-08-06 | thống kê đầu tiên có nghĩa (Sharpe/Calmar/max DD trên ~365 bar thật, ngoài mẫu, chưa từng bị nhìn trước đó) |

**KHÔNG sửa cấu hình giữa chừng.** `forward/config_frozen.yaml` và
`FEATURE_SUBSET` đóng băng tại ngày bắt đầu ở trên — `load_frozen_settings()`
kiểm tra lại sha256 mỗi lần chạy, sửa file sau đó làm chương trình dừng
ngay (không âm thầm chạy tiếp). Muốn sửa bất kỳ tham số nào (feature,
retrain interval, cost model, ngưỡng rebalance, ...): **KẾT THÚC thí
nghiệm này, bắt đầu thí nghiệm mới** với `config_frozen.yaml`/hash mới,
ghi rõ lý do và ngày bắt đầu mới vào đây — không sửa tại chỗ, không nối
tiếp log.csv cũ sang cấu hình khác.

### Bổ sung hạ tầng (2026-08-07) — không đụng cấu hình đóng băng

Ba việc, không sửa `forward/config_frozen.yaml`/`FEATURE_SUBSET` (không mở
thí nghiệm mới):

1. **Warnings không filter, chuyển hướng vào `forward/warnings.log`** —
   timestamp + bar date + toàn văn, cột `warning_count` thêm vào
   `forward/log.csv`. Lý do: 12 tháng không người trông, lọc warning là mất
   tín hiệu về thay đổi hành vi giữa chừng. Không có `filterwarnings
   ("ignore", ...)` nào trong `forward/logger.py` (khác pytest, vốn chủ ý
   bỏ qua vài loại quen thuộc). `forward/warnings.log` thêm ngoại lệ tường
   minh trong `.gitignore` (`!forward/warnings.log`) — mặc định `*.log` sẽ
   bỏ qua nó, nhưng đây là bằng chứng thí nghiệm, không phải log runtime.

2. **Kiểm tra lại `predict_regime_filtered` có chuẩn hoá đúng trong log
   space không** (nghi ngờ dấy lên từ warning `RuntimeWarning: divide by
   zero/overflow encountered in matmul` thấy lúc chạy). **Kết luận: không
   có bug.** Cô lập bằng `warnings.simplefilter("error")` quanh từng lệnh
   gọi riêng: `select_and_train` (đường `.fit()` EM/k-means của hmmlearn/
   sklearn) RAISE warning; `predict_regime_filtered` KHÔNG raise gì, chạy
   sạch, `state_probabilities` hợp lệ (tổng ≈ 1.0, không NaN/Inf). Đo trực
   tiếp `log_alpha` trên dữ liệu thật (2657 bar): khoảng [-22815, -9.2] ở
   bar cuối — cách rất xa giới hạn float64. Lý do không cần chuẩn hoá mỗi
   bước: trong log space, `log_alpha` giảm gần tuyến tính theo t (không
   phải cấp số nhân như xác suất thường), `logsumexp` đã tự ổn định từng
   bước, và exp() duy nhất của toàn thuật toán (ở bước chuẩn hoá cuối cùng)
   luôn nhận input ≤ 0. Đã ghi chi tiết vào docstring
   `HMMRegimeEngine._forward_log_alpha` (`core/hmm_engine.py`) — không sửa
   code, chỉ xác nhận và ghi lại bằng chứng.

3. **launchd thay cron** — `forward/com.regime-trader-crypto.forward-test.plist`
   (LaunchAgent, `StartCalendarInterval` 08:00 giờ địa phương = 01:00 UTC).
   Lý do đổi từ gợi ý cron ban đầu: cron không chạy khi máy ngủ, launchd tự
   bù khi máy thức dậy — cần thiết cho 12 tháng không người trông trên
   laptop. Hướng dẫn nạp/kiểm tra/gỡ: `forward/README.md`, mục "Lịch chạy
   tự động (launchd)".

---

## Sửa CLAUDE.md bất biến #12 (2026-08-06)

**Sửa đổi có chủ ý, sau khi đã thấy kết quả §4.9 (6/8 PASS) — không phải
diễn giải lại quy tắc gốc.** Ghi lại đây, đủ chi tiết để không cần đoán lại
ý định khi đọc lại sau này. Nội dung đầy đủ đã sửa: `CLAUDE.md`, bất biến
#12.

**Quy tắc gốc** (viết trước Phase 4, trước khi có bất kỳ kết quả nào):
"Sau Phase 4, đối chiếu 8 tiêu chí ở §4.9 của spec. Không đủ 8/8 thì không
xây tầng thực thi." Quy tắc gốc đã làm đúng việc nó phải làm — chặn dự án
xây risk manager/order executor cho tới khi 8 tiêu chí được đối chiếu bằng
số thật (`docs/VALIDATION_REPORT.md`), không phải bằng cảm giác "chắc là
được rồi".

**Quy tắc mới:** Xây tầng thực thi được phép ở mức TESTNET. KHÔNG được vào
mainnet, không được đặt lệnh bằng tiền thật, cho tới khi forward test đạt
kết quả ở mốc 12 tháng (2027-08-06) và §4.9 được đánh giá lại trên dữ liệu
forward.

**Lý do dời cổng** (testnet được phép dù chưa đủ 8/8):
- 6/8 với hai FAIL nằm trong sai số đo đã lượng hoá được (0.059 và 0.173,
  cả hai nhỏ hơn biên độ nhiễu bar-offset 0.217 — `docs/VALIDATION_REPORT.md`
  mục 3.1).
- Testnet không có rủi ro tài chính.
- Lỗi thực thi (order sizing, idempotency qua `orderLinkId`, stop loss,
  circuit breaker) chỉ lộ ra khi chạy thật qua sàn — không lộ ra khi chỉ
  backtest, kể cả backtest kỹ tới đâu.

**Lý do GIỮ cổng ở mainnet** (không dời luôn cả hai cổng cùng lúc):
Chiến lược vẫn **chưa** chứng minh được lợi thế Sharpe so với hai benchmark
không dùng HMM: `sma200_trend` (0.9567) và `static_vol_target` (0.9142),
trong khi strategy chỉ đạt 0.9411 (`docs/VALIDATION_REPORT.md` mục 2). Xây
xong tầng thực thi (kỹ thuật) không phải bằng chứng nên dùng nó với tiền
thật (chiến lược).

**Điều gì KHÔNG đổi:** cơ chế tự nhắc — nếu ai (kể cả chính người viết
`CLAUDE.md`) bảo bỏ qua bước này lần nữa (vào mainnet trước mốc 12 tháng,
hoặc trước khi §4.9 đánh giá lại trên dữ liệu forward), quy tắc vẫn yêu
cầu nhắc rằng chính người đó đã viết ra nó, và hỏi lại một lần nữa trước
khi làm — không tự động vượt qua chỉ vì đã có một lần nới lỏng trước đó.

---

## Phase 8 — Risk manager: hiệu chỉnh ngưỡng circuit breaker bằng dữ liệu

Theo `prompts/phase-08-risk-manager.md`: "Hiệu chỉnh ngưỡng bằng dữ liệu,
không dùng số mặc định." Lấy phân phối lợi nhuận ngày/tuần thật từ
`reports/pruned8_base/equity_curve.csv` (Phase 6, 2290 bar daily return,
2284 bar weekly 7-ngày rolling return, cửa sổ 2018-02-09 → 2026-08-04 —
cùng dữ liệu đã cho kết quả §4.9 trong `docs/VALIDATION_REPORT.md`).

**Phân vị đã in ra** (âm = ngày/tuần lỗ, magnitude = trị tuyệt đối):

| phân vị | daily return | weekly (7d) return |
|---|---|---|
| p0.5 | −6.344% | −13.718% |
| p1 | −5.479% | −12.042% |
| p2 | −4.300% | −9.946% |
| p2.5 | −3.850% | −9.481% |
| p3 | −3.614% | −9.172% |
| p5 | −2.912% | −7.441% |
| p10 | −1.746% | −4.922% |

Max drawdown peak-to-trough toàn kỳ (tham khảo, không dùng để hiệu chỉnh
reduce/halt daily-weekly): **−54.80%**.

**Quy tắc áp dụng** (Brain-Crypto-Bybit.md §5.2): ngưỡng "giảm size" ở
phân vị 2–3%, ngưỡng "dừng" ở phân vị 0.5%. Chọn p2.5 cho "giảm size" (giữa
khoảng 2–3%) và p0.5 cho "dừng":

| tham số | giá trị cũ (đoán) | giá trị mới (hiệu chỉnh) | nguồn |
|---|---|---|---|
| `daily_dd_reduce_pct` | 4.0% | **3.85%** | \|p2.5 daily\| |
| `daily_dd_halt_pct` | 6.0% | **6.34%** | \|p0.5 daily\| |
| `weekly_dd_reduce_pct` | 10.0% | **9.48%** | \|p2.5 weekly\| |
| `weekly_dd_halt_pct` | 14.0% | **13.72%** | \|p0.5 weekly\| |
| `peak_dd_halt_pct` | 20.0% | **20.0% (giữ nguyên)** | trần tuyệt đối, không phải phân vị — xem dưới |

Bốn ngưỡng daily/weekly đầu chỉ lệch số mặc định gốc **0.15–0.52 điểm
phần trăm** — số mặc định trong spec hoá ra đã khá gần với phân vị thật
của chính dữ liệu này, không phải đoán tuỳ tiện, nhưng vẫn hiệu chỉnh đúng
theo dữ liệu thay vì giữ nguyên số tròn theo quán tính.

`peak_dd_halt_pct` **không** hiệu chỉnh bằng phân vị — đây là trần tuyệt
đối cho một sự kiện hiếm/nghiêm trọng (dừng vô thời hạn, cần can thiệp thủ
công), không phải ngưỡng đo trên phân phối ngày/tuần thông thường. Giữ
20.0% theo spec: thấp hơn nhiều so với max drawdown lịch sử thật (−54.80%)
nên sẽ kích hoạt sớm, đúng vai trò phanh khẩn cấp.

Đã cập nhật `config/settings.yaml`, mục `risk.circuit_breaker` — kèm
comment trỏ lại đúng bảng này. Cũng thêm các tham số §5.1/§5.4/§5.5 trước
đó chưa có field trong settings.yaml (`min_cash_buffer_pct`,
`max_trades_per_day`, `max_leverage`, `spread_max_pct`,
`usdt_depeg_threshold_pct`, `duplicate_order_window_seconds`) — đúng
CLAUDE.md bất biến #14 (không magic number ngoài config).

**Không ảnh hưởng gì tới forward test đang chạy** — `forward/config_frozen.yaml`
là bản copy độc lập, không đọc lại `config/settings.yaml`, và
`forward/logger.py` không dùng `risk_manager` (xem `forward/README.md`).

---

## Đổi sàn Bybit -> Binance (ccxt) (2026-08-06)

**Nguyên nhân:** Bybit chặn theo khu vực (regulatory restrictions) từ môi
trường vận hành hiện tại — xác nhận bằng gọi thật, không suy luận:
`retCode 10024` trên cả `api-testnet.bybit.com` lẫn `api.bybit.com`. Không
kết nối được cả hai môi trường, kể cả ở tầng public endpoint (không phải
lỗi xác thực key như đã gặp và ghi nhận trước đó ở mục Phase 9 nghiệm thu
— đây là một vấn đề khác, nghiêm trọng hơn: không phải "key sai", mà là
"không bao giờ tới được sàn"). `broker/bybit_client.py` giữ nguyên trong
repo, đánh dấu deprecated trong docstring — không xoá, vì nó là bằng chứng
cho quyết định này (retCode thật, toàn bộ lịch sử sửa `_call_with_retry`
whitelist trước khi phát hiện chặn khu vực) và ví dụ tham khảo cho thấy
`ExchangeClient` ABC hoạt động đúng thiết kế ra sao khi đổi sàn.

**Sàn thay thế:** Binance, qua thư viện `ccxt` thay vì SDK riêng
(`python-binance` hay tương tự). Ràng buộc thật ở đây là **khả năng truy
cập theo khu vực**, không phải chất lượng API của một sàn cụ thể — nếu
Binance sau này cũng bị chặn, hoặc cần thử một sàn khác, `ccxt` biến việc
đổi sàn thành đổi `config/settings.yaml: exchange.name` (chuỗi khớp tên
module `ccxt.<exchange_id>`) thay vì viết lại một implementation mới từ
đầu. Cái giá phải trả: mất một số tối ưu riêng của Bybit (vd. rate limiter
token-bucket 600 req/5s đo đúng giới hạn Bybit v5 — `ccxt`'s
`enableRateLimit` là cơ chế chung, không tinh chỉnh riêng cho từng sàn ở
mức đó). Đánh đổi hợp lý: dự án đặt vài lệnh/ngày (`max_trades_per_day: 6`),
không phải market making — không cần tối ưu rate-limit ở mức micro-giây.

**Bỏ WebSocket, chuyển hẳn sang REST polling** — quyết định kiến trúc đi
kèm, không phải hệ quả phụ của việc đổi sàn:
- Bot chạy bar `1D` (`exchange.timeframe`) — polling REST 30-60s dư sức
  đáp ứng tần suất cần thiết; WebSocket là công nghệ cho tần suất cao
  (tick-by-tick, market making) mà dự án không có nhu cầu đó.
- Đổi lại: bỏ được toàn bộ độ phức tạp của heartbeat/phát hiện mất kết nối
  im lặng/reconnect-với-backoff — `broker/base.py::ExchangeClient` không
  còn `subscribe_klines`/`subscribe_executions`; `data/market_data.py`
  không còn `is_feed_alive()`/cache bar mới nhất; `broker/position_tracker.py`
  không còn `on_execution()` (từng được gọi qua `subscribe_executions`,
  nay đã đối soát định kỳ qua `poll()` thay thế — cùng logic
  `reconcile_on_startup()`, gọi thêm mỗi vòng main loop).
- Với REST polling, không còn khái niệm "kết nối còn sống nhưng dữ liệu
  cũ" cần một cơ chế phát hiện riêng — mỗi lần gọi HOẶC thành công HOẶC
  raise ngay tại chỗ gọi.

**Kiểm chứng ranh giới ABC không bị rò rỉ** (đúng tiêu chí đặt ra trước khi
làm): `broker/order_executor.py` — lớp gọi `ExchangeClient` nhiều nhất
(`get_instrument_rules`/`get_balance`/`get_positions`/`submit_order`/
`get_open_orders`/`cancel_order`) — **không cần sửa một dòng logic nào**.
Xác nhận bằng cả hai cách: (1) toàn bộ 15 test hiện có của nó
(`tests/test_orders.py`) pass nguyên vẹn không sửa; (2) đọc lại từng lệnh
gọi `self.exchange_client.*` — tất cả đều dùng đúng chữ ký ABC không đổi.
Một dòng COMMENT (không phải logic) trong `_wait_for_fill_or_cancel` có
tham chiếu tên `subscribe_executions` đã xoá — sửa lại nội dung comment
cho khớp cơ chế mới (poll định kỳ thay vì đẩy qua WebSocket), không đổi
hành vi. Đây chính là bằng chứng cho lý do `broker/base.py::ExchangeClient`
tồn tại: đổi sàn hoàn toàn (Bybit/pybit -> Binance/ccxt) và đổi cả cơ chế
polling (WebSocket -> REST) mà tầng thực thi phía trên không hề biết.

**Chi tiết kỹ thuật đáng ghi lại** (verify bằng gọi thật/introspection,
không suy luận từ tài liệu):
- `ccxt.NetworkError` (và lớp con: `RequestTimeout`, `ExchangeNotAvailable`,
  `RateLimitExceeded`, `DDoSProtection`, `InvalidNonce`, `OnMaintenance`)
  = nhất thời, retry có backoff mũ; `ccxt.ExchangeError` (và lớp con:
  `AuthenticationError`, `InsufficientFunds`, `InvalidOrder`, `BadSymbol`,
  `OrderNotFound`, `PermissionDenied`...) = sàn cố tình từ chối, thất bại
  ngay — cùng triết lý whitelist đã áp dụng cho `bybit_client.py`, dựng
  trên cây kế thừa exception khác của ccxt.
- `orderLinkId` -> `params={"clientOrderId": ...}` khi gọi
  `create_order()` — ccxt tự map sang `newClientOrderId` gốc của Binance
  (xác nhận bằng grep trực tiếp `ccxt/binance.py::create_order`, không
  suy luận từ tài liệu ccxt).
- Binance có CẢ market spot (`"BTC/USDT"`) LẪN USDT-M perpetual
  (`"BTC/USDT:USDT"`) cùng chia sẻ id thô `"BTCUSDT"` trong
  `markets_by_id` — `CCXTClient._to_ccxt_symbol()` cố tình KHÔNG dùng
  `exchange.market("BTCUSDT")` (thứ tự giải quyết nhập nhằng không đảm
  bảo ổn định qua phiên bản ccxt), mà tách hậu tố `quote_asset` tường
  minh rồi tra theo ký hiệu hợp nhất `"BTC/USDT"` — luôn đúng spot, đúng
  phạm vi dự án (không leverage).
- `fetch_open_orders()`/`cancel_order()` không kèm `symbol` bị chính ccxt
  chặn trên Binance (`ExchangeError` "WARNING... 10 times more" rate-limit
  weight) — khác Bybit v5 cho phép query "mọi symbol" miễn phí. Vì dự án
  chỉ giao dịch một symbol duy nhất, `CCXTClient` lưu `symbol` đã cấu hình
  từ constructor (bắt buộc truyền, không default ngầm) và luôn truyền
  tường minh — tránh cả lỗi lẫn phí rate-limit thừa, đồng thời đơn giản
  hơn cách `BybitClient` phải tra `symbol` từ danh sách lệnh mở trước khi
  huỷ.

**`ops/health_check.py`** cũng đổi theo (ngoài phạm vi yêu cầu ban đầu,
nhưng để lại sẽ khiến health check kiểm tra nhầm sàn đã bị chặn thay vì
sàn thật đang dùng — một bug ẩn khó phát hiện nếu không sửa cùng lúc):
đọc `exchange.name` từ `settings.yaml` thay vì hardcode `ccxt.bybit`, biến
môi trường đổi tên `BYBIT_API_KEY`/`BYBIT_API_SECRET`/`BYBIT_TESTNET` ->
`EXCHANGE_API_KEY`/`EXCHANGE_API_SECRET`/`EXCHANGE_TESTNET` (đọc được cả
tên cũ làm fallback, `.env` có sẵn từ trước migration không bị hỏng ngay
lập tức). Xác nhận bằng gọi thật (`testnet.binance.vision`): `exchange_reachable`
OK 155ms, `exchange_authenticated` FAIL đúng lý do (thiếu key mới, không
phải lỗi code).

**`config/settings.yaml`**: `exchange.name: bybit -> binance`. KHÔNG thêm
field `exchange.sandbox` như gợi ý ban đầu — giữ nguyên `exchange.testnet`
(đã dùng ở CLAUDE.md #6, `ops/health_check.py`, `ops/RUNBOOK.md` từ trước);
`CCXTClient` gọi `exchange.set_sandbox_mode(True)` khi `testnet=True`, tên
tham số/field không cần trùng tên phương thức ccxt dùng nội bộ. Thêm field
trùng nghĩa chỉ để khớp gợi ý ban đầu sẽ tạo hai nguồn sự thật cho cùng
một khái niệm.

**Bổ sung cùng ngày: bỏ fallback `BYBIT_*` trong `ops/health_check.py`.**
Fallback đọc tên biến cũ (`BYBIT_API_KEY`/`BYBIT_API_SECRET`/`BYBIT_TESTNET`)
ở trên có mục đích rõ: `.env` có sẵn từ trước migration không bị hỏng
ngay lập tức. Sau khi xác nhận migration hoạt động đúng, fallback đó tự
nó là một cách để cấu hình sai (gõ nhầm/quên đổi tên biến) lặng lẽ vẫn
"hoạt động" bằng key/flag của sàn đã ngừng dùng — đúng loại lỗi mà một
health check tồn tại để bắt, không phải để tạo ra. Bỏ hẳn fallback: chỉ
đọc `EXCHANGE_API_KEY`/`EXCHANGE_API_SECRET`/`EXCHANGE_TESTNET`; thiếu
biến nào trong hai biến credential thì `exchange_authenticated` FAIL và
nêu đúng tên biến còn thiếu (không còn thông báo chung chung liệt kê cả
hai bất kể biến nào thật sự thiếu). `.env.example` bỏ mọi dòng nhắc tới
`BYBIT_*`. `.env` thật (không commit) vẫn còn `BYBIT_API_KEY`/
`BYBIT_API_SECRET`/`BYBIT_TESTNET` ở trên — giờ hoàn toàn không có tác
dụng (trước là "dự phòng", giờ là dead config), không xoá tự động vì đó
là file người vận hành tự quản lý, không phải phạm vi sửa của thay đổi
này. `tests/test_health_check.py` thêm test xác nhận đúng việc bỏ
fallback: set `BYBIT_API_KEY`/`BYBIT_API_SECRET` (không set `EXCHANGE_*`)
phải vẫn FAIL.

---

## Lấp 4 test skip trong test_hmm.py — phát hiện bug thật ở `_extract_variances` (2026-08-07)

`tests/test_hmm.py` có 4 test `pytest.skip("TODO: Phase 2")` từ lúc
`core/hmm_engine.py` còn là stub — module đó đã implement đầy đủ từ lâu,
test chưa bao giờ viết lại. Viết 14 test thay 4 skip (BIC selection, gán
nhãn regime theo return-rank, vol-rank độc lập, bộ lọc ổn định/hysteresis,
flicker rate) — không đi qua `predict_regime_filtered()` cho phần
stability/flicker (gọi thẳng `_update_stability()`, state machine thuần
không đụng `self.model`) vì ép forward algorithm đi đúng một chuỗi
argmax mong muốn qua nhiều bar liên tiếp đòi hỏi tinh chỉnh means_/
covars_/transmat_ rất mong manh, dễ flaky.

**Bug thật phát hiện lúc viết test** (không phải đọc code):
`HMMRegimeEngine._extract_variances()` giả định `self.model.covars_`
giữ nguyên shape gọn theo `covariance_type` (`(n_components, n_features)`
cho `diag`, `(n_features, n_features)` cho `tied`, v.v.) — SAI với
hmmlearn 0.3.3 (phiên bản đang dùng, xác nhận bằng fit thật cả 4 loại,
không suy luận từ tài liệu): property công khai `covars_` LUÔN trả về ma
trận ĐẦY ĐỦ `(n_components, n_features, n_features)` bất kể
`covariance_type` (hmmlearn tự "phồng" `diag`/`tied`/`spherical` về full
qua `hmmlearn/utils.py::fill_covars` trước khi trả ra — `_covars_`
private mới giữ shape gọn, không phải thứ hàm này đọc). Kết quả: nhánh
`full` (đọc `covars[s,i,i]`) tình cờ đúng vì đó đúng là shape thật;
`diag` đọc sai (lấy nguyên một HÀNG của ma trận full thay vì phần tử
đường chéo — trộn lẫn cả covariance chéo vào "variance"); `tied` đọc sai
vị trí hoàn toàn (`covars[feature_idx, feature_idx]` trên mảng 3D thực
chất đang lấy chỉ số THEO COMPONENT, không phải theo feature);
`spherical` trả nguyên ma trận 3D thay vì một số vô hướng.

Không lộ ra trước giờ vì `settings.yaml: hmm.covariance_type: full` là
cấu hình production DUY NHẤT từng chạy qua — bug nằm ở ba nhánh
`diag`/`tied`/`spherical`, sẽ lộ ngay khi ablation thử `covariance_type`
khác (CLAUDE.md bất biến #13 khuyến khích đúng việc này). Hậu quả nếu
không phát hiện: `vol_rank` (xếp hạng volatility giữa các regime) sai âm
thầm → `recommended_strategy_type` (LOW_VOL/MID_VOL/HIGH_VOL) và
`max_allocation_pct` trong `RegimeInfo` sai theo, không có exception nào
báo hiệu.

**Sửa:** vì `covars_` luôn đầy đủ bất kể `covariance_type`, bỏ hẳn nhánh
theo loại — luôn đọc `covars[s, feature_idx, feature_idx]`. Đơn giản hơn
bản cũ (không cần biết `covariance_type` để đọc đúng), và đúng cho cả 4
loại — xác nhận bằng test mới `test_extract_variances_matches_covars_diagonal_for_every_covariance_type`
(parametrize cả 4 loại, fit thật, so với `model.covars_[s,i,i]` tính độc
lập).

**Tự kiểm chứng theo CLAUDE.md #16** (mutation trước khi tin): mutate 5
chỗ (revert fix `_extract_variances`, đảo BIC chọn cao nhất thay vì thấp
nhất, bỏ sort theo return khi gán nhãn, bỏ chờ `stability_bars`, bỏ cắt
`flicker_window`) — đúng 11/14 test đỏ khớp từng mutation (3 test không
liên quan tới mutation nào vẫn xanh đúng, bao gồm nhánh `full` của
`_extract_variances` — không bị mutation A đụng tới, đúng như dự đoán).
Revert lại bản gốc + fix thật trước khi chạy full suite.

207 passed / 0 skipped (trước: 193 passed / 4 skipped — 4 skip đã lấp
hết). ruff + mypy sạch (trừ 8 lỗi pre-existing ở `test_forward_logger.py`,
không liên quan, chưa xử lý).

---

## Phase 10 — Main loop (`main.py::run_live_loop`) (2026-08-07)

Theo `prompts/phase-10-main-loop.md` + `docs/Brain-Crypto-Bybit.md` §Phase
7, điều chỉnh cho kiến trúc REST polling đã đổi ở Phase 9 (không có bước
"nhận bar qua WebSocket"/"đóng WebSocket lúc tắt" trong spec gốc — thay
bằng poll REST định kỳ, xem mục "Đổi sàn Bybit -> Binance (ccxt)" ở trên).

**Tái sử dụng thay vì viết lại:** `ops/health_check.py::check_exchange_reachable/
authenticated` làm bước 1-2 của khởi động (kết nối + xác thực + đồng bộ
thời gian) — cùng logic đã xác nhận bằng mạng thật ở Phase 9, không viết
lại lần hai. `core/signal_generator.py::SignalGenerator` (dựng ở phase
trước, KHÔNG có caller/test nào cho tới bản này) đúng là mảnh ghép
HMM→strategy→trend_gate→risk_manager cần cho main loop — chỉ cần đổi
`generate()` trả về thêm `regime_state`/`is_flickering` (đóng gói thành
`SignalGeneratorResult`) vì `main.py` cần hai giá trị đó để log/ghi
`state_snapshot.json` mà bản gốc tính xong rồi vứt.

**Phát hiện quan trọng lúc thiết kế khôi phục sau restart:** `broker/
order_executor.py::OrderExecutor._current_stops` sống trong bộ nhớ, mất
khi tiến trình chết. Không nạp lại tường minh thì `modify_stop()` ĐẦU
TIÊN sau restart coi `current=None` (chưa từng có stop) và chấp nhận BẤT
KỲ giá trị nào — kể cả RỘNG HƠN stop thật đã đặt trước khi crash — vi
phạm CLAUDE.md bất biến #5 (chỉ siết, không bao giờ nới) một cách hoàn
toàn im lặng, không exception nào báo hiệu. Thêm
`OrderExecutor.restore_known_stop()` (nạp thẳng, không qua kiểm tra siết/
nới — đây là NẠP LẠI trạng thái đã biết, không phải một quyết định sửa
stop mới) — `run_live_loop()` gọi nó ngay sau khi đọc `state_snapshot.json`,
TRƯỚC bất kỳ lệnh gọi `modify_stop()` nào khác. Đây là loại lỗi mà
CLAUDE.md #16 (mutation trước khi tin) và bài học "restart là nơi bất
biến dễ vỡ nhất trong im lặng nhất" (docs/STATE.md) muốn ngăn — không lộ
ra bằng đọc code hay chạy happy-path, chỉ lộ ra khi cố tình nghĩ về đúng
kịch bản restart-giữa-lúc-đang-giữ-stop.

**Stop loss trên spot** — Bybit/Binance spot không có lệnh stop native
qua `broker/order_executor.py` hiện tại (chỉ LIMIT/MARKET, xem
`broker/base.py::OrderType`). Bot tự theo dõi: mỗi bar, nếu
`close_price <= tracked_stop` thì `close_position()` NGAY, dừng ở đó,
KHÔNG sinh signal mới cho bar đó (position coi như đã đóng). Đây là điểm
duy nhất `process_one_bar()` chủ động đóng vị thế mà không đi qua
`SignalGenerator`/`RiskManager` — chọn thiết kế này vì stop loss là biện
pháp bảo vệ CUỐI CÙNG (CLAUDE.md bất biến #5), không nên phụ thuộc vào
risk_manager approve nó như một signal thường.

**`reset_daily()`/`reset_weekly()` (CLAUDE.md bất biến #10):** timeframe
`1D` nghĩa là MỖI bar đã là một ngày mới — `reset_daily()` gọi vô điều
kiện mỗi bar (không cần kiểm tra ranh giới, luôn đúng); `reset_weekly()`
chỉ khi `bar_ts.weekday() == 0` (Thứ Hai). Gọi TRƯỚC `SignalGenerator.generate()`
(tức trước `circuit_breaker.update()` của bar đó) — để baseline
daily/weekly là equity đóng cửa của bar HÔM TRƯỚC, không phải equity của
chính bar đang xử lý.

**"Lỗi HMM: giữ nguyên regime cũ" (spec §Xử lý lỗi)** — KHÔNG bắt riêng
một `except` quanh lệnh gọi `hmm_engine.predict_regime_filtered()` bên
trong `process_one_bar()`. Nếu `SignalGenerator.generate()` raise (vì bất
kỳ lý do gì, kể cả lỗi HMM), `process_one_bar()` raise theo, và
`run_live_loop()`'s catch-all vòng ngoài bắt nó, log traceback, rồi ghi
lại `state` — biến này vẫn là kết quả của lần `process_one_bar()` THÀNH
CÔNG gần nhất (chưa bao giờ bị gán lại bởi lần gọi lỗi dở dang) — hiệu
quả giống hệt "giữ nguyên regime hiện tại" mà không cần hai lớp try/except
lồng nhau xử lý cùng một kết quả. Cùng nguyên tắc cho "mất data feed": lỗi
mạng lúc `history_loader.load()` cũng rơi vào catch-all này, vòng lặp tiếp
tục ở lần poll kế tiếp, không có lệnh mới nào được gửi trong lúc đó (stop
cũ vẫn còn hiệu lực phía bot).

**`_latest_closed_bar_date()` trùng logic `forward/logger.py::latest_closed_bar_date`
CỐ TÌNH không import từ đó** — `forward/` tự cô lập hoàn toàn khỏi phần
còn lại hệ thống (docstring module đó: "KHÔNG import broker.* ở bất cứ
đâu"), thí nghiệm tiền đăng ký 12 tháng cần giữ nguyên trạng suốt kỳ. Live
loop phụ thuộc ngược vào `forward/` — dù chỉ một hàm thuần 4 dòng vô hại —
phá vỡ tính đối xứng của ranh giới đó. Trùng lặp rẻ hơn.

**Xác nhận bằng chạy thật, không chỉ unit test** (dù testnet bị chặn ở
tầng tài khoản GitHub, xem mục "Testnet đang bị chặn" — `exchange_reachable`
KHÔNG bị chặn, chỉ các endpoint cần key mới bị):
- `python main.py --dry-run` chạy thật tới `testnet.binance.vision`:
  `exchange_reachable` OK 178ms, `InstrumentRules(BTCUSDT)` đúng, vào tới
  bước train HMM thật (dừng có chủ đích, train đầy đủ tốn nhiều phút).
- `state/trading_halted.lock` thủ công → `python main.py --dry-run`
  thoát NGAY exit 1, KHÔNG gọi mạng, in đúng nội dung lock + hướng dẫn.
- `grep -rn "is_market_open\|market_hours" .` — 0 kết quả.

**Tự kiểm chứng bằng mutation (CLAUDE.md #16):** 5 mutation trên
`process_one_bar`/`run_live_loop`/`load_state_snapshot` — đúng 5 test
liên quan đỏ, 9 không liên quan vẫn xanh. Revert lại bản thật trước khi
chạy full suite.

**Chưa xác nhận được** (đúng lý do — cần testnet thật, đang bị chặn ở
tầng tài khoản, KHÔNG phải chưa xây): kill+restart qua tiến trình thật
đầu-cuối (đã xác nhận riêng từng phần: `state_snapshot.json` round-trip
bằng unit test, `restore_known_stop()` bằng unit test); `--dry-run` chạy
liên tục 24 giờ; `submit_order`/`close_position`/`modify_stop` thật qua
mạng.

227 passed / 0 skipped. ruff + mypy sạch toàn bộ 52 file (bao gồm
`test_forward_logger.py`, đã sửa xong ở mục trước).

---

## Kiểm tra hồi tố: Phase 10 có đổi hành vi forward pipeline không? (2026-08-07)

Yêu cầu: xác nhận bằng thực nghiệm (không phải đọc code) rằng Phase 10
(`877ddc2`, xem mục trên) không âm thầm đổi output pipeline forward giữa
chừng thí nghiệm 12 tháng.

**Phương pháp:** `git worktree add /tmp/pre-p10 3edc6d4` (commit cha trực
tiếp của `877ddc2`). Chạy `_run_golden_pipeline()` — hàm THẬT trong
`tests/test_forward_golden.py`, không viết lại pipeline riêng cho việc
này — ở cả `/tmp/pre-p10` lẫn HEAD hiện tại, cùng dữ liệu tổng hợp seed cố
định (`_SEED=12345`). So JSON kết quả bằng script độc lập bên ngoài, field-
by-field, không qua logic assert của chính file test (tránh tự tin nhầm
vào công cụ đang được dùng để kiểm tra chính nó).

**Kết quả: KHỚP 100%.** 60/60 bar, mọi field categorical/string khớp
tuyệt đối, `regime_probability` lệch đúng `0.0` (bit-for-bit, không chỉ
trong dung sai `1e-6` của test). Đối chiếu thêm với
`tests/golden/forward_baseline.json` đã commit — cũng khớp 100% với cả
hai lần chạy. Ba nguồn (baseline đã commit, pre-Phase-10, HEAD) đồng nhất.

**Vì sao khớp — xác nhận bằng `git diff --stat 3edc6d4 HEAD -- core/`**:
Phase 10 chỉ đổi MỘT file trong `core/` — `core/signal_generator.py`
(`generate()` đổi kiểu trả về thành `SignalGeneratorResult`, thêm
`regime_state`/`is_flickering`). `_run_golden_pipeline()` KHÔNG dùng
`SignalGenerator` — nó gọi thẳng `HMMRegimeEngine`/`StrategyOrchestrator`/
`StructuralTrendGate`/`compose_layer_allocations`, bỏ qua lớp bọc đó hoàn
toàn. `main.py` (file mới, không sửa `core/`), `broker/order_executor.py`
(chỉ thêm `restore_known_stop()`, không sửa method cũ nào), `config/settings.yaml`
(chỉ thêm section `execution` mới, không đổi bất kỳ giá trị `hmm`/
`trend_gate`/`strategy` nào golden test dùng — golden test tự khai báo
tham số riêng, không đọc `settings.yaml`) — không file nào trong ba file
đó chạm tới đường pipeline forward thật sự đi qua.

**Kết luận:** Thí nghiệm forward (`forward/log.csv`, xem mục "Forward test
— tiền đăng ký") **còn nguyên vẹn qua Phase 10** — không có gì kết thúc,
không cần regenerate golden baseline, không cần đóng thí nghiệm. Ghi lại
việc này (dù kết luận là "không đổi gì") vì đây là loại kiểm tra CLAUDE.md
tinh thần #16 muốn thấy trước khi tin — "golden test hiện đang PASS" một
mình không đủ bằng chứng nó BẮT ĐƯỢC đúng thay đổi Phase 10 tạo ra (có
thể pipeline forward tình cờ không đi qua đường Phase 10 sửa, may rủi
thay vì thiết kế) — kiểm tra hồi tố kiểu này xác nhận trực tiếp, không
suy luận gián tiếp từ "test đang xanh".

Dọn `git worktree remove /tmp/pre-p10` sau khi xong.
