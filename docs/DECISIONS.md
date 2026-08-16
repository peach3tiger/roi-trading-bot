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
tầng tài khoản BINANCE — `-2015`; xem mục sửa attribution 2026-08-14 ở
cuối file. `exchange_reachable` KHÔNG bị chặn, chỉ các endpoint cần key
mới bị):
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

---

## `tests/test_wiring_equivalence.py` — bảo vệ ba đường composition không hợp nhất được (2026-08-07)

Hệ quả trực tiếp của phát hiện ở mục trên: `SignalGenerator` là đường nối
dây thứ ba (bên cạnh `_run_golden_pipeline()` và `forward/logger.py`),
không được golden test lẫn forward test hằng ngày bảo vệ. Ba đường KHÔNG
được hợp nhất thành một hàm dùng chung — `forward/logger.py` là thí
nghiệm đóng băng 12 tháng, không được sửa dù chỉ để gọi qua
`SignalGenerator` — nên giải pháp là một test XÁC NHẬN TƯƠNG ĐƯƠNG
(equivalence), không phải refactor.

**Thiết kế đáng chú ý — vì sao không dùng chung một `HMMRegimeEngine` cho
cả ba đường:** `predict_regime_filtered()` có tác dụng phụ tích luỹ (bộ
lọc ổn định, cache alpha). Gọi nó hai lần cho "cùng một bar" (một lần
trực tiếp, một lần gián tiếp qua `SignalGenerator.generate()`) sẽ cộng
dồn bộ đếm ổn định hai lần, làm hỏng phép so sánh. Giải pháp: hai
`HMMRegimeEngine` độc lập, train giống hệt (xác định luận hoàn toàn — chỉ
`random_state=seed` cố định, không nguồn ngẫu nhiên nào khác trong
`scan_bic`), cho ăn cùng chuỗi bar theo cùng thứ tự — xác nhận đồng bộ
bằng `assert` tường minh mỗi bar (`regime_id`/`regime_label`/
`is_flickering`), không giả định suông rằng train giống hệt thì suy luận
cũng giống hệt.

`StrategyOrchestrator`/`StructuralTrendGate` xác nhận KHÔNG có tác dụng
phụ (đọc lại code, không chỉ tin docstring) — dùng chung một instance an
toàn cho cả ba đường.

`bars_window`: dùng `ohlcv.loc[:ts]` (không giới hạn, khớp quy ước
`_run_golden_pipeline()`) cho cả ba đường. Xác nhận bằng thực nghiệm
(không suy luận): với dải bar mục tiêu của test, `get_allocation_cap()`
với `ohlcv.loc[:ts]` và với `ohlcv.loc[:ts].tail(300)` (quy ước
`forward/logger.py`/`main.py`) cho CÙNG kết quả ở cả 60/60 bar — nên chọn
quy ước nào không ảnh hưởng tới việc test đo đúng thứ cần đo.

**SignalGenerator không lộ `hmm_allocation` (giá trị trước khi áp
trend_gate cap)** qua kết quả trả về — tính lại bằng CHÍNH `orchestrator`
(đã xác nhận thuần, gọi lại với cùng input luôn cho cùng kết quả
bit-for-bit).

`risk_manager` cấu hình PASS-THROUGH hoàn toàn (không cap, không circuit
breaker, không chặn trùng, không halt lock) — nó không tồn tại ở hai
đường kia, không được phép là biến số trong phép so sánh.

**Xác nhận bằng mutation (CLAUDE.md #16):** đổi `min()` thành `max()`
trong `SignalGenerator._apply_layer_caps()` — test đỏ NGAY bar đầu tiên
(`bar 150`), thông điệp lỗi tự chẩn đoán đúng vị trí (`hmm_allocation`
khớp, `trend_gate_cap` khớp, nên lệch nằm ở công thức kết hợp) — revert
sạch (`git diff --stat` rỗng).

Thêm vào CLAUDE.md #15 (6 file bắt buộc, tăng từ 5).

228 passed / 0 skipped. ruff + mypy sạch.

---

## Đo `bars_window`: `.tail(300)` (forward/logger.py) vs không giới hạn (golden) — ĐÓNG (2026-08-07)

Câu hỏi tồn đọng từ phiên trước: `forward/logger.py:558` truyền
`ohlcv.loc[:ts].tail(_STRATEGY_BARS_LOOKBACK)` (300 bar) vào
`generate_signal()`/`get_allocation_cap()`, còn `tests/test_forward_golden.py`/
`tests/test_wiring_equivalence.py` dùng `ohlcv.loc[:ts]` (không giới
hạn). Kết luận trước đó ("vô hại") chỉ dựa trên đọc code (EMA50/ATR14 hội
tụ nhanh trong vài chục bar) — **chưa đo**.

**Phương pháp** (`tests/test_bars_window_sensitivity.py`, KHÔNG sửa
`forward/logger.py`): tái tạo đúng công thức wiring của module đó (HMM →
`StrategyOrchestrator.generate_signal()` → `StructuralTrendGate.get_allocation_cap()`
→ `compose_layer_allocations()`) bằng component thật của `core/`, chạy
HAI LẦN ĐỘC LẬP trên CÙNG 300 bar dữ liệu tổng hợp (seed cố định) — một
lần `bars_window` cắt 300 bar, một lần không giới hạn — mỗi lần tự tích
luỹ `current_allocation` RIÊNG (không reset giữa hai lần chạy), để một
khác biệt nhỏ có cơ hội cộng dồn qua ngưỡng rebalance nếu nó thật sự tồn
tại. Dải bar kiểm tra xác nhận (bằng `assert`, không chọn số rồi hy vọng)
đi qua đúng ranh giới nơi `.tail(300)` bắt đầu cắt thật (ohlcv position
300).

**Kết quả: KHỚP 100%** — 0/300 bar lệch ở cả `hmm_allocation`,
`trend_gate_cap`, `final_allocation`, kể cả sau khi hai chuỗi
`current_allocation` tích luỹ độc lập suốt 300 bar (đối chứng cuối: hai
giá trị cuối cùng bằng nhau tuyệt đối, không chỉ khớp bar-by-bar).

**Xác nhận test không vô nghĩa (mutation, CLAUDE.md #16):** thu nhỏ
`_TAIL_LOOKBACK` trong test xuống 235 (chỉ 5 bar trên ngưỡng warmup tối
thiểu 230 của trend gate) — LỘ RA lệch thật ngay bar 230
(`trend_gate_cap`: `0.60` cắt-235 vs `0.30` không giới hạn) — chứng minh
phép đo THẬT SỰ nhạy với khác biệt cửa sổ khi nó tồn tại, không phải luôn
xanh do lỗi thiết kế test. Ở giá trị PRODUCTION THẬT (300, dư 70 bar so
với ngưỡng 230), khác biệt không xuất hiện.

Kết quả ghi vào docstring `forward/logger.py` (mục "ĐO `bars_window`",
chỉ thêm text, không đổi logic dòng nào) — câu hỏi đóng. Không cần đo lại
trừ khi `_STRATEGY_BARS_LOOKBACK` (forward/logger.py) hoặc cấu hình
`TrendGateConfig`/`sma_period`+`slope_lookback` đổi.

229 passed / 0 skipped. ruff + mypy sạch.

---

## Khoá giả định "`StrategyOrchestrator.generate_signal()` thuần" bằng assertion runtime (2026-08-07)

`tests/test_wiring_equivalence.py`/`tests/test_bars_window_sensitivity.py`
đều gọi `orchestrator.generate_signal()` NHIỀU LẦN với cùng input, dựa
trên giả định "không tác dụng phụ trên `self`" — trước đó chỉ xác nhận
bằng ĐỌC LẠI code mỗi lần. Rẻ hơn nhiều để khoá giả định đó bằng một
assertion runtime thường trực, thay vì đọc lại code mỗi lần nghi ngờ.

`tests/test_strategies.py::test_generate_signal_is_idempotent_no_hidden_state`:
gọi `generate_signal()` BA LẦN trên CÙNG một instance orchestrator với
input giống hệt, khẳng định cả ba `Signal` trả về bằng nhau tuyệt đối
(dataclass frozen — so toàn bộ field cùng lúc, không chỉ
`target_allocation_pct`). Gọi trên CÙNG instance (không phải instance mới
mỗi lần) — instance mới sẽ luôn khớp bất kể có state ẩn hay không, không
kiểm tra được gì.

**Xác nhận bằng mutation (CLAUDE.md #16):** thêm `self._call_count`
(tăng dần mỗi lần gọi) vào `StrategyOrchestrator.__init__`/`generate_signal()`,
rò rỉ giá trị đó vào `reasoning` của signal trả về — test đỏ NGAY ở lần
gọi thứ hai (`reasoning` lệch `[MUTATION call=1]` vs `[MUTATION call=2]`),
đúng cơ chế test được thiết kế để bắt. Revert sạch (`git diff --stat`
rỗng).

230 passed / 0 skipped. ruff + mypy sạch 54 file.

---

## `tests/test_frozen_files.py` — ghim SHA256 hai file thí nghiệm đóng băng (2026-08-07)

`forward/logger.py` được TUYÊN BỐ đóng băng ("KHÔNG BAO GIỜ được sửa",
docstring module) nhưng trước bản này KHÔNG có gì tự động kiểm tra điều
đó — chỉ có kỷ luật đọc kỹ trước khi commit. `forward/config_frozen.yaml`
đã có hash-kiểm RIÊNG nhưng chỉ ở TẦNG RUNTIME (`forward/config_frozen.sha256`,
kiểm tra bên trong `load_frozen_settings()`, chỉ chạy khi `run_forward_test()`
thật sự được gọi qua launchd) — một thay đổi tới file đó vẫn có thể lọt
qua `pytest`/CI thường nếu không ai chạy forward test trong lúc đó.

**Thêm `tests/test_frozen_files.py`**: ghim SHA256 của cả hai file vào
`tests/golden/frozen_hashes.json`, kiểm tra ở TẦNG TEST SUITE — chạy mỗi
lần `pytest`, không phụ thuộc forward test có chạy hay không. Thông báo
lỗi khi FAIL nêu rõ: đây là thí nghiệm 12 tháng bắt đầu 2026-08-06, sửa
file (dù vô tình hay cố ý) nghĩa là thí nghiệm hiện tại kết thúc tại đúng
thời điểm đó, phải ghi `docs/DECISIONS.md` TRƯỚC khi cập nhật hash cho
thí nghiệm mới — không được sửa hash để test xanh lại.

**Hash ghim phản ánh trạng thái `forward/logger.py` SAU khi thêm đoạn
docstring "ĐO `bars_window`"** (mục ngay trên đây) — lần sửa DUY NHẤT
từng được yêu cầu tường minh, xảy ra TRƯỚC khi `tests/test_frozen_files.py`
tồn tại. Từ lúc ghim hash này, không còn ngoại lệ nào nữa cho
`forward/logger.py`, kể cả một dòng comment.

**Xác nhận bằng mutation (CLAUDE.md #16), làm cẩn thận vì đây là file
nhạy cảm nhất repo:** backup cả hai file ra `/tmp` trước, xác nhận backup
byte-for-byte giống bản gốc (so hash). Append một dòng comment vào
`forward/logger.py` — test đỏ đúng thông điệp thiết kế. Khôi phục từ
backup, xác nhận hash khớp lại + `git diff --stat` rỗng. Lặp lại y hệt
cho `forward/config_frozen.yaml` (mutate riêng, đỏ riêng, khôi phục riêng,
xác nhận riêng) — không mutate cả hai cùng lúc, để biết chắc test bắt
được TỪNG file một, không chỉ tổng thể.

Thêm vào CLAUDE.md #15 (7 file bắt buộc, tăng từ 6).

231 passed / 0 skipped. ruff + mypy sạch.

---

## 2026-08-07 — Phase 11: Monitoring (`monitoring/logger.py`/`dashboard.py`/`alerts.py`)

Xây theo `prompts/phase-11-monitoring.md` + `docs/Brain-Crypto-Bybit.md` §8.
Ba file scaffold tồn tại từ trước (dataclass/enum đầy đủ, method
`raise NotImplementedError`) — implement thật lần này.

**`monitoring/logger.py`**: JSONL thật (mỗi dòng một object JSON hợp lệ),
`RotatingFileHandler` 10MB/30 backup (proxy dung lượng, KHÔNG phải lịch 30
ngày — ghi rõ trong docstring để không ai đọc nhầm là `TimedRotatingFileHandler`).
Bug thật bắt được qua chính test (không phải mutation cố ý):
`logging.getLogger(name)` tra registry TOÀN CỤC theo tên — `get_logger("main", dirA)`
rồi `get_logger("main", dirB)` (khác `log_dir`, cùng `name`) cộng dồn
handler trên CÙNG object logger. Sửa bằng dựng `logging.Logger(...)` trực
tiếp thay vì qua `logging.getLogger()`.

**`monitoring/alerts.py`**: `AlertManager` — rate limit 1/loại sự
kiện/15 phút áp dụng CHUNG cho mọi kênh của một alert (không phải riêng
từng kênh). Kênh console dùng `logging.StreamHandler` riêng, KHÔNG
`print()` — nghiệm thu của phase-11-monitoring.md chạy
`grep -rn "print(" monitoring/` và kỳ vọng không thấy vi phạm thật.
`send()` cam kết không bao giờ raise; ban đầu chỉ bắt
`requests.RequestException`/`(SMTPException, OSError)` — test tự đỏ khi
mock ném `OSError` cho kênh Telegram/webhook (loại không nằm trong
`requests.RequestException`), sửa bằng bắt `Exception` rộng ở cả ba kênh
mạng, đúng tinh thần cam kết "không bao giờ raise" là tuyệt đối, không
phải "trừ khi thư viện ném loại tôi chưa liệt kê". Thêm
`AlertType.TREND_GATE_CHANGE` — scaffold gốc thiếu dù
phase-11-monitoring.md liệt kê "đổi trạng thái trend gate" là trigger
riêng.

**`monitoring/dashboard.py`**: `Dashboard` (rich) — 6 panel đúng §8.2.
Scaffold gốc thiếu field cho hai panel VỊ THẾ/SIGNAL GẦN ĐÂY — bổ sung vào
`DashboardState` (`position_direction`/`position_entry_price`/.../
`recent_signals: tuple[RecentSignal, ...]`). "Phí tháng này" luôn hiển thị
kể cả bằng 0 (nghiệm thu riêng). `render_text()` (Console record=True)
cho test không cần TTY thật và cho "chụp màn hình dạng text".

**Wire vào `main.py`** (không chỉ viết ba module rồi để đó chưa gọi):
- `LiveLoopState` +2 field, cả hai có default (backward-compat với
  snapshot cũ, xác nhận bằng test riêng):
  - `cumulative_fees_paid` — phí THẬT đọc từ `OrderResult.raw_response["fee"]`/
    `["fees"]` (cấu trúc ccxt chuẩn), KHÔNG ước lượng bằng
    `costs.taker_fee_pct` (đó là số cho backtest). Bug thật bắt được qua
    test: `log_state()` ban đầu gọi TRƯỚC khi cộng phí bar hiện tại — log
    trễ một bar. Sửa bằng dời lệnh gọi xuống ngay trước `return`, sau khi
    `cumulative_fees` đã cập nhật xong cho cả hai nhánh (approved/rejected).
  - `current_trend_structure` — để `_fire_bar_alerts()` phát hiện đổi
    trend-gate-state so với bar trước, cùng kỹ thuật `current_regime_id`
    đã dùng từ Phase 10 (so giá trị CŨ trong `state` với giá trị MỚI vừa
    tính trong CHÍNH bar này, không cần biến rời sống ngoài hàm).
- `process_one_bar()`: +3 tham số optional (`alert_manager`,
  `regime_state_logger`, `large_pnl_alert_pct`), mặc định giữ nguyên hành
  vi 23 test Phase 10 đã có (không truyền = không đổi gì).
- `run_live_loop()`: build cả hai, truyền vào mỗi bar; thêm
  `AlertType.HMM_RETRAINED` sau retrain, `AlertType.API_LOST` ở catch-all
  vòng ngoài.

**Cố ý CHƯA wire** (không fabricate để "cho đủ"): `AlertType.STABLECOIN_DEPEG`
liên tục (thiếu nguồn giá USDT/USD đáng tin đã kiểm chứng),
`AlertType.CLOCK_DRIFT` liên tục mỗi bar (cần `ExchangeClient.get_server_time()`
chưa tồn tại trong ABC), `LARGE_PNL` chiều LÃI (chỉ đọc được
`CircuitBreaker.check().daily_dd`, vốn chỉ đo drawdown — chưa có equity
history bar-over-bar để phát hiện P&L dương lớn), `main.py --dashboard`
CLI (Dashboard class xong/test đầy đủ, nhưng `state_snapshot.json` hiện
không lưu đủ field để dựng `DashboardState` sống mà không bịa số — đặc
biệt `ws_connected`/`ws_last_message_seconds_ago`/`api_latency_ms` vốn
cho kiến trúc WebSocket, hệ thống này đã đổi sang REST polling từ trước,
xem mục "Đổi sàn Bybit -> Binance"). Ghi đầy đủ vào `docs/STATE.md` mục
Phase 11 thay vì âm thầm bỏ qua.

**Không xác nhận được** (cần mạng thật): gửi Telegram thật (chưa có token
thật để thử — độc lập với việc testnet bị chặn, có thể làm khi có token),
dashboard chạy với dữ liệu testnet thật (phụ thuộc cả hai gap ở trên).

Test mới: `tests/test_monitoring_logger.py` (8), `tests/test_monitoring_alerts.py`
(18), `tests/test_monitoring_dashboard.py` (10), mở rộng
`tests/test_main_loop.py` (+12). 288 passed / 0 skipped. ruff + mypy sạch.

---

## 2026-08-07 (sau) — Sửa schema DashboardState: bỏ tàn dư WebSocket

Phát hiện: `monitoring/dashboard.py::DashboardState` còn `ws_connected: bool`/
`ws_last_message_seconds_ago: float` — mô tả kiến trúc WebSocket đã bị bỏ
từ đợt đổi sàn Bybit -> Binance (xem mục "Bỏ WebSocket, chuyển hẳn sang
REST polling" ở trên). Hai field đó không có nghĩa thật để điền trong kiến
trúc REST polling: không có kết nối bền để "connected"/"disconnected",
không có message đẩy tới để đo "bao lâu kể từ tin nhắn cuối".

**Sửa:**
- Bỏ `ws_connected`/`ws_last_message_seconds_ago`.
- Thêm `poll_latency_ms` (round-trip của lần fetch OHLCV gần nhất qua
  `history_loader.load()`) + `last_poll_at` (ISO UTC, lúc lần fetch đó xảy
  ra) — CẢ HAI persist trong `LiveLoopState`/`state_snapshot.json`, cập
  nhật ở `run_live_loop()` đúng chỗ gọi `history_loader.load()` (không
  phải mỗi lần lặp vòng poll — phần lớn chu kỳ 60s không có bar mới nên
  không gọi mạng, xem docstring `run_live_loop`). Cả hai có default `None`
  — backward-compat với snapshot cũ, xác nhận bằng test riêng.
- Thêm `bars_behind` (bar đã đóng - bar đã xử lý gần nhất). KHÔNG persist
  — `main.py::compute_bars_behind()` là hàm THUẦN, tính lại từ
  `last_processed_bar` (đã có sẵn trong snapshot) + đồng hồ hiện tại mỗi
  lần gọi. Quyết định có chủ đích: một giá trị `bars_behind` lưu sẵn từ
  lần ghi snapshot cuối sẽ đứng yên ở "0" ngay cả khi tiến trình chính đã
  chết từ lâu — đúng lúc field này tồn tại để báo động nhất, một con số
  đông cứng sẽ nói dối đúng lúc quan trọng nhất.
- `monitoring/dashboard.py::_system_panel` vẽ lại panel HỆ THỐNG với ba
  field mới; màu `bars_behind` (xanh=0, vàng=1, đỏ>=2).

**Xác nhận bằng mutation (CLAUDE.md #16):** đổi `_bool_icon(bars_behind_ok)`
thành `_bool_icon(True)` cố định trong `_system_panel` — test
`test_bars_behind_nonzero_shows_warning_icon` đỏ đúng, khôi phục sạch
(`git diff --stat` rỗng). Tương tự cho `compute_bars_behind()`: hàm giả
luôn trả 0 làm test staleness đỏ.

**Grep toàn repo tìm tàn dư WebSocket khác** (`ws_`, `websocket`,
`heartbeat`, `reconnect`, `subscribe_`) — báo cáo cho người dùng, KHÔNG tự
sửa (yêu cầu tường minh):
- `tests/test_orders.py::_FakeExchange` còn định nghĩa `subscribe_klines`/
  `subscribe_executions` dù `ExchangeClient` ABC đã bỏ hai method này —
  xác nhận KHÔNG được gọi ở đâu (`grep ".subscribe_klines(\|.subscribe_executions("`
  rỗng). Đúng như `ops/RUNBOOK.md` đã tự cảnh báo trước: "nếu bạn thấy
  code tham chiếu chúng, đó là tàn dư cần dọn, không phải tính năng còn
  sống".
- `broker/bybit_client.py` (đã đánh dấu deprecated, giữ làm bằng chứng —
  quyết định trước đó, không xoá) vẫn có implementation đầy đủ của
  `subscribe_klines()`/`subscribe_executions()` qua `pybit.unified_trading.WebSocket`
  — dead code bên trong một file đã cố ý giữ lại nguyên trạng.
- `README.md` dòng mô tả `ops/RUNBOOK.md` còn ghi "mất WebSocket" — nội
  dung RUNBOOK thật đã đổi thành "Mất dữ liệu giá (REST polling thất
  bại)", dòng mô tả một-câu trong README chưa cập nhật theo.
- `docs/Brain-Crypto-Bybit.md`, `prompts/phase-09-bybit-broker.md`,
  `prompts/phase-10-main-loop.md`, `prompts/phase-12b-harness-engineering.md`
  (field ví dụ `"ws_latency_ms": 45` trong JSON mẫu của `monitoring/health.py`
  đề xuất) — spec/prompt gốc viết TRƯỚC quyết định bỏ WebSocket, còn mô tả
  luồng WebSocket (heartbeat ping/pong, "Mở WebSocket feed", checklist "Mô
  phỏng mất WebSocket"). Là tài liệu spec/kế hoạch, không phải code sống —
  không tự sửa, để người dùng quyết định (ghi chú "đã lỗi thời" hay viết
  lại).
- Hai worktree cũ `.claude/worktrees/cranky-easley-107d17`/`kind-clarke-98bb91`
  (từ agent chạy `isolation: "worktree"` phiên trước, chưa được dọn tự
  động) đứng ở commit cũ (`479495d`/`155259a`, trước đợt đổi sàn) — chứa
  bản sao stale của `broker/base.py`/`data/market_data.py`/... với đầy đủ
  WebSocket. Không phải một phần cây làm việc chính, chỉ ghi nhận sự tồn
  tại — có thể dọn bằng `git worktree remove` nếu không còn cần.

Test mới/sửa: `tests/test_monitoring_dashboard.py` (+5),
`tests/test_main_loop.py` (+7). 300 passed / 0 skipped. ruff + mypy sạch.

---

## 2026-08-07 (sau nữa) — Lệch đồng hồ: get_server_time() + monitoring/clock.py

Hoàn thiện `AlertType.CLOCK_DRIFT` — trước đó chỉ có giá trị enum, chưa
đo/wire gì (ghi nhận là khoảng trống ở mục Phase 11 phía trên).

**1. `ExchangeClient.get_server_time() -> int` (`broker/base.py`)** —
epoch milliseconds. **KHÔNG `@abstractmethod`** như 8 method còn lại của
interface, có chủ đích: `broker/bybit_client.py` đánh dấu deprecated với
lời cam kết "giữ nguyên, không sửa logic, không xoá" (docstring module) và
ĐƯỢC instantiate trực tiếp trong `tests/test_bybit_client.py` — một
abstractmethod mới sẽ làm `TypeError` ngay lúc khởi tạo, buộc phải sửa
file đã cố tình đóng băng chỉ để thoả một tính năng nó không cần (không
còn dùng để giao dịch thật). Method mặc định `raise NotImplementedError`
— lộ RÕ RÀNG nếu bị gọi trên một subclass không override, không phải một
phép tính im lặng dùng số sai. `CCXTClient.get_server_time()` override
bằng `exchange.fetch_time()` thật, qua `_call_with_retry()` như mọi lời
gọi mạng khác trong client.

**2. `monitoring/clock.py::measure_clock_drift()`** — hiệu chỉnh
round-trip kiểu NTP:

    t0 = local_ms(); server = get_server_time(); t1 = local_ms()
    round_trip = t1 - t0
    local_at_server_response = t0 + round_trip / 2
    drift_ms = server - local_at_server_response

KHÔNG dùng công thức ngây thơ `server - now()` — nó cộng gộp gần như toàn
bộ độ trễ MỘT CHIỀU vào kết quả (server đọc giờ ở GIỮA round-trip, không
phải lúc `t1`). Xác nhận bằng test riêng (`test_naive_formula_would_disagree_by_round_trip_confirming_test_is_meaningful`):
cùng dữ liệu giả lập, công thức ngây thơ cho hai round-trip (50ms/400ms)
lệch nhau ~175ms, công thức đúng lệch nhau <2ms.

Trung vị (median) của 3 lần đo liên tiếp — chọn NGUYÊN CẶP `(drift_ms,
round_trip_ms)` của lần đo có drift ở giữa, KHÔNG lấy trung vị độc lập
từng trường (sẽ tạo ra một cặp số không tương ứng với bất kỳ lần đo thật
nào, vô nghĩa để audit lại).

**Xác nhận `ops/health_check.py::check_exchange_reachable` DÙNG CÔNG THỨC
NGÂY THƠ** (`abs(local_ms - server_time_ms)`, đã có từ Phase 9/10) — GIỮ
NGUYÊN, không sửa: nó phục vụ mục đích khác (WARN sớm, không cần
credential, dùng một ccxt instance trần dựng riêng, không qua
`ExchangeClient`). `monitoring/clock.py` là kênh đo CHÍNH XÁC, có thẩm
quyền quyết định thật ở ngưỡng dừng lệnh — `check_exchange_reachable` chỉ
còn là heads-up sớm hơn, ít chính xác hơn.

**3-4. Ngưỡng và wire:**
- `config/settings.yaml: monitoring.clock_drift_alert_ms=1000`,
  `clock_drift_halt_ms=2500` (Binance `recvWindow` mặc định 5000ms — quá
  nửa cửa sổ đó, request ký bị từ chối `-1021`, trông hệt key sai).
- Khởi động (`run_live_loop`, ngay sau `build_exchange_client()`): đo một
  lần, **FAIL cứng** (`sys.exit(1)`, in `drift_ms`/`round_trip_ms` thật)
  nếu vượt `clock_drift_halt_ms`.
- Mỗi bar (`process_one_bar`, qua `_check_clock_drift()`, gọi khi
  `alert_manager` được truyền — cùng pattern optional-param với
  `_check_spread_and_alert`/`_fire_bar_alerts`, không đổi hành vi test cũ
  không truyền `alert_manager`): log MỌI bar (kể cả không vượt ngưỡng nào)
  vào `regime.log`; `AlertType.CLOCK_DRIFT` ở >1000ms; **DỪNG gửi lệnh
  mới** ở >2500ms — return SỚM, TRƯỚC CẢ bước kiểm tra stop-loss breach,
  giữ nguyên `current_stop_loss`/`current_allocation_pct`/`current_regime_id`
  y hệt input. Đánh đổi CÓ CHỦ Ý, ghi rõ trong docstring: một breach
  stop-loss THẬT xảy ra đúng bar bị halt sẽ KHÔNG được enforce (đóng vị
  thế) cho tới bar kế tiếp đồng hồ đã đồng bộ — vì `close_position()` lúc
  đó cũng là một request ký, sẽ thất bại với cùng lỗi `-1021`, thử vẫn
  chắc chắn không thành công.
- `LiveLoopState` +3 field (`last_clock_drift_ms`/`last_clock_round_trip_ms`/
  `last_clock_check_at`, default `None`, backward-compat) — feed
  `DashboardState.clock_drift_ms` (field đã có sẵn từ trước) khi
  `--dashboard` được wire.

**5. TUYỆT ĐỐI KHÔNG bật ccxt `adjustForTimeDifference`** — không có ở
đâu trong `broker/ccxt_client.py`, lý do đầy đủ ghi trong docstring
`monitoring/clock.py`: cờ đó giấu triệu chứng (request đi lọt) mà không
sửa nguyên nhân (đồng hồ hệ thống vẫn sai), và một hệ thống KHÁC đọc cùng
máy đó (log timestamp, cron job khác) vẫn tin vào giờ sai.

**6. Test — mutation-verified (CLAUDE.md #16) cả hai lớp:**
- `tests/test_monitoring_clock.py` (7 test: offset cố định +1500/-3000/0
  với round-trip=0; hiệu chỉnh round-trip 50ms vs 400ms cho kết quả gần
  nhau <2ms; median giữ đúng cặp từ một lần đo). Mutation: bỏ
  `round_trip/2`, dùng `t1` trực tiếp — 2 test (hiệu chỉnh, median) đỏ
  đúng vị trí, revert sạch.
- `tests/test_main_loop.py` (+9: dưới ngưỡng alert không cảnh báo; giữa
  hai ngưỡng cảnh báo nhưng không halt; vượt ngưỡng halt — không
  submit_order/close_position/modify_stop nào được gọi, state giữ
  nguyên; halt vẫn cập nhật telemetry + last_processed_bar; drift ÂM cũng
  halt (so trên `abs()`); log mỗi bar kể cả không cảnh báo; đo lỗi mạng
  không halt). Mutation: ép `halted = False` cố định trong `_check_clock_drift`
  — 2 test (halt trên ngưỡng dương, halt trên ngưỡng âm) đỏ đúng, revert
  sạch (`git diff --stat` rỗng).
- `tests/test_ccxt_client.py` (+2: trả đúng int epoch ms; đi qua
  `_call_with_retry` khi gặp `NetworkError`).
- `tests/test_bybit_client.py` (+1: gọi `get_server_time()` không
  override raise `NotImplementedError`).

**7. `ops/RUNBOOK.md`** — mục mới "CLOCK_DRIFT — đồng hồ máy lệch so với
sàn", đặt cạnh mục "Xác thực sàn thất bại" (triệu chứng bề ngoài giống
hệt nhau, dễ chẩn đoán nhầm): nguyên nhân thường gặp (NTP tắt, máy ngủ
dậy, CMOS trôi), cách sửa macOS (System Settings → General → Date & Time
→ Set automatically, hoặc `sudo sntp -sS time.apple.com` để ép đồng bộ
ngay) và Linux/container (`timedatectl status`, container dùng đồng hồ
host qua kernel — sửa ở container không có tác dụng nếu host sai).

317 passed / 0 skipped. ruff + mypy sạch.

---

## 2026-08-08 — Forward test dừng im lặng 08-06 → 08-08; cuộn schema log sang v2

### Nguyên nhân ban đầu bị chẩn đoán sai

Giả định lúc phát hiện là "launchd chưa từng được nạp thành công". **Sai.**
`launchctl print` cho thấy job ĐÃ nạp, ĐÃ chạy (`runs = 1`), và
`last exit code = 1`. Nó chạy đủ đều — chỉ là lần nào cũng chết.

`forward/launchd.err.log` chỉ thẳng:

```
pandas.errors.ParserError: Error tokenizing data.
C error: Expected 31 fields in line 3, saw 32
```

### Nguyên nhân thật — `warning_count` thêm vào file đóng băng, KHÔNG có quyết định tường minh

1. `8dde130` dựng forward test. `log.csv` bắt đầu với **31 cột**. Bar
   2026-08-05 ghi lúc 08-06T05:49 theo schema đó.
2. `506bfde` (08-06T13:10, **7 giờ sau** dòng đầu tiên) thêm
   `warning_count` vào `_CSV_FIELDNAMES` của `forward/logger.py` — file đã
   tuyên bố đóng băng. Schema thành **32 cột**.
3. `append_row()` chỉ ghi header khi file **chưa tồn tại**
   (`is_new = not target.exists()`). Đúng cho append-only, nhưng hệ quả là
   một file đã bắt đầu **không bao giờ học được cột mới**.
4. Backfill 08-08T04:20 append hai dòng **32 cột** vào file header 31 cột.
5. `read_existing_log()` chết ở `pd.read_csv` **mỗi lần chạy** từ đó.

Mục "Bổ sung hạ tầng (2026-08-07)" phía trên có nhắc việc thêm cột, kết
luận "không đụng cấu hình đóng băng". Kết luận đó đúng về mặt **khoa học**
(`config_frozen.yaml`/`FEATURE_SUBSET` không đổi, thí nghiệm không mở
lại), nhưng **không có entry nào ghi rằng `forward/logger.py` — file đóng
băng — đang bị sửa, cũng không có ai quyết định điều đó tường minh.** Thay
đổi đi kèm hai việc khác trong cùng một commit và trôi qua.

**Bài học, áp dụng từ nay:** mọi thay đổi vào `forward/` — **kể cả chỉ
thêm một cột log** — phải có entry `docs/DECISIONS.md` **TẠI THỜI ĐIỂM
thay đổi**, không phải gộp vào bản tổng kết sau. Quy trình cuộn schema
viết thành 5 bước trong `forward/SCHEMA.md`.

### Xử lý: CUỘN FILE, không sửa file cũ

Phương án đầu tiên tôi làm là migrate `log.csv` tại chỗ (viết lại header
32 cột, chèn `warning_count` rỗng cho dòng 08-05). **Đã hoàn tác.** Sửa
header của một file bằng chứng sau khi đã thấy kết quả thì kể cả khi đúng
về kỹ thuật, nó phá đúng tính chất khiến log này đáng tin: mỗi dòng được
ghi một lần, tại thời điểm đó, và không bao giờ đổi.

| Phiên bản | File | Bar | Cột |
|---|---|---|---|
| v1 | `forward/log.csv` | 2026-08-05 (1 bar) | 31 |
| v2 | `forward/log_v2.csv` | từ 2026-08-06 | 32 (+`warning_count`) |

`log.csv` giữ **nguyên trạng byte-for-byte** (`git checkout`), giờ đã
đóng. Hai dòng 32 cột tách sang `log_v2.csv` với header đúng, lấy từ bản
sao lưu trạng thái hỏng — đi thẳng từ nguyên bản do logger ghi ra, không
qua phép biến đổi nào.

**Không mất dữ liệu.** Cả 3 bar còn đủ, `load_all_bars()` nối lại liền
mạch, alignment kiểm từng cột (`regime_id=6`/`STRONG_BEAR`,
`hmm_allocation=0.95`, `trend_gate_cap=0.3`, `final_allocation=0.3` nhất
quán cả ba).

### `forward/runner.py` — cuộn file mà KHÔNG sửa file đóng băng

Chỉ dẫn ban đầu là "logger ghi vào log_v2.csv + cập nhật
`frozen_hashes.json`", tức chấp nhận sửa `forward/logger.py` và đổi hash
ghim. **Không làm vậy**, vì theo chính bất biến đang có, đổi hash CHỈ hợp
lệ khi CỐ Ý kết thúc thí nghiệm — mà cuộn file log không phải kết thúc
thí nghiệm: cấu hình đóng băng không đổi, chuỗi bar không đứt,
`hmm_retrained` đọc tiếp được qua cả hai file. Vô hiệu hoá một bất biến để
thoả một thay đổi không cần tới nó chính là lỗ hổng đã gây ra sự cố này
ngay từ đầu.

`append_row()`/`read_existing_log()` tra `_LOG_PATH` ở **thời điểm gọi**,
không phải lúc định nghĩa hàm — thiết kế có chủ đích, docstring
`append_row` nói rõ là để monkeypatch được. `forward/runner.py` gán lại
biến module rồi gọi `run_forward_test()`: **`forward/logger.py` không đổi
một byte**, SHA256 vẫn `20a9474d…`, `tests/test_frozen_files.py` giữ
nguyên hiệu lực.

LaunchAgent đổi sang `python -m forward.runner`.

`frozen_hashes.json` VẪN được cập nhật, nhưng theo nghĩa khác: **ghim
thêm `forward/log.csv`** (v1 đã đóng). Lý do ghim khác hai file kia —
chúng là code/cấu hình không được sửa TRONG thí nghiệm, còn đây là bằng
chứng đã ghi, nên hash này không đổi kể cả khi bắt đầu thí nghiệm MỚI.

### `forward/SCHEMA.md` + `load_all_bars()`

Code phân tích mốc 3/6/12 tháng phải đọc **cả hai** file. Để yêu cầu đó
thực thi được chứ không chỉ là ghi chú, `forward/runner.py::load_all_bars()`
nối tất cả, sắp theo `date`, thêm cột `source_log`, trả về đúng
`_CSV_FIELDNAMES` mới nhất.

`warning_count` của v1 là **`NaN`, không phải `0`** — bản chạy v1 không có
cơ chế đếm warning, nên `0` là khẳng định sai ("đã đo, không có") thay vì
ô trống trung thực ("không biết"). Có test riêng đỏ nếu ai đó thêm
`fillna(0)`.

### Test append-only áp cho CẢ HAI file

`tests/test_forward_log_append_only.py` (13 test) kiểm **file thật trên
đĩa**, không phải hàm trên `tmp_path` như `tests/test_forward_logger.py`.
Khoảng trống nó lấp: sự cố này không phải lỗi của `append_row()` — hàm đó
chạy đúng đặc tả suốt. Lỗi là file rơi vào trạng thái header một schema /
dòng một schema khác, và **không test nào nhìn vào file thật để thấy**.

Kiểm: mọi dòng cùng số cột với header; header khớp đúng schema của file;
ngày tăng dần không trùng; không bar nào xuất hiện ở cả hai file; file đã
đóng đều phải được ghim hash.

Đột biến (kỷ luật #16) — 4/4 bị bắt: append dòng 32 cột vào v1
(`test_moi_dong_cung_so_cot_voi_header` + `test_frozen_files` cùng đỏ);
nhân đôi bar cuối v2; `fillna(0)` trong `load_all_bars`; runner trỏ ngược
về v1.

### Canh gác độ tươi — `monitoring/forward_watchdog.py`

LaunchAgent **riêng** (09:00, một giờ sau job forward test). Không gộp:
watchdog chạy chung tiến trình với thứ nó canh sẽ chết cùng thứ đó.

Canh file `ACTIVE_LOG_PATH` hỏi từ `forward.runner`, không hardcode — cuộn
schema mà watchdog vẫn canh file cũ thì file đó không bao giờ tăng dòng
nữa, nên nó kêu mỗi ngày, bị coi là báo động giả, rồi bị tắt đúng lúc mất
khả năng canh thật.

Tín hiệu quyết định là `max(date)`, **không phải** mtime (`git checkout`
làm mới mtime mà không thêm bar nào) và không phải số dòng (cần state
file — thêm một thứ nữa hỏng im lặng được, đúng chế độ hỏng đang bắt). Cả
hai vẫn được đo và đưa vào thông điệp làm dữ liệu chẩn đoán.

Ngưỡng `> 2` ngày: bar D ghi vào D+1 nên staleness=1 là bình thường, =2 là
lỡ đúng một lần (máy ngủ qua 08:00), >2 là lỡ từ hai lần trở lên. Kêu sớm
hơn sẽ báo động giả mỗi lần máy ngủ, và watchdog kêu oan đều đặn sẽ bị ngó
lơ đúng hôm nó kêu thật.

#### Đột biến tìm ra bug thật trong chính watchdog

Bản đầu chỉ hỏi "`read_existing_log()` có ném lỗi không". Đột biến tái
hiện lệch schema cho **`stale=False`** — watchdog báo KHOẺ trên đúng kiểu
hỏng nó sinh ra để bắt.

Lý do: khi **mọi** dòng dư đúng một trường so với header, pandas không ném
gì — nó lặng lẽ lấy cột đầu làm index. `df` trông lành lặn (31 cột, không
NaN bất thường), `date` chứa giá trị `run_at_utc`, `staleness_days` vẫn ra
1. Sự cố thật ném `ParserError` chỉ vì độ rộng **không đồng nhất** (dòng
đầu 31, dòng sau 32). Nếu nó đồng nhất, thí nghiệm đã âm thầm ghi số lệch
cột suốt 12 tháng mà không ai biết — tệ hơn hẳn việc dừng hẳn.

Sửa: `inspect_log()` ghim `list(df.columns)` vào `_CSV_FIELDNAMES` và kiểm
`isinstance(df.index, pd.RangeIndex)`. Mọi tín hiệu khác đều trông lành
lặn trong ca này.

### Kiểm chứng launchd (không chờ tới sáng mai)

Plist dùng đường dẫn **tuyệt đối** tới `.venv/bin/python` — thiết yếu, vì
`PATH` mặc định của launchd là `/usr/bin:/bin:/usr/sbin:/sbin`, không chứa
venv. `WorkingDirectory` tuyệt đối. `StandardOutPath`/`StandardErrorPath`
đều có — thiếu thì lần lỗi sau không để lại dấu vết nào, đúng cách sự cố
này ẩn được 3 ngày.

`bootout` + `bootstrap` (launchd không tự đọc lại plist đã đổi) rồi
`kickstart -p`, **đọc stderr**: `runs = 2`, `last exit code = 0`,
`{"appended": 0, "last_logged_date": "2026-08-07"}`. Watchdog cũng
`runs = 2`, `exit 0`, canh đúng `log_v2.csv`.

### Điểm mù còn lại — CHƯA đóng

`.env` có `TELEGRAM_BOT_TOKEN=` và `TELEGRAM_CHAT_ID=` **giá trị rỗng**
(độ dài 0). `AlertManager` quy đổi chuỗi rỗng thành `None` → kênh Telegram
không gửi gì. Watchdog phát hiện đúng nhưng chỉ ghi ra
`forward/watchdog.err.log` — file không ai đọc, tức là vẫn chưa thoát khỏi
"phụ thuộc vào việc con người nhớ kiểm tra".

Giảm thiểu tạm: `telegram_configured` được tính và ghi vào
`watchdog.out.log` **mỗi lần chạy**, kể cả khi log khoẻ, kèm
`logger.warning` rõ ràng. Kiểm kênh chỉ lúc cần gửi thì phát hiện "chưa
cấu hình" đúng hôm cần nó nhất.

`load_dotenv()` nạp `.env` trong tiến trình (launchd không có env của
shell). **Không** đặt credential vào plist: plist được commit, `.env` thì
không (bất biến #6).

**Chưa đóng hẳn cho tới khi điền credential thật vào `.env`.**

348 passed / 0 skipped. ruff + mypy sạch.

---

## 2026-08-08 (sau) — Sửa 9 bug tầng thực thi

Không thêm tính năng, không refactor kiến trúc, không đổi logic giao dịch.
Mỗi bug có một test tái hiện trong `tests/test_nine_bug_fixes.py`, tất cả
đã kiểm chứng bằng đột biến (kỷ luật #16) — 10/10 đột biến bị bắt.

**1. `run_live_loop()` bỏ qua bar bị lỡ.** Lặp qua mọi bar chưa xử lý
bằng `main._pending_bar_dates()`.

**Lệch khỏi chỉ dẫn:** yêu cầu ghi "dùng `pending_bar_dates()` (đã có
trong `forward/logger.py`)". Bản đầu tôi import thẳng từ đó, rồi **hoàn
tác** — `docs/STATE.md` (mục Phase 10) đã ghi một quyết định thiết kế
ngược lại: `main.py` CỐ TÌNH không import từ `forward/` dù chỉ một hàm
thuần, vì forward test là thí nghiệm tiền đăng ký tự cô lập. Từ
2026-08-08 còn một lý do mạnh hơn: `forward/logger.py` ĐÓNG BĂNG với
SHA256 ghim, nên nối live loop vào nó sẽ ép mọi nhu cầu đổi hành vi sau
này lên đúng file không được sửa. Nhân bản 5 dòng theo đúng tiền lệ
`_latest_closed_bar_date()`, kèm
`test_bug1_khop_voi_ban_forward` khẳng định hai bản không trôi lệch —
đó là thứ khiến nhân bản chấp nhận được thay vì chỉ là sao chép. Kèm tham số **`process_one_bar(execute: bool)`**
— bar cũ chỉ tua TRẠNG THÁI (regime, bộ đếm ổn định, alpha forward
algorithm, lịch sử trend gate), tuyệt đối không đặt lệnh. Không có tham số
này thì bản sửa tạo ra bug tệ hơn bug nó sửa: signal của bar D-3 tính trên
giá D-3, đặt lệnh hôm nay theo nó là khớp quyết định của ba ngày trước ở
giá hiện tại. `execute=False` cũng không đổi `current_allocation_pct`/
`current_stop_loss` (không lệnh nào chạy → vị thế thật không đổi), không
phát alert, không đo lệch đồng hồ, và breach stop-loss chỉ được GHI NHẬN
— bar cuối cùng mới là chỗ hành động.

**2. `close_position()` `order_link_id` không deterministic.** Chữ ký đổi
thành `close_position(symbol, bar_timestamp)`, **bắt buộc**, không mặc
định `None` với fallback `datetime.now()` — một mặc định "tiện" ở đây tái
tạo đúng bug đang sửa và không caller nào lộ ra. `close_all_positions()`
cũng nhận `bar_timestamp` cùng lý do.

**3. `generate_order_link_id()` không normalize `Decimal`.**
`Decimal("0.30") == Decimal("0.3")` là TRUE nhưng `str()` ra hai chuỗi
khác nhau → hai hash → hai orderLinkId cho cùng một quyết định. Dùng
`f"{target_allocation.normalize():f}"`; `:f` (không phải `str()`) chặn ký
hiệu mũ mà `normalize()` sinh ra cho số 0 (`Decimal("0E-5")`).
**Cảnh báo triển khai đã ghi vào `ops/RUNBOOK.md`** — đổi công thức hash
làm MỌI id thay đổi, chỉ deploy khi không có lệnh nào đang chờ.

**4. Breach stop-loss đi vòng qua risk manager.** Bản cũ gọi thẳng
`close_position()`. Sửa: dựng signal `target_allocation_pct=0` và đưa qua
`validate_signal()`. Phương án "chỉ kiểm halt rồi đóng" bị BỎ vì không
thoả bất biến #4 (lệnh không đi qua điểm phủ quyết).

`RiskManager._approve_exit()` — `validate_signal()` **LUÔN duyệt** lệnh
giảm về 0, kiểm TRƯỚC mọi cổng từ chối. Lý do: stop-loss bị chặn vì
`max_daily_trades`/circuit breaker/halt lock nghĩa là giữ nguyên một vị
thế đang lỗ — tệ hơn hẳn thứ các cổng đó bảo vệ. Không phá bất biến #2:
lệnh này chỉ GIẢM exposure. Vẫn `circuit_breaker.update()` và vẫn tăng
`_daily_trade_count` — "không chặn" không có nghĩa "không ghi nhận".
Thêm kiểm `halt_lock_path` mỗi bar trong `process_one_bar()`, không chỉ
lúc khởi động.

**5. `_requested_qty` mất khi restart.** `handle_partial_fill()` fallback
đọc từ SÀN (`get_open_orders()`: `qty - filled_qty`) thay vì persist ra
file — sàn là nguồn sự thật, không mất khi restart, nhất quán với nguyên
tắc "đối soát, tin sàn" của `position_tracker`. Thêm `Order.filled_qty`
(mặc định 0, nối vào CUỐI dataclass — không đảo thứ tự field) và map
`item["filled"]` trong `ccxt_client.get_open_orders()`. `broker/bybit_client.py`
(deprecated) không đụng tới; nó dựng `Order` bằng keyword nên vẫn chạy.

**6. Equity tính từ `balance.total`.** Thêm pre-flight sau khi tính
`limit_price`: chiều MUA mà `qty * limit_price > balance.available` →
warning + `OrderResult(REJECTED)`, không gửi ra sàn. Chỉ áp cho chiều
mua — bán làm giảm exposure và không tiêu số dư.

**7. `round_price()` luôn `ROUND_DOWN`.** Thêm tham số hướng: BUY →
`ROUND_DOWN` (không vượt số dư), SELL rebalance → `ROUND_UP` (bán là NHẬN
tiền; làm tròn xuống là tự nguyện nhận ít hơn). Mặc định giữ `ROUND_DOWN`
nên `tests/test_precision.py` không đổi.

**Ghi rõ trong docstring vì đây là chỗ dễ bị "sửa lại cho đúng":**
CLAUDE.md bất biến #3 quy định `ROUND_DOWN` cho **SỐ LƯỢNG**, không phải
GIÁ. Tham số hướng ở đây không vi phạm bất biến — nó nằm ở một đại lượng
khác. Thoát bảo vệ khi thủng stop ưu tiên KHỚP ĐƯỢC: `close_position()`
dùng `OrderType.MARKET` nên không đi qua `round_price`; nếu sau này
chuyển sang LIMIT thì phải `ROUND_DOWN`, không phải `ROUND_UP`.

**8. `_build_regime_infos()` nổ khi thiếu `log_return_1`.**
`_validate_feature_names()` chạy ở dòng ĐẦU của `select_and_train()` (và
của `load()`), trước `scan_bic()`. Thông điệp nêu rõ feature thiếu, liệt
kê feature đang có, và trỏ tới `settings.yaml`.

**Lệch khỏi chỉ dẫn:** yêu cầu ghi "validate trong `__init__`", nhưng
`HMMRegimeEngine.__init__` **chưa có feature nào để kiểm** —
`feature_names` chỉ tồn tại sau `select_and_train()`/`load()`. Hai chỗ đó
là điểm sớm nhất phép kiểm này thực hiện được; đã ghi lý do vào docstring
`_validate_feature_names`.

**9. `compute_all_features()` tính lại mỗi 60s.** `main.FeatureCache` —
**CHỈ cache**, khoá theo `len(ohlcv)` + SHA256 của
`hash_pandas_object(ohlcv, index=True)`. Hash giá trị (không chỉ độ dài)
vì sàn có thể sửa lại nến lịch sử. **KHÔNG tính tăng dần** — z-score 365
bar/SMA200/ATR đều phụ thuộc cửa sổ, bản tăng dần gần như chắc chắn lệch
nhẹ, và lệch nhẹ làm `test_wiring_equivalence`/`test_forward_golden` đỏ
hoặc tệ hơn là lệch âm thầm. `test_bug9_ket_qua_giong_het_ban_khong_cache`
ghim điều này bằng `assert_frame_equal`.

Đặt cache ở `main.py` (tầng gọi) chứ KHÔNG ở `data/feature_engineering.py`
— CLAUDE.md bất biến #11 nói module đó chứa hàm THUẦN, không state, không
I/O. Thêm cache module-level vào đó sẽ phá đúng tính chất khiến việc kiểm
tra look-ahead bias khả thi.

### Khoảng trống đã lấp: `run_live_loop(max_iterations=...)`

Bản đầu ghi nhận một giới hạn: vòng lặp vô hạn không chạy được trong test
suite, nên phần nối dây `_pending_bar_dates` + `execute=is_latest` chỉ
được phủ gián tiếp bằng cách gọi tay `process_one_bar()` theo đúng chuỗi
mà vòng lặp gọi — tức là kiểm một BẢN SAO của logic, không phải logic
thật. **Đã lấp.**

`run_live_loop(args, settings, max_iterations=None)` — `None` (mặc định,
và là thứ duy nhất vận hành thật dùng) giữ nguyên vòng lặp vô hạn, không
đổi một hành vi nào. `N` thoát sau đúng N vòng. Đếm MỌI vòng, kể cả vòng
thoát sớm bằng `continue` — chỉ đếm vòng có xử lý bar thì `max_iterations`
không chặn được một vòng lặp đang quay tít ở nhánh lỗi, đúng thứ nguy hiểm
nhất cần chặn được trong test.

`tests/test_live_loop_iterations.py` (9 test) chạy `run_live_loop()` THẬT
3 vòng, chỉ giả lập BIÊN chạm ra ngoài (sàn, tải lịch sử, health check,
đồng hồ, alert):

| vòng | bar chưa xử lý | kết quả |
|---|---|---|
| 1 | LAST-3, LAST-2, LAST-1 | 2 bar đầu `execute=False`, bar cuối `True` |
| 2 | không có bar mới | sleep, không xử lý bar nào |
| 3 | LAST | `execute=True` |

Khẳng định: đúng 4 bar được xử lý với đúng cờ `execute`; đúng 2 lệnh
`submit_order` ở đúng 2 bar `execute=True`, không lệnh nào mang timestamp
của bar bị lỡ; alert = 0 ở mọi bar bị lỡ.

**Hai lỗi trong chính test, tìm ra bằng đột biến — cả hai đều làm test
xanh RỖNG NGHĨA:**

1. **Fake `OrderBook` dựng sai.** `best_bid`/`best_ask` là `@property`
   tính từ `bids`/`asks`, không phải field constructor. Truyền nhầm →
   `OrderBook(...)` ném `TypeError` → `_check_spread_and_alert` nuốt lỗi
   thành `DATA_FEED_LOST` → phép kiểm spread không bao giờ chạy.
2. **State khôi phục để `current_regime_id=None`.** `_fire_bar_alerts()`
   chỉ phát khi giá trị mới KHÁC giá trị đang mang, và bỏ qua hoàn toàn
   khi state là `None`. Bar bị lỡ vì thế không bao giờ tạo alert — dù
   guard `and execute` còn hay mất. Đột biến bỏ guard: test **vẫn xanh**.
   Sửa bằng SENTINEL (`999`/`"SENTINEL_REGIME"`/`"SENTINEL_TREND"`) để bar
   bị lỡ đầu tiên luôn là một "thay đổi".

Ngoài ra dữ liệu tổng hợp ban đầu (`sigma=0.012`) làm bar cuối thủng stop
→ `process_one_bar()` đi nhánh THOÁT thay vì nhánh thực thi, `submit_order`
chỉ 1 lần thay vì 2. Hạ xuống `sigma=0.004`;
`test_khong_co_breach_stop_loss_trong_kich_ban` khoá tiền đề này lại.

Đột biến: **6/6 bị bắt** — luôn `execute=True`; chỉ xử lý bar mới nhất
(bug gốc); alert phát ở bar bị lỡ; `pending` bỏ qua `last_processed`; hai
đột biến làm vòng lặp vô hạn (bị bắt bằng treo/timeout, ghi rõ là "treo"
chứ không phải "đỏ bằng assertion").

391 passed / 0 skipped (348 + 43 mới). Bảy test bắt buộc xanh, không
skip/xfail. ruff + mypy sạch.

---

## 2026-08-08 (sau nữa) — Thu hẹp exception handling: lỗi lập trình ≠ sự cố vận hành

`TypeError`/`AttributeError`/`KeyError` nghĩa là giả định của chính chúng
ta về hợp đồng dữ liệu đã sai — không phải mạng chập, không phải sàn 5xx.
Gộp chúng vào `DATA_FEED_LOST`/`API_LOST` tạo ra chế độ hỏng tệ nhất:
người vận hành đọc alert "mất feed", quyết định **CHỜ**, và bug nằm im vô
thời hạn.

Không phải giả thuyết. Nó vừa xảy ra trong chính test của dự án này:
fake `OrderBook` dựng bằng `best_bid=`/`best_ask=` (vốn là `@property`,
không phải field constructor) ném `TypeError`,
`_check_spread_and_alert` nuốt thành `DATA_FEED_LOST`, và phép kiểm
spread im lặng không chạy lần nào — phát hiện bằng đột biến, không phải
bằng test đỏ. Cùng triệu chứng sẽ xảy ra khi chạy thật nếu một field ở
tầng broker đổi tên.

### Rà soát toàn bộ `except Exception` trong đường live loop

| # | Vị trí | Trước | Sau |
|---|---|---|---|
| 1 | `main.py::_check_spread_and_alert` (fetch orderbook) | mọi lỗi → `DATA_FEED_LOST` | **tách**: lỗi lập trình → `INTERNAL_ERROR`; còn lại → `DATA_FEED_LOST` (+ `exc_info`) |
| 2 | `main.py::_check_clock_drift` (đo giờ) | mọi lỗi → `warning`, `(False, None)` | **tách**: lỗi lập trình → `INTERNAL_ERROR`; còn lại giữ `warning` |
| 3 | `main.py::run_live_loop` (retrain HMM) | mọi lỗi → "giữ nguyên model cũ" | **tách**: lỗi lập trình → `INTERNAL_ERROR` (vẫn giữ model cũ) |
| 4 | `main.py::run_live_loop` (catch-all vòng lặp) | mọi lỗi → `API_LOST` | **tách**: lỗi lập trình → `INTERNAL_ERROR`; còn lại → `API_LOST` |
| 5 | `main.py::run_live_loop` (nạp model lúc khởi động) | `warning` + train mới | **tách**: lỗi lập trình → `ERROR` nêu rõ "cần sửa code" (vẫn train mới; `alert_manager` chưa tồn tại ở bước này) |

**Đã rà, CỐ Ý không đổi:**

| Vị trí | Lý do giữ nguyên |
|---|---|
| `monitoring/alerts.py` ×3 (`_send_telegram`/`_send_email`/`_send_webhook`) | Hợp đồng của chúng là "không bao giờ raise ra ngoài" — một kênh alert hỏng không được làm sập vòng lặp giao dịch. Thu hẹp ở đây sẽ để lỗi lập trình trong kênh gửi làm chết bar đang xử lý, đắt hơn hẳn cái được. |
| `ops/health_check.py` ×3 | Chạy TRƯỚC khi vào vòng lặp; mục đích là trả `FAIL` có thông điệp thay vì traceback. Không có `alert_manager` ở đó. |
| `broker/bybit_client.py:274` | File deprecated, có cam kết "giữ nguyên, không sửa logic, không xoá". |
| `monitoring/forward_watchdog.py:198` | Cố ý rộng: MỌI lỗi đọc `log.csv` đều là "thí nghiệm đang chết", đúng thứ watchdog phải báo. |

### `_PROGRAMMING_ERRORS` — vì sao đúng ba loại

`ValueError` **cố tình không có mặt**: vừa là lỗi lập trình vừa là cách
hợp lệ để báo dữ liệu đầu vào xấu (`Decimal("abc")`, parse timestamp
hỏng), không phân loại được nếu chỉ nhìn kiểu. `IndexError` cũng không:
`response["list"][0]` trên một phản hồi rỗng của sàn LÀ sự cố dữ liệu
thật, không phải bug của ta.

`AlertType.INTERNAL_ERROR` mới, `severity="ERROR"`, thông điệp luôn kèm
"lỗi lập trình, không phải mất feed" — hai loại này cần hành động khác
hẳn nhau và alert phải nói ra điều đó ngay dòng đầu.

Thứ tự `except` quyết định nhãn: nhánh `_PROGRAMMING_ERRORS` PHẢI đứng
trước nhánh rộng. `test_danh_sach_loi_lap_trinh_dung_ba_loai` ghim danh
sách để việc nới nó thành một quyết định có ý thức.

### Test + đột biến

`tests/test_exception_classification.py` (12 test): tiêm từng loại lỗi
vào đường spread và đường đo giờ, khẳng định lỗi lập trình **không**
sinh `DATA_FEED_LOST`, và — chiều ngược lại — lỗi hạ tầng
(`ConnectionError`/`TimeoutError`/`OSError`/`RuntimeError` đứng thay
`ccxt.*`) **vẫn** sinh `DATA_FEED_LOST`. Việc thu hẹp không được làm mất
cảnh báo thật.

Đột biến: **6/6 bị bắt** — bỏ nhánh lỗi lập trình ở cả hai hàm; làm
`_PROGRAMMING_ERRORS` rỗng; nới sang `ValueError`; alert mất câu "không
phải mất feed"; hạ `severity` xuống `WARNING`.

403 passed / 0 skipped. ruff + mypy sạch.

---

## 2026-08-08 (cuối) — `AlertManager`: sức khoẻ từng kênh, `${STATE_DIR}/status.json`

Cam kết "không bao giờ raise" của `send()` bảo vệ vòng lặp giao dịch —
một kênh alert hỏng không được làm crash bot đang cố báo một sự cố khác.
Nhưng bản trước **trả giá bằng sự im lặng hoàn toàn**: Telegram trả 401
mọi lần (token bị revoke) trông y hệt gửi thành công ở mọi chỗ khác trong
hệ thống. Giữ cam kết, bỏ cái giá.

### 1. Kênh file là đường cuối cùng, try RIÊNG

`_send_file()` mới. Bản trước gọi thẳng `self._alert_logger.info(...)`
trong `send()` **không có `try` nào** — đĩa đầy hoặc rotating handler lỗi
sẽ ném ra khỏi `send()` và phá cam kết ở đúng kênh quan trọng nhất.

Thứ tự: kênh cục bộ (file, console) TRƯỚC, kênh từ xa SAU. Mỗi kênh một
`try` riêng, không kênh nào chung `try` với kênh khác.

**Ghi chính xác để không tự lừa mình:** vì mỗi kênh đã có `try` riêng,
thứ tự KHÔNG còn ảnh hưởng hành vi — một đột biến đảo thứ tự thuần tuý
sẽ không (và không nên) bị test bắt. Tính chất được ép buộc thật sự là
"mỗi kênh một try riêng, kênh file luôn được gọi"; thứ tự là lớp phòng
thủ mang tính tài liệu, đọc từ trên xuống thấy ngay ý định.

### 2. Đếm thất bại theo từng kênh

`ChannelHealth` (dataclass KHÔNG frozen — bộ đếm sống, khác `Alert`):
`attempts`, `failures`, `consecutive_failures`, `last_error`,
`last_failure_at`, `last_success_at`.

`consecutive_failures` reset về 0 khi có một lần THÀNH CÔNG — "degraded"
mô tả tình trạng HIỆN TẠI, không phải lịch sử; tổng `failures` vẫn giữ để
đọc lại được.

Chỉ theo dõi kênh **đã cấu hình**: một kênh không bật thì `_send_*` trả
về ngay, đó không phải thất bại. Đếm chúng sẽ làm mọi cài đặt tối thiểu
trông như degraded vĩnh viễn.

**HTTP != 200 giờ tính là thất bại** (Telegram), `>= 400` (webhook). Bản
trước chỉ `logger.warning` rồi đi tiếp — đó chính là chỗ một token bị
revoke ẩn được.

### 3. Ngưỡng degraded

`status()` trả `"degraded"` nếu **bất kỳ** kênh nào có
`consecutive_failures >= degraded_after` (mặc định **3**).

3 chứ không phải 1: một lần Telegram 502 hay SMTP timeout là chuyện
thường ngày và không có nghĩa kênh đã chết. Hạ trạng thái ngay lần đầu
biến "degraded" thành trạng thái mặc định, và một chỉ báo lúc nào cũng đỏ
thì không ai đọc nữa.

`any` chứ không `all`: kênh file vẫn khoẻ nhưng Telegram chết nghĩa là
cảnh báo KHÔNG tới điện thoại — trạng thái tổng thể phải phản ánh điều đó.

**KHÔNG có kênh nào cũng là `"degraded"`.** Phát hiện khi in thử
`status.json` đầu tiên: một `AlertManager` không kênh nào báo
`status: "ok"` với `channels: {}` — dạng cực đoan nhất của chính thứ
cơ chế này sinh ra để chặn: 100% cảnh báo đi vào hư không mà chỉ báo
vẫn xanh. Không xảy ra ở vận hành thật (`build_alert_manager` luôn
truyền `log_dir`, console mặc định bật), nhưng một AlertManager không
kênh nào không phải "khoẻ", nó là "câm".

Log ĐÚNG một lần ở mỗi lần đổi trạng thái (vào/ra degraded), không phải
mỗi lần thất bại: một kênh chết sẽ thất bại mỗi alert, và log mỗi lần
biến chính dòng log đó thành nhiễu.

### `${STATE_DIR}/status.json`

Ghi NGUYÊN TỬ (tmp + rename, cùng lý do `main.py::write_state_snapshot`),
sau mỗi `send()`. `write_status()` **không bao giờ raise** — nó chạy bên
trong `send()`, nên một đĩa đầy không được làm crash vòng lặp giao dịch.

**Đường dẫn: đã chuyển từ `monitoring/state/status.json` sang
`${STATE_DIR}/status.json`.** Bản đầu đặt trong `monitoring/` theo đúng
chỉ dẫn, kèm ghi chú rằng nó gợn: `monitoring/` là thư mục MÃ NGUỒN, còn
đây là state runtime. Nay gom về cùng chỗ với `state_snapshot.json` và
`trading_halted.lock` — cùng một volume đã mount
(`ops/docker-compose.yml`: `STATE_DIR=/app/state`), cùng một đường sao
lưu, cùng một thứ để xoá khi muốn bắt đầu sạch. `.gitignore` đổi từ
`monitoring/state/` sang `state/`.

`_default_status_path()` đọc `STATE_DIR` ở **thời điểm gọi**, không phải
hằng số mức module: env đó được đặt lúc chạy, còn module có thể được
import trước đó — một hằng số tính lúc import sẽ đóng băng giá trị sai và
ghi status ra NGOÀI volume đã mount, tức là mất sạch mỗi lần container
restart, đúng lúc lịch sử sức khoẻ kênh có ích nhất.

**Hợp đồng của `send()` KHÔNG đổi:** giá trị trả về vẫn chỉ nói alert có
bị rate-limit hay không, không phản ánh kênh nào thất bại
(`_SpyAlertManager` trong test suite dựa vào điều này). Thất bại nằm ở
`status()`/`health_snapshot()`/`status.json` — đó mới là chỗ đúng, vì
caller không làm gì được với thông tin "webhook lỗi" giữa lúc đang xử lý
một bar.

### Test + đột biến

`tests/test_alert_channel_health.py` (16 test). Đột biến **10/10 bị bắt**:
bỏ gọi kênh file; bỏ `try` quanh kênh file; HTTP != 200 không tính thất
bại; không ghi `status.json`; thành công không reset chuỗi; ngưỡng hạ
xuống 1; `any` -> `all`; đếm cả kênh chưa cấu hình; không kênh nào vẫn
báo ok; `write_status` raise được.

419 passed / 0 skipped. ruff + mypy sạch.

---

## Điểm dữ liệu `warning_count` ĐẦU TIÊN đo được — 2026-08-08 (lần retrain #2)

Ghi ở đây vì thông tin này trước đó chỉ nằm trong commit message
(`6cf5fee`), mà commit message thì phải `git log --grep` mới tìm ra. Mục
đích: lần sau có mốc để so, thay vì phải đoán "23478 là nhiều hay ít".

### Số đo

| | |
|---|---|
| Bar | 2026-08-08 |
| `hmm_train_bars` | 2660 |
| `warning_count` | **23478** |

**Xác minh hai đường độc lập:** `wc -l forward/warnings.log` = 23478, và
cột `warning_count` của dòng 08-08 trong `log_v2.csv` = 23478. Hai con số
khớp — nghĩa là cơ chế chuyển hướng warning không mất dòng nào và không
đếm trùng.

**Nguồn:** `RuntimeWarning` — "divide by zero / overflow / invalid value
encountered in matmul" từ `sklearn/utils/extmath.py:203` và
`sklearn/cluster/_kmeans.py:237`, tức đường `.fit()` EM/k-means của
hmmlearn/sklearn. Đã điều tra 2026-08-07 (mục "Bổ sung hạ tầng"):
**không phải bug** — `predict_regime_filtered` chạy sạch, cô lập bằng
`warnings.simplefilter("error")` quanh từng lệnh gọi riêng, `log_alpha`
trên 2657 bar nằm trong [-22815, -9.2], cách rất xa giới hạn float64.

### Điểm #1 KHÔNG đo được — chuỗi thực tế mới có MỘT điểm

| # | Bar | `hmm_train_bars` | `warning_count` |
|---|---|---|---|
| 1 | 2026-08-05 | 2657 | **NaN** — schema v1 chưa có cột này |
| 2 | 2026-08-08 | 2660 | 23478 |

Lần retrain đầu tiên xảy ra dưới schema v1, vốn không có cột
`warning_count` (đó chính là cột được thêm vào gây ra sự cố lệch schema —
xem mục 2026-08-08 phía trên). Nên xét theo **thứ tự lần retrain** thì
08-08 là điểm #2, nhưng xét theo **chuỗi đo được** thì nó là điểm ĐẦU
TIÊN. Quy tắc §C.1 ("xu hướng tăng đơn điệu 3 lần liên tiếp") không thể
tính 08-05 vào chuỗi: không so sánh được với `NaN`.

### Sớm nhất kích hoạt được: 2026-08-29 (đọc theo cách thận trọng)

`retrain_interval_days = 7` (`forward/config_frozen.yaml`), nên các điểm
đo được rơi vào ~08-08, ~08-15, ~08-22, ~08-29.

**Câu chữ §C.1 mơ hồ, hai cách đọc cho hai mốc khác nhau.** Nguyên văn:
"Xu hướng tăng đơn điệu **3 lần liên tiếp**".

| Cách đọc | Cần | Sớm nhất |
|---|---|---|
| A — 3 ĐIỂM liên tiếp tăng dần | 08-08, 08-15, 08-22 | 2026-08-22 |
| B — 3 LẦN TĂNG liên tiếp (tức 4 điểm) | + 08-29 | 2026-08-29 |

**Chốt: dùng cách đọc B, mốc 2026-08-29.** Lý do chọn cách muộn hơn: nếu
lấy 08-22 mà thực tế quy tắc cần 4 điểm, người theo dõi sẽ thấy "đã tới
mốc mà không có cảnh báo" và đi tìm bug không tồn tại. Sai theo hướng chờ
lâu hơn thì cùng lắm là phát hiện muộn một chu kỳ 7 ngày; sai theo hướng
kia tạo ra một cuộc điều tra vô ích.

Khi xây `monitoring/drift.py` (Phase 12b §C.1), **chốt cách đọc trong
code và ghi lại ở đây** — đừng để nó tiếp tục mơ hồ ở tầng cài đặt.

*(Bản đầu của mục này khẳng định 08-22 là đúng và quy 08-29 cho một lỗi
tính toán. Đó là tôi đoán lý do của người khác rồi ghi phỏng đoán đó vào
hồ sơ như sự thật — câu chữ §C.1 chịu được cả hai cách đọc, và không có
gì trong bảng nói cách nào mới đúng.)*

### Phát hiện kèm theo: lần retrain này lệch nhịp

08-05 → 08-08 cách nhau 3 ngày, không phải 7. Ghi thành mục riêng —
**"Sai lệch thí nghiệm #1"** ngay dưới đây — vì nó là một sai lệch của
chính thí nghiệm, không phải một ghi chú về `warning_count`.

---

## Sai lệch thí nghiệm #1 — lịch retrain bị reset khi cuộn schema (2026-08-08)

> **Sổ đăng ký sai lệch.** Mục này mở đầu một danh sách ĐÁNH SỐ: mọi chỗ
> thí nghiệm forward chạy KHÁC đặc tả, dù vô hại, đều được ghi ở đây với
> số thứ tự tăng dần. Ở mốc đánh giá 12 tháng (2027-08-06), người dựng lại
> "thí nghiệm có chạy đúng đặc tả không" cần đọc MỘT danh sách, không phải
> lục `git log --grep` hay suy ra từ tài liệu schema.

### Sự kiện

Retrain xảy ra ngày **2026-08-08**, cách lần trước (2026-08-05) **3 ngày**
thay vì 7 như `retrain_interval_days` quy định.

### Nguyên nhân

`run_forward_test()` suy ra `last_retrain_date` bằng cách quét cột
`hmm_retrained` trong file log **ĐANG HOẠT ĐỘNG**:

```python
retrained_rows = existing[existing["hmm_retrained"]]
last_retrain_date = retrained_rows["date"].max().date()  # None nếu rỗng
```

Sau khi cuộn sang `log_v2.csv` (xem mục "Forward test dừng im lặng"), file
đó chỉ chứa 08-06 và 08-07 — cả hai `hmm_retrained=False`. Lần retrain
08-05 nằm ở `log.csv` đã đóng nên **vô hình** với đường này →
`last_retrain_date = None` → runner coi như chưa từng retrain → retrain
ngay ở lần chạy kế tiếp.

### Ảnh hưởng

Một lần retrain sớm 4 ngày. **Không mất bar nào, không sai giá trị nào.**
Từ 08-08 trở đi `log_v2.csv` tự mang lịch sử retrain của chính nó, nhịp 7
ngày chạy lại bình thường (kế tiếp ~08-15).

### KHIẾM KHUYẾT ĐÃ BIẾT — KHÔNG SỬA ĐƯỢC

`forward/logger.py` đóng băng với SHA256 ghim trong
`tests/golden/frozen_hashes.json`. Sửa nó = **kết thúc thí nghiệm hiện
tại** (CLAUDE.md bất biến #15). Một lần retrain lệch nhịp không đáng để
đánh đổi 12 tháng dữ liệu.

**Chấp nhận sống chung tới 2027-08-06.**

Cách phòng cho tương lai không phải là sửa `logger.py`, mà là **không cuộn
schema lần nữa** — xem `forward/SCHEMA.md`, mục "KHÔNG cuộn schema lần nữa
trong thời gian thí nghiệm" (dùng file phụ `forward/extra_<tên>.csv` khoá
theo `bar_date` thay vì `log_v3.csv`).

### Vì sao ghi ở ĐÂY chứ không chỉ trong SCHEMA.md

`forward/SCHEMA.md` trả lời câu hỏi *"tôi sắp đổi schema, phải làm gì?"* —
người đọc nó là người sắp sửa code. Mục này trả lời câu hỏi khác:
*"thí nghiệm đã chạy đúng đặc tả chưa?"* — người đọc là người đang thẩm
định kết quả ở mốc 12 tháng, và họ sẽ thấy một lần retrain lệch nhịp trong
`log_v2.csv` rồi cần lời giải thích **ngay trong hồ sơ thí nghiệm**.

Hai tài liệu, hai câu hỏi, hai nhóm người đọc. Không phải trùng lặp.

### Điều đáng lo hơn bản thân sai lệch

Sai lệch này vô hại. Cái đáng lo là **nó chỉ được phát hiện ba ngày sau,
một cách tình cờ** — khi kiểm số liệu để viết mục `warning_count` và thắc
mắc vì sao hai lần retrain cách nhau 3 ngày. Không có phép kiểm nào canh
"nhịp retrain có đúng `retrain_interval_days` không".

Chưa thêm phép kiểm đó: nó sẽ phải đọc `log_v2.csv` và biết về lần reset
hợp lệ ở 08-08, tức là một ngoại lệ hardcode ngay từ ngày đầu. Ghi lại như
một khoảng trống đã biết thay vì dựng một cái canh gác biết trước là sẽ
phải nói dối.


## Ngưỡng drift §C.1 quá chặt so với nhiễu cửa sổ 30 bar (2026-08-14)

**Đo, không suy đoán.** Cài xong `monitoring/drift.py` theo đúng §C.1
(cửa sổ trượt 30 ngày, ngưỡng 15 điểm % cho phân bố allocation, 20 điểm %
cho thời gian trend gate chặn HMM), rồi trượt cửa sổ đó qua chính baseline
Phase 7:

| | Tỷ lệ cửa sổ baseline tự báo động |
|---|---|
| Phân bố allocation (4 mức) | **99.0 %** |
| Thời gian trend gate chặn HMM | **72.9 %** |
| Ít nhất một cảnh báo | **99.7 %** (2255/2262 cửa sổ) |

Nguyên nhân: phân bố allocation trên cửa sổ 30 bar có **độ lệch chuẩn ~41
điểm %**. Ngưỡng 15 điểm nằm sâu bên trong nhiễu tự nhiên. Đổi mốc so từ
"toàn kỳ" sang "trung vị của cửa sổ" KHÔNG cứu được (100.0 % / 32.7 %) —
vấn đề nằm ở kích thước cửa sổ so với biên độ chỉ số, không nằm ở chọn mốc.

Bề rộng dải p1–p99 của phân bố allocation theo kích thước cửa sổ:

```
 30 bar : [100.0, 100.0,  98.0, 100.0]
 90 bar : [100.0,  55.6,  70.0,  95.6]
182 bar : [100.0,  40.1,  51.1,  92.9]
365 bar : [ 87.2,  35.9,  30.0,  81.9]
```

Ba trong bốn rổ phủ trọn 0–100 % ở cửa sổ 30 bar: chỉ số này gần như
KHÔNG mang thông tin ở kích thước cửa sổ mà §C.1 quy định.

> **SỬA 2026-08-14:** câu gốc ở đây viết *"Nó bắt đầu có nghĩa từ khoảng
> 180 bar."* — **SAI**, suy ra từ bề rộng dải chứ không đo trực tiếp. Đo
> trực tiếp thì ở 182 bar chỉ số này vẫn KHÔNG phát hiện được một bot hỏng
> hoàn toàn; mốc thật là 365 bar. Xem mục "ĐO #3" ở cuối file.

### Quyết định

Giữ nguyên mọi ngưỡng §C.1 — **không nới một con số nào**. Thêm một điều
kiện THỨ HAI: cảnh báo chỉ bật khi giá trị đồng thời **nằm ngoài dải
p1–p99 của chính baseline đo trên cửa sổ CÙNG kích thước**
(`monitoring/drift.py::Bands`). Đủ LỚN (ngưỡng §C.1) **và** đủ HIẾM (dải).

Kết quả đo lại: tỷ lệ báo động sai trên chính baseline **99.7 % → 1.02 %**.

Vì sao không chọn cách khác:

- *Nới ngưỡng*: sẽ phải nới lên hơn 40 điểm % để im, lúc đó ngưỡng không
  còn phát hiện được gì.
- *Kéo dài cửa sổ lên 180 bar*: đúng về mặt thống kê, nhưng §C.1 nói rõ 30
  ngày, và một chỉ báo phản ứng sau sáu tháng thì quá chậm cho vai trò
  "phát hiện trôi lệch trước khi thua lỗ xuất hiện". `run(window_days=...)`
  nhận tham số nên vẫn thử được khi có nhu cầu.
- *Bỏ chỉ số*: nó vẫn có giá trị QUAN SÁT (in ra mỗi lần chạy) kể cả khi
  chưa đủ tin cậy để báo động.

### Hệ quả phải biết

Ở cửa sổ 30 bar, chỉ số "phân bố allocation" thực tế chỉ báo động ở những
trường hợp rất cực đoan. Đừng đọc "drift im lặng" thành "hành vi khớp
baseline" — nó chỉ nghĩa là chưa có gì đủ cực đoan để phân biệt được với
nhiễu. `tests/test_drift.py::test_dai_allocation_gan_nhu_phu_tron_o_cua_so_30_bar`
giữ bằng chứng cho điều này và sẽ đỏ nếu ai đó lặng lẽ đổi cách tính dải.


## Phân loại 8 ngưỡng còn lại chưa có bằng chứng phân phối (2026-08-14)

Bổ sung cho bảng ở mục "Ngưỡng drift §C.1..." phía trên và cho `CLAUDE.md`
#18. `CLAUDE.md` #18 liệt kê 8 ngưỡng trong `config/settings.yaml` và mã
`monitoring/` vẫn là **số tròn chưa được chứng minh**. Không phải cả 8 đều
cần đo — phân loại trước, rồi mới đo phần đáng đo.

### Nhóm 1 — CÓ CĂN CỨ, chỉ thiếu ghi nguồn gốc

| Ngưỡng | Giá trị | Căn cứ |
|---|---|---|
| `clock_drift_halt_ms` | 2500 | Nửa `recvWindow` mặc định 5000ms của Binance. Vượt quá nửa cửa sổ đó thì request ký bắt đầu bị từ chối `-1021`. **Ràng buộc của sàn, không phải phân phối** — không có gì để đo. |
| `clock_drift_alert_ms` | 1000 | Cảnh báo sớm trước ngưỡng halt, cùng ràng buộc. |

Việc phải làm: ghi nguồn gốc vào docstring/comment. Đã có trong
`config/settings.yaml`; không cần đo.

### Nhóm 2 — CẦN ĐO, nghi ngờ sai. Đã đo, xem ba mục dưới.

`large_pnl_alert_pct` 2.0, `WARNING_TREND_LEN` 3, `WINDOW_DAYS` 30.

### Nhóm 3 — CHƯA CÓ BASELINE, hoãn

| Ngưỡng | Giá trị | Vì sao hoãn |
|---|---|---|
| `unfilled_order_degraded_seconds` | 300 | Cần phân phối thời gian chờ khớp của lệnh THẬT. Backtest không mô phỏng độ trễ khớp, nên không có dữ liệu để trượt cửa sổ. Đo sau khi testnet tích được vài chục lệnh. |

### Nhóm 4 — LỰA CHỌN VẬN HÀNH, không có sự thật nền, giữ nguyên

| Ngưỡng | Giá trị | Vì sao không đo được |
|---|---|---|
| `alert_rate_limit_seconds` | 900 | "Bao lâu thì một người chịu được một cảnh báo lặp lại" là sở thích, không phải tính chất của dữ liệu. |
| `_DEFAULT_DEGRADED_AFTER` | 3 | Đánh đổi giữa nhạy và ồn của kênh gửi; phụ thuộc độ tin cậy hạ tầng cụ thể, không phải chuỗi giá. |

---

## ĐO #1 — `large_pnl_alert_pct` (2026-08-14)

**Quy trình #18.** Nguồn: `tests/snapshots/phase7_baseline/equity_curve.csv`,
2290 bar daily return. Chỉ lấy chiều LỖ (1135 bar) vì cảnh báo này đọc từ
`CircuitBreaker.check().daily_dd`, vốn chỉ đo drawdown.

**Phân phối |daily drawdown| trên bar lỗ:**

| p50 | p70 | p80 | p85 | p90 | p95 | p97.5 | p99 |
|---|---|---|---|---|---|---|---|
| 0.695 | 1.230 | 1.799 | 2.261 | 2.931 | 3.884 | 4.914 | 6.351 |

**Ngưỡng cũ 2.0% = phân vị 82.4** (dự đoán ban đầu là p60–70 — đo được là
p82, tức là bảo thủ hơn dự đoán nhưng vẫn quá ồn).

| Ngưỡng | Phân vị | Lần/năm | So với breaker (3.85, 9.2 lần/năm) |
|---|---|---|---|
| **2.00 (cũ)** | p82.4 | **32.0** | 3.5× |
| 2.26 | p85 | 27.3 | 3.0× |
| **2.93 (mới)** | **p90** | **18.2** | **2.0×** |
| 3.36 | p92.5 | 13.7 | 1.5× |

**Quyết định: 2.0 → 2.93 (p90).** Tiêu chí chọn: cảnh báo này là tín hiệu
"chú ý" trước khi circuit breaker can thiệp thật, nên nó PHẢI phát thường
hơn breaker — nhưng 3.5× là 2.7 lần/tháng, đủ để người ta ngừng đọc. 2.0×
là tỷ lệ hợp lý cho một cảnh báo tiền đề: đủ dày để có ích, đủ thưa để
mỗi lần phát còn nghĩa lý.

**Báo động giả đo được: 32.0 → 18.2 lần/năm (−43%).**

---

## ĐO #2 — `WARNING_TREND_LEN` (2026-08-14)

**Giả thuyết không:** `warning_count` giữa các lần retrain là iid (không
có xu hướng). Dưới giả thuyết đó, xác suất `L` giá trị liên tiếp tăng đơn
điệu ngặt là **1/L!** — mọi thứ tự của L giá trị liên tục đều đồng khả
năng. Xác nhận bằng mô phỏng 200 000 chuỗi:

| L | P(báo động mỗi lần kiểm) | Lý thuyết 1/L! | Retrain 7 ngày/lần → 1 báo động giả mỗi |
|---|---|---|---|
| **3 (cũ)** | 0.1666 | 1/6 | **6.0 tuần** |
| **4 (mới)** | **0.0417** | 1/24 | **24 tuần (5.5 tháng)** |
| 5 | 0.0084 | 1/120 | 119 tuần (27.5 tháng) |

**Quyết định: 3 → 4.** Forward test 12 tháng ≈ 52 lần retrain. Ở L=3 điều
đó là **~8.7 báo động giả trong một thí nghiệm** — nhiều hơn số sự kiện
thật mà thí nghiệm có thể quan sát. Ở L=4 là ~2.2. L=5 (~0.4) thưa hơn
nữa nhưng cần 5 lần retrain = 35 ngày mới kích hoạt được lần đầu, và bỏ
lỡ một xu hướng thật kéo dài 4 lần.

**Báo động giả đo được: 1 lần mỗi 6.0 tuần → 1 lần mỗi 24 tuần (−75%).**

---

## ĐO #3 — `WINDOW_DAYS` — và SỬA một kết luận sai của chính tôi (2026-08-14)

Mục "Ngưỡng drift §C.1 quá chặt..." phía trên viết: *"Nó bắt đầu có nghĩa
từ khoảng 180 bar."* **Câu đó SAI.** Suy ra từ bề rộng dải chứ không đo
trực tiếp. Đo trực tiếp:

**FP của ngưỡng §C.1 MỘT MÌNH (không dải), trượt qua chính baseline:**

| W (bar) | 30 | 60 | 90 | 120 | 182 | 270 | 365 |
|---|---|---|---|---|---|---|---|
| FP | 99.7% | 98.4% | 96.3% | 95.0% | **91.9%** | 84.2% | **79.0%** |

**Kéo dài cửa sổ KHÔNG cứu được ngưỡng.** Ngay cả một năm tròn vẫn 79%.
Thứ cứu được là **dải phân vị**, không phải kích thước cửa sổ — đúng như
mục trước đã kết luận, nhưng lý do thì tôi đã ghi sai.

**Đo tiếp SỨC PHÁT HIỆN (ngưỡng + dải), thứ mục trước chưa hề đo:**

| W | FP | Bot kẹt hoàn toàn ở 1 rổ | Lệch 25 điểm % |
|---|---|---|---|
| 30 | 1.02% | **KHÔNG BẮT** | KHÔNG BẮT |
| 60 | 2.37% | **KHÔNG BẮT** | KHÔNG BẮT |
| 90 | 2.45% | **KHÔNG BẮT** | KHÔNG BẮT |
| 182 | 2.13% | **KHÔNG BẮT** | KHÔNG BẮT |
| 365 | 3.22% | **BẮT** | **BẮT** |

Ở cửa sổ 30–182 bar, chỉ số phân bố allocation **không phân biệt được một
bot hỏng hoàn toàn với hoạt động bình thường**. Đó không phải lỗi cài đặt:
"100% số bar ở mức allocation thấp nhất trong 30 ngày" là chuyện BÌNH
THƯỜNG của chiến lược này (một đoạn bear 30 ngày cho đúng như vậy). Ở cửa
sổ 365 bar thì không còn bình thường nữa, nên nó bắt được.

**Quyết định: cửa sổ theo TỪNG CHỈ SỐ, không phải một con số chung.**

- `WINDOW_DAYS = 30` — GIỮ NGUYÊN cho flicker, phí, rebalance, trend gate.
  Đây là các chỉ số phản ứng nhanh và cửa sổ dài chỉ làm chậm phát hiện.
- `ALLOCATION_WINDOW_DAYS = 365` — MỚI, chỉ cho phân bố allocation. Đây là
  kích thước cửa sổ NHỎ NHẤT đo được có sức phát hiện khác 0.

**Báo động giả đo được cho phân bố allocation: 99.7% (ngưỡng đơn thuần,
W=30) → 3.22% (ngưỡng + dải, W=365), và từ KHÔNG PHÁT HIỆN ĐƯỢC GÌ sang
bắt được cả hỏng toàn phần lẫn lệch 25 điểm.**

Đánh đổi phải biết: chỉ số này giờ phản ứng trong một năm, không phải một
tháng. Đó là giới hạn THẬT của đại lượng được đo, không phải lựa chọn cấu
hình — một cửa sổ ngắn hơn không cho phát hiện sớm hơn, nó cho *không phát
hiện gì cả*. Forward test bắt đầu 2026-08-06 nên chỉ số này chỉ có dữ liệu
đầy đủ từ 2027-08-06, trùng mốc 12 tháng.


## Quy tắc đã học, không lặp lại (chuyển từ STATE.md 2026-08-14)

Chuyển về đây theo quy tắc mới ở đầu `STATE.md`: mỗi mục dưới đây kể
một chuyện ĐÃ XẢY RA để giải thích vì sao quy tắc tồn tại, nên chỗ của
chúng là file này. `STATE.md` chỉ giữ một dòng trỏ tới đây.

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


## CI đỏ lần chạy đầu tiên — hai lỗi, hai nguyên nhân khác hẳn (2026-08-14)

`.github/workflows/ci.yml` chạy lần đầu trên `57d97f5` và đỏ ở cả hai job.

### LỖI 1 — `mypy .` đỏ trên CI, xanh ở local

**Đo trước, sửa sau.**

| | local | CI |
|---|---|---|
| Python | **3.9.6** | **3.11** |
| mypy | 1.19.1 | (không ghim → bản mới nhất) |
| pandas-stubs | **2.2.2.240807** | (không ghim → bản mới nhất cho 3.11) |
| số file mypy kiểm | 96 (tại HEAD) | 87 (tại `57d97f5`) |

**Số file 87 vs 96 KHÔNG phải lỗi:** CI chạy `57d97f5` (Phase 12d §A),
local đã ở `59305a7` (thêm §B–§E). Cùng commit thì cùng số. Ghi ra vì
"phạm vi khác nhau" là thứ #19 bắt phải kiểm, và ở đây câu trả lời là
"khác commit", không phải "khác cách đếm".

**Nguyên nhân thật:** không ghim phiên bản. Trên Python 3.9, pip chỉ thấy
tới `pandas-stubs==2.2.2.240807`; trên 3.11 nó cài bản mới hơn. 6 trong 10
lỗi là `Unused "type: ignore"` — tức là CI có stub MỚI HƠN, đã sửa những
thứ mà `# type: ignore` ở local đang vá.

### Quyết định: ghim LÙI về bản local đang có, không ghim TIẾN

`pandas-stubs==2.2.2.240807` có `requires-python >= 3.9` → cài được trên
CẢ 3.9 lẫn 3.11. Ghim nó làm hai bên chạy đúng cùng stub.

Ghim TIẾN (bản mới nhất cho 3.11) bị loại vì hai lý do, lý do thứ hai
nặng hơn:

1. Máy dev chạy 3.9 nên **không cài được** bản đó — "mypy xanh ở local"
   thành một câu không kiểm chứng được.
2. **6 "unused ignore" nằm trong `forward/logger.py` — FILE ĐÓNG BĂNG**
   (`tests/golden/frozen_hashes.json`). Ghim tiến buộc phải sửa nó, cập
   nhật hash, và theo `CLAUDE.md` #15 thì đó là **kết thúc thí nghiệm
   forward 12 tháng** — cho một thay đổi chú thích kiểu không đổi một
   hành vi nào. Ghim lùi giữ file đó **không bị chạm**.

### Bốn lỗi còn lại: sửa thật, không thêm ignore

- `tests/test_backtester.py:131` — `Series + Decimal` không có kiểu hợp lệ.
  Sửa bằng so từng phần tử với `Decimal`, KHÔNG ép `float`: ép float ở đây
  sẽ chấp nhận đúng loại sai lệch mà `Decimal` sinh ra để loại bỏ
  (`CLAUDE.md` #3). Phụ phẩm: khi đỏ nó chỉ ra ĐÚNG bar nào vi phạm.
- `tests/test_properties.py:137–138` — `.values` trả
  `ndarray | ExtensionArray`; nhân `ExtensionArray` với `float` không có
  kiểu. Sửa bằng `.to_numpy(dtype=float)`. Đây là dữ liệu giá dùng để sinh
  feature, nên `float` là đúng (`CLAUDE.md` #3 cho phép float cho feature).

Cả hai lỗi này **thật** dù stub cũ không thấy — sửa để chúng đúng dưới mọi
bộ stub.

### Hạn chế còn lại, không giấu

Máy dev chạy **Python 3.9.6** trong khi `pyproject.toml` khai
`python_version = "3.11"` và `CLAUDE.md` nói "Python 3.11+". Hôm nay bản
ghim che được khoảng cách đó vì nó hỗ trợ cả hai. **Ngày một dependency bỏ
3.9, chuyện này tái diễn.** Cách sửa gốc là cài Python 3.11 ở local;
chưa làm được trong phiên này.

---

## Test tất định gọi MẠNG — khiếm khuyết có sẵn, CI chỉ làm nó lộ ra (2026-08-14)

### Triệu chứng

`pytest -m slow` đỏ 6 test. `tests/test_determinism.py` → `HistoryLoader()`
→ `ccxt.ExchangeNotAvailable: 451 Service unavailable from a restricted
location` (Binance chặn IP runner GitHub).

### Vấn đề thật KHÔNG phải "CI không vào được Binance"

Test này tồn tại để khẳng định hai lần chạy giống nhau **bit-for-bit**.
Đầu vào đến từ một API sống thì **tiền đề của chính nó đã sai ngay trên
máy dev**: Binance có thể sửa lại bar lịch sử, cache có thể trống, mạng có
thể chậm. CI chỉ làm điều đó lộ ra ồn ào hơn.

Rà `grep -rn "HistoryLoader|ccxt\.|publicGet|fetch_ohlcv" tests/` tìm
thấy **ba** file gọi mạng thật, không phải một:

| File | Chạy khi | Hậu quả |
|---|---|---|
| `test_determinism.py` | mặc định + slow | cổng chặn của regression harness |
| `test_snapshot.py` | **mỗi commit** | canary phụ thuộc Binance có trả lời không |
| `regression_harness.py` | slow | so với baseline Phase 7 |

`test_snapshot.py` chạy ở bộ MẶC ĐỊNH. Job `fast` của CI không đỏ vì nó
dừng ở bước `mypy` trước khi tới `pytest` — nghĩa là lỗi này còn một lần
nữa đang chờ, và nó chỉ lộ ra sau khi lỗi 1 được sửa.

### Quyết định: MỘT fixture đã commit cho cả ba

`tests/fixtures/btcusdt_1d_2018_2026.parquet` — BTC/USDT 1D, 2018-01-01 →
2026-08-04, **3138 bar, 7 cột, 171 KB**, ghim SHA256 trong
`frozen_hashes.json`, sinh lại bằng `scripts/build_test_fixture.py`.

- **Một** file, không phải ba: ba fixture nghĩa là ba lần phải nhớ sinh
  lại, và lần quên đầu tiên sẽ im lặng.
- Dải lấy theo test cần rộng nhất (`regression_harness`, tới 2026-08-04).
- Bỏ `quote_volume`/`taker_buy_quote_volume` (không hàm nào đọc) đưa
  228 KB → 171 KB, dưới trần 200 KB. Giữ `taker_buy_base_volume` cho
  `taker_buy_ratio` (Tier 2, hiện tắt).
- `bar_offset_hours` mặc định **đã kiểm bằng đo** là trùng
  `bar_offset_hours=0` mà `regression_harness` truyền tường minh — nên một
  fixture phục vụ được cả hai đường gọi.

**Bằng chứng fixture tái tạo đúng dữ liệu mạng:** `regression_harness`
vẫn PASS so với baseline Phase 7 (ngưỡng Sharpe 0.001) sau khi đổi nguồn.
Nếu fixture lệch dù một bar, phép so đó đỏ.

### Thiếu fixture → FAIL, TUYỆT ĐỐI KHÔNG skip

`tests/fixtures/__init__.py::load_fixture()` ném `AssertionError` kèm lệnh
sinh lại. Một test bị skip lặng lẽ chính là cách "878 passed" trở thành
lời nói dối — đúng thứ `ops/verify_scope.py` (`CLAUDE.md` #19) tồn tại để
chặn.

Sau thay đổi này **không còn file test nào gọi mạng**, nên không cần
`@pytest.mark.network` và không có test nào bị loại khỏi CI.


## Mẫu LẶP LẠI: lỗi bị che bởi lỗi đứng trước (2026-08-14)

Không phải một sự cố riêng lẻ. Đây là **lần thứ tư** cùng một mẫu.

### Lần này

`tests/test_snapshot.py` gọi `HistoryLoader()` — tức là qua MẠNG — và nó
chạy ở bộ **mặc định**, mỗi commit. Trên CI, Binance trả 451 cho IP runner
GitHub, nên nó **phải** đỏ.

Nhưng job `fast` báo đỏ ở `mypy`, không ở `pytest`. Lý do: các bước trong
một job GitHub Actions chạy TUẦN TỰ và dừng ở bước đầu tiên thất bại.

```yaml
- name: ruff    -> pass
- name: mypy    -> FAIL, job dừng tại đây
- name: pytest  -> KHÔNG BAO GIỜ CHẠY
```

Đọc log CI thì thấy đúng hai lỗi: `mypy` và `pytest -m slow`. Kết luận tự
nhiên — và SAI — là "có hai vấn đề". Thực tế là **ba**: lỗi thứ ba nằm
im phía sau `mypy`, và nó chỉ lộ ra sau khi lỗi thứ nhất được sửa. Nếu chỉ
sửa `mypy` rồi push, lần chạy kế tiếp lại đỏ ở một chỗ "mới" mà thật ra đã
ở đó suốt.

### Cùng một mẫu với ba lần trước

| Lần | Lỗi che | Lỗi bị che | Hậu quả |
|---|---|---|---|
| — | `cmd \| tail` | mã thoát của `cmd` | `$?` trả mã thoát của `tail`, luôn 0 (`CLAUDE.md` #17) |
| 2026-08-14 | mypy dừng ở lỗi phân giải module | 15 lỗi kiểu trong `tests/` | "mypy sạch" = "mypy chưa kiểm được gì" |
| 2026-08-14 | thiếu `config/__init__.py` | như trên, lần hai | `ops/verify_scope.py` bắt được ngay |
| 2026-08-14 | bước `mypy` của CI | `test_snapshot.py` gọi mạng | "hai job đỏ" thật ra là ba lỗi |

Điểm chung: **một phép kiểm thất bại SỚM làm phép kiểm sau nó không chạy,
và cái không chạy trông giống hệt cái đã qua.** Số lượng lỗi nhìn thấy
được là một cận DƯỚI, không phải tổng.

### Hệ quả cho quy trình

1. **Đừng đếm lỗi từ log CI.** Sửa lỗi đầu, chạy lại, đếm lại. Số ban đầu
   luôn là cận dưới.
2. Bước **"PHẠM VI ĐÃ KIỂM"** (`CLAUDE.md` #19) trong `ci.yml` được đặt ở
   **CUỐI** job `fast` một cách có chủ ý: nếu bất kỳ bước nào trước nó
   thất bại, phạm vi KHÔNG được in ra — và một báo cáo phạm vi vắng mặt là
   tín hiệu "chưa kiểm hết", đọc được ngay từ danh sách bước.
3. Không dùng `continue-on-error` để "thấy hết lỗi một lượt". Nó biến một
   job đỏ thành một job xanh-có-chú-thích, và chú thích thì không ai đọc.
   Đổi lại, chấp nhận sửa theo vòng: mỗi vòng lộ ra lớp tiếp theo.

Ba lỗi lần này được sửa TRONG CÙNG MỘT commit chính vì đã lường trước lớp
thứ ba, không phải vì log CI đã nói ra nó.


## Bộ test đọc `.env` THẬT — và ĐÃ gửi POST ra api.telegram.org (2026-08-14)

### Triệu chứng

Hai test trong `tests/test_monitoring_alerts.py` chuyển từ xanh sang đỏ
**mà không dòng code nào đổi**. Thứ đã đổi là `.env`:
`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` được điền giá trị thật (mục treo
#0 của `STATE.md`). Xác minh không phải do Phase 12c: `git stash` rồi chạy
bộ đầy đủ ở HEAD sạch — vẫn 2 đỏ.

### Nguyên nhân

`monitoring/forward_watchdog.py::run_watchdog()` gọi `load_dotenv()` cứng,
đường dẫn MẶC ĐỊNH = `.env` thật, và hàm đó ghi thẳng vào `os.environ`.
`monkeypatch` không hoàn tác được thứ nó không đặt, nên biến rò rỉ sang
mọi test chạy sau trong cùng phiên.

### KHẲNG ĐỊNH BAN ĐẦU LÀ SUY LUẬN — đây là phép ĐO

Bản ghi đầu của mục này viết *"một test không patch `requests.post` sẽ gửi
tin nhắn Telegram THẬT"*. Đó là **suy luận**. Đo lại
(`grep -rln "AlertManager|send_alert" tests/` rồi đọc từng chỗ):

| File | Dựng gì | Có chặn mạng? |
|---|---|---|
| `test_main_loop.py` | `_SpyAlertManager(AlertManager)`, **~20 chỗ** | `send()` gọi `super().send()` — **KHÔNG chặn** |
| `test_alert_channel_health.py:283` | `AlertManager` thật rồi `.send()` | **KHÔNG chặn** |
| `test_exception_classification.py` | `_SpyAlertManager` (import từ trên) | **KHÔNG chặn** |
| `test_monitoring_alerts.py` | `AlertManager` thật | có `patch("...requests.post")` |
| `test_daily_digest.py`, `test_watchdog.py`, `test_data_harness.py`, `test_health.py` | `_FakeAlertManager` thuần | không chạm mạng |

`_SpyAlertManager` tắt console (`console_enabled=False`) nhưng Telegram
đọc từ `os.environ` — nên nó đi trọn đường gửi thật. Chạy thử với token
giả lập và một `requests.post` đếm được:

```
số lời gọi requests.post: 1
  -> https://api.telegram.org/bot<TOKEN>/...
```

**Kết luận đúng, và nặng hơn bản suy luận:** không phải "một test *sẽ*"
gửi — mà **~20 chỗ gọi ĐÃ POST thật tới `api.telegram.org` ở MỖI lần chạy
bộ test**, kể từ ngày `.env` có token cho tới bản sửa này.
`test_spy_alert_manager_di_qua_duong_gui_that` giữ phép đo đó.

### Vì sao nặng hơn một test đỏ

1. Bộ test cho **kết quả khác nhau trên hai máy** tuỳ máy đó có credential
   hay không — đúng lớp "lỗi xác minh" mà `CLAUDE.md` #16 gọi là chế độ
   hỏng chủ đạo của dự án.
2. Bộ test **gửi ra ngoài thật**, không phải "có nguy cơ gửi".
3. Ẩn được nhiều tháng vì tới hôm đó `.env` toàn giá trị rỗng.

### Sửa — HAI lớp, lớp đầu mới là lớp thật

**Lớp 1 — vá ĐƯỜNG RÒ RỈ.** `tests/conftest.py` thay
`monitoring.forward_watchdog.load_dotenv` bằng bản NÉM LỖI khi gọi với
đường dẫn mặc định. Đóng cho **MỌI biến**, có tên hay chưa — mạnh hơn hẳn
một danh sách. Gọi với đường dẫn tường minh vẫn chạy (đó là cách
`test_load_dotenv_*` kiểm chính hàm đó). Dưới launchd `load_dotenv()` hoạt
động đầy đủ: tiến trình khác, không có conftest.

Chặn này lập tức lộ ra **4 test nữa** cũng đang đọc `.env` thật qua
`run_watchdog`. Sửa bằng cách thêm tham số `env_path` cho `run_watchdog`
(mặc định `None` = `.env` thật, chỉ test truyền đường dẫn tạm) — một phụ
thuộc ngầm vào trạng thái máy phải làm cho TƯỜNG MINH, không phải để test
đi vòng.

**Lớp 2 — danh sách credential**, chỉ còn nhiệm vụ chặn biến đã `export`
sẵn trong shell (đường lớp 1 không với tới).

### Danh sách 12 tên là PHÁN ĐOÁN, không phải phép đo

Đo lại: code không phải test đọc **19** biến môi trường, không phải 12.
Bảy biến ngoài danh sách là đường dẫn/tham số vận hành (`CONFIG_PATH`,
`LOG_DIR`, `MODEL_PATH`, `REQUIRE_HMM_MODEL`, `STATE_DIR`,
`WATCHDOG_POLL_SEC`, `WATCHDOG_STALE_SEC`).

`tests/test_env_isolation.py::test_khong_bien_moi_truong_nao_bi_bo_quen`
đối chiếu mã nguồn với HAI danh sách (`CREDENTIAL_ENV`, `NON_SECRET_ENV`)
mỗi lần chạy: biến thứ 20 xuất hiện buộc một quyết định — bí mật hay
không — và không thể im lặng ở lại ngoài cả hai. Cộng phép kiểm chiều
ngược (tên trong danh sách mà code không còn đọc) và phép kiểm hai danh
sách không giao nhau.

### Điều KHÔNG sửa, và vì sao

`load_dotenv()` giữ nguyên hành vi production: nó cần đọc `.env` thật khi
chạy dưới launchd (launchd không có env của shell). Vấn đề không nằm ở nó
mà ở việc **bộ test chạy chung không gian tiến trình** — nên cách ly thuộc
về `conftest.py`, không thuộc về module đang làm đúng việc của mình.


## Điều kiện thời điểm deploy — ngưỡng biến động p80 đã đo (2026-08-14)

Phase 12c §E. Quy trình CLAUDE.md #18.

**Đại lượng:** `|log return|` 24 giờ, đơn vị %. **So cùng kích thước cửa
sổ** (bước 1 của #18, bước hay bị bỏ nhất): giá trị hiện tại là
`|log return|` của MỘT bar, và phân phối nền cũng là `|log return|` của
TỪNG bar — không phải độ lệch chuẩn toàn kỳ.

**Nguồn:** `tests/fixtures/btcusdt_1d_2018_2026.parquet`, 3137 bar
(2018-01-02 → 2026-08-04).

| p50 | p70 | p75 | p80 | p85 | p90 | p95 | p99 |
|---|---|---|---|---|---|---|---|
| 1.457 | 2.548 | 2.999 | **3.561** | 4.219 | 5.285 | 7.197 | 11.402 |

trung bình 2.257, độ lệch chuẩn 2.575, lớn nhất **50.26**.

**Tỷ lệ ngày bị CHẶN deploy đo được: 20.0% (628/3137).** Ở đây con số bằng
đúng định nghĩa phân vị — p80 chặn 20% theo cấu trúc — nhưng vẫn phải ĐO
chứ không suy: `percentile_of()` dùng "nhỏ hơn nghiêm ngặt", và giá trị
lặp có thể làm lệch. Có test ghim cả ngưỡng lẫn tỷ lệ.

Ý nghĩa vận hành: trung bình **chờ 5 ngày** là có một ngày deploy được.

**Đánh đổi nếu muốn đổi ngưỡng:** p70 chặn 30%, p90 chặn 10%. Giữ p80
theo §E; nới lên p90 nghĩa là chấp nhận deploy vào những ngày biến động
gấp ~1.5 lần.

**Vì sao PHÂN VỊ chứ không phải con số tuyệt đối:** ngưỡng tuyệt đối sai
dần khi chế độ biến động của thị trường đổi (BTC 2018 và BTC 2026 không
cùng biên độ), còn phân vị tự hiệu chỉnh theo lịch sử.

### Trạng thái thứ ba: `ok = None`

`Condition.ok` có BA giá trị, không hai. `None` = **không xác định được**
(không hỏi được sàn, chưa đủ lịch sử, `risk_manager` không lộ đường dẫn
halt lock). Trộn nó vào:

- `True` -> cổng RỖNG: một sự cố mạng biến "chưa kiểm được" thành "đạt".
- `False` -> một sự cố đo lường chặn deploy vĩnh viễn.

`DeployReadiness.ok` chỉ `True` khi MỌI điều kiện `is True`. Đột biến đổi
`is True` thành `is not False` bị bắt.

### §E.3 không đo được, và không giả vờ đo được

"Bạn có mặt được ít nhất 2 giờ tới không?" là điều kiện THẬT đằng sau luật
"không deploy tối Thứ Sáu" — luật của thị trường có giờ đóng cửa, tồn tại
vì cuối tuần không ai trực. `deploy_conditions.py` in câu hỏi đó ở MỌI lần
chạy, kể cả khi mọi điều kiện khác đều ĐẠT: đó là lúc dễ bỏ qua nhất.


## SỬA ATTRIBUTION: testnet bị chặn ở tầng BINANCE, không phải GitHub (2026-08-14)

`docs/STATE.md` và mục Phase 10 của file này từng ghi *"testnet chặn ở
tầng tài khoản GitHub"*. **Sai.**

| | Thực tế |
|---|---|
| **Chặn testnet** | Tầng tài khoản **Binance** — lỗi `-2015` trên endpoint cần API key. `exchange_reachable` (endpoint công khai) vẫn OK 155–178ms. |
| **Chặn GitHub** | GitHub chặn OAuth sang `testnet.binance.vision`. **Chuyện KHÁC**, đã xử lý xong bằng tài khoản `peach3tiger`. |

Hai sự cố khác nhau, ở hai lớp khác nhau, xảy ra gần nhau về thời gian và
bị gộp làm một trong ghi chép.

**Vì sao đáng sửa dù cả hai đều "đang bị chặn":** ghi sai nguyên nhân thì
ba tháng nữa có người đi sửa nhầm lớp — kiểm tra quyền GitHub, đổi token
OAuth, xem lại workflow — trong khi thứ cần làm là xử lý tài khoản
Binance. Đây đúng là điều `docs/STATE.md` đã tự dặn ("xác định ĐÚNG lớp bị
chặn trước khi debug"), và bản ghi chép vi phạm chính lời dặn đó.

Commit `f838cc0` ("docs/STATE.md: ghi nhận testnet bị chặn ở tầng tài
khoản GitHub") mang tên sai. Không viết lại lịch sử đã push; ghi ở đây để
ai đọc log không bị dẫn sai.


## Chuỗi chặn deploy liên tiếp — đo HỆ QUẢ VẬN HÀNH, không chỉ phân phối (2026-08-14)

"20% số ngày bị chặn" chưa trả lời được câu hỏi thật: **cổng này có sống
được không?** Biến động có tính CỤM, nên 627 ngày bị chặn đến thành chuỗi
chứ không rải đều. Một cổng chặn 30 ngày liền sẽ bị bỏ qua ngay lần đầu nó
chặn một bản vá cần gấp — và cổng đã bị bỏ qua một lần thì không còn là
cổng.

Nhóm ngày bị chặn (`|log return| > p80 = 3.561%`) thành chuỗi liên tiếp
trên 3137 bar:

| | |
|---|---|
| số chuỗi | 456 |
| dài nhất | **6 ngày** |
| p95 | 3 ngày |
| trung vị | 1 ngày |
| trung bình | 1.38 ngày |

Phân bố độ dài: 1 ngày × 335, 2 × 90, 3 × 22, 4 × 3, 5 × 2, 6 × 4.

**Ba chuỗi dài nhất, đều 6 ngày:** 2021-01-10, 2020-03-12, 2019-06-25 —
tức là các đợt sập lớn đã biết. Cổng chặn ĐÚNG LÚC chứ không chặn ngẫu
nhiên.

### Kết luận: KHÔNG cần lối thoát ghi đè

Ngưỡng cân nhắc là **14 ngày**. Chuỗi dài nhất đo được là 6, nên chờ tối
đa một tuần — chấp nhận được cho một bản vá không phải sự cố đang cháy, và
sự cố đang cháy thì dùng `scripts/emergency_kill.py` chứ không deploy.

`tests/test_deploy_conditions.py::test_chuoi_chan_lien_tiep_dai_nhat_la_6_ngay`
ghim con số này VÀ ghim luôn quyết định: nếu fixture đổi và chuỗi dài nhất
vượt 14, test đỏ kèm thông điệp yêu cầu dựng lối thoát có kiểm soát —
người vận hành sẽ tự chế ra một cái lúc 2 giờ sáng nếu không có.


## Commit đột biến lên `origin/main` — nguyên nhân thật + lỗ hổng nó lộ ra (2026-08-14)

### Nguyên nhân: push thủ công của người dùng, KHÔNG phải "không xác định được"

Bản ghi đầu của tôi trong commit `c7dbfca` viết *"tôi KHÔNG xác định được
đường nào đã đẩy nó lên"*. Đã tìm ra. GitHub Activity:

    "TẠM — đột biến nghiệm thu #2 — peach3tiger pushed 3 commits to main
     a488ccd…c7fb25b"

**Ba commit trong MỘT lần push, kết thúc ở `c7fb25b`.** Đó là lệnh
`git push origin main` người dùng chạy thủ công trong Terminal, rơi đúng
vào khoảng giữa chu trình đột biến — commit tạm đã tồn tại, `git reset
--hard` chưa kịp chạy. Reflog của tôi cho thấy khoảng đó dài đúng **một
phút** (commit 23:22:40, reset 23:23:55).

Đây là **lỗi ĐIỀU PHỐI giữa hai tác nhân trên cùng một repo**, không phải
lỗi của chu trình đột biến. Chu trình làm đúng mọi bước nó được thiết kế
để làm: commit trước, chạy, reset, `grep MUTANT` xác nhận sạch. Không bước
nào trong đó biết được có người khác đang gõ `git push` ở cửa sổ bên cạnh.

### Lỗ hổng THẬT mà sự cố này lộ ra

Câu hỏi tiếp theo là đúng câu cần hỏi: **CI có bắt được không?** Đo bằng
cách chạy bộ test tại chính `c7fb25b`:

| Phép kiểm | Chạy ở | Kết quả tại `_EMA_PERIOD = 40` |
|---|---|---|
| `test_forward_golden` | mặc định, <1s | **ĐỎ** |
| `test_snapshot` | mặc định, ~11s | xanh — không bắt (đã biết từ 12b) |
| `test_properties` | mặc định | xanh |
| `test_wiring_equivalence` | mặc định | xanh |
| `regression_harness` (gọi TƯỜNG MINH) | — | **ĐỎ** |
| **`pytest -m slow`** | cổng §E | **XANH — 8 passed** |

Dòng cuối là lỗ hổng. `pytest -m slow` chạy nhanh hơn (67s) so với gọi
riêng harness (125s), và đó là dấu hiệu: **harness không hề chạy.**

**`tests/regression_harness.py` CHƯA TỪNG được thu thập lần nào** kể từ khi
được viết ở Phase 12b. Pattern mặc định của pytest là `python_files =
test_*.py`; tên file không khớp.

Hệ quả không phải "thiếu một test":

- Cổng §E (`ops/readiness_gate.py`) **bắt buộc** chạy `pytest -m slow`
  trước khi merge thay đổi chạm `core/`. Lệnh đó thu 8 test và **không có**
  test hồi quy mạnh nhất. Cổng đã thi hành một lệnh không chứa thứ nó sinh
  ra để bảo vệ.
- CI job `slow-gate` cũng chạy `pytest -m slow -q` — cùng lỗ.
- Mọi lần tôi "xác minh harness bắt được `_EMA_PERIOD`" đều gọi TƯỜNG MINH
  `pytest tests/regression_harness.py`, và cách gọi đó bỏ qua pattern. Phép
  xác minh đúng, nhưng nó xác minh một đường mà cổng không đi.

Đây đúng chế độ hỏng `CLAUDE.md` #19: một công cụ báo "sạch" mà không nói
nó đã nhìn vào đâu.

### Sửa

`pyproject.toml`: `python_files = ["test_*.py", "regression_harness.py"]`.
Thêm pattern thay vì đổi tên file — tên `regression_harness.py` được nhắc
ở `docs/`, `ops/verify_scope.py` và nhiều chỗ; đổi tên là sửa một tá chỗ
để chiều một pattern.

Đo lại tại `c7fb25b` SAU khi sửa: `pytest -m slow` → **1 failed, 8 passed**
(`regression_harness::test_regression_vs_phase7_baseline`). Cổng §E giờ
thật sự bắt được.

### Lỗ hổng đó đã che một BUG THẬT tôi gây ra

Ngay khi harness chạy lần đầu, nó ĐỎ ở HEAD sạch:

    sharpe        0.9411 -> 1.0776   (trần 0.001)
    n_trades      739    -> 822      (trần 1%)
    total_fee     4445   -> 3784     (trần 0.5%)
    bar đầu lệch: 2020-04-28, tức bar THỨ HAI

Lệch từ bar thứ hai nghĩa là **đầu vào** khác, không phải logic. Nguyên
nhân: khi chuyển harness sang fixture (commit `35eb833`), tôi thay
`HistoryLoader().load(_CCXT_SYMBOL, "1D", _START, _END, bar_offset_hours=0)`
bằng `load_fixture(_END)` — **mất mốc `_START = 2018-02-09`**. Fixture bắt
đầu từ 2018-01-01, nên harness nhận thêm 39 bar warmup; z-score đổi, HMM
đổi, mọi chỉ số đổi theo.

**Và câu tôi viết trong commit đó — "regression_harness vẫn PASS so với
baseline Phase 7 sau khi đổi nguồn" — là SAI.** Nó dựa trên một lần chạy
`pytest -m slow` không hề chứa harness. Tôi đã báo cáo một bằng chứng
không tồn tại.

Sửa: `load_fixture(end, *, start=None)` và harness gọi
`load_fixture(_END, start=_START)`. Sau đó harness **PASS** (82s) — nên
fixture THẬT SỰ tái tạo đúng dữ liệu mạng; chỉ phần nối dây của tôi sai.

`test_determinism` và `test_snapshot` không bị: cả hai vốn dùng
`_DATA_START = 2018-01-01`, trùng mốc bắt đầu của fixture.

`tests/test_collection_scope.py` (3 test) ghim để lỗ này không mở lại:
harness phải có trong `-m slow`; mọi file `.py` trong `tests/` phải góp ít
nhất một test; và `slow ∪ not-slow == tất cả` — một chênh lệch ở đó nghĩa
là có test rơi ra ngoài CẢ HAI nhóm, tức không bao giờ chạy ở lệnh nào.

### Một phát hiện phụ: `test_watchdog` FLAKE

Lần chạy đầy đủ tại `c7fb25b` còn đỏ
`test_watchdog::test_bot_treo_that_bi_phat_hien_va_bi_KILL`. Chạy riêng 3
lần tại `c7fb25b` **và** 3 lần tại HEAD sạch: xanh 6/6. Nó là test dựng
tiến trình con thật + `SIGSTOP` + ngưỡng thời gian 1.0s — nhạy với tải máy
khi chạy cùng ~1000 test khác. KHÔNG do đột biến. Chưa sửa; ghi lại vì một
test lúc xanh lúc đỏ dạy người đọc bỏ qua màu đỏ.


## CI xác nhận trên GitHub Actions: bộ test KHÔNG mù trước đột biến (2026-08-15)

Kiểm chứng độc lập cho sự cố `c7fb25b`, đọc từ GitHub Actions chứ không
suy từ máy dev.

| Lần chạy | Commit | Kết quả |
|---|---|---|
| CI **#8** | `c7fb25b` (có `_EMA_PERIOD = 40`) | **ĐỎ** ở CẢ HAI job: "Bộ mặc định + lint" và "Cổng §E — test chậm khi chạm tầng quyết định" |
| CI **#9** | `a378168` (sau revert) | **XANH** — main sạch, xác nhận độc lập |

Trong toàn bộ lịch sử repo chỉ có **hai** lần chạy đỏ: CI #1 và CI #8.

### 1. Bộ test KHÔNG mù — và `compare_versions` vẫn không thừa

CI #8 đỏ ở bước `pytest -m 'not slow'`. Khớp với phép đo tại máy: bộ mặc
định bắt được đột biến qua `tests/test_forward_golden.py` (<1s).

**Điều này trả lời một câu hỏi thật: `ops/compare_versions.py` có thừa
không?** Không thừa — nhưng lý do KHÔNG phải "vì nó là thứ duy nhất bắt
được". Hai công cụ trả lời hai câu khác nhau:

| | `pytest` | `ops/compare_versions.py` |
|---|---|---|
| Trả lời | "có gì đó vỡ" | "vỡ Ở ĐÂU" |
| Đầu ra | một dòng đỏ | bar ĐẦU TIÊN lệch + bốn trường quyết định + 10 bar bối cảnh |
| Ví dụ thật | `test_forward_golden` FAILED | `2024-06-11`, `hmm_allocation` 0.948956 vs 0.6, `regime_id` không đổi, `trend_gate_cap` không đổi |

Dòng cuối là giá trị thật của `compare_versions`: nó chỉ ra `regime_id` và
`trend_gate_cap` KHÔNG đổi ở bar lệch, nên nguyên nhân nằm ở tầng
strategy — không phải HMM, không phải trend gate. `pytest` báo đỏ và người
đọc phải tự tìm.

Ghi lại vì bản ghi trước của tôi ngụ ý bộ test mù trước đột biến này. Nó
không mù; chỉ có `test_snapshot` (~11s canary) là không bắt được, và điều
đó đã đo và ghi từ Phase 12b.

### 2. Bước "PHẠM VI ĐÃ KIỂM" bị bỏ qua — ĐÚNG THIẾT KẾ, lần đầu chạy thật

Bước đó nằm CUỐI job "Bộ mặc định + lint", sau `pytest`. CI #8 đỏ ở
`pytest` nên job dừng và bước phạm vi **không chạy**.

Đó là lựa chọn có chủ ý khi viết `ci.yml`: đặt ở cuối để **một báo cáo
phạm vi VẮNG MẶT chính là tín hiệu "chưa kiểm hết"**, đọc được ngay từ
danh sách bước mà không cần mở log. Đây là lần đầu cơ chế đó hoạt động
trong tình huống thật, và nó hoạt động đúng như mô tả.

Hệ quả cần nhớ: **không dùng `continue-on-error`** để "thấy hết lỗi một
lượt". Nó biến một job đỏ thành job xanh-có-chú-thích, và chú thích thì
không ai đọc. Xem mục "Mẫu LẶP LẠI: lỗi bị che bởi lỗi đứng trước".

### 3. Cổng §E đỏ ở commit chạm tầng quyết định — CHƯA XÁC ĐỊNH ĐƯỢC bước nào

`c7fb25b` sửa `core/regime_strategies.py`, nên job "Cổng §E" chạy đúng vai
và nó ĐỎ. Nhưng **tôi chưa xác định được nó đỏ ở BƯỚC nào**, và hai khả
năng có ý nghĩa rất khác nhau:

- **Bước `pytest -m slow`** — nhưng tại `c7fb25b`, `regression_harness`
  chưa được thu thập (xem mục trên), nên bộ slow chỉ có 8 test và đo tại
  máy cho 8 passed. Nếu bước này đỏ trên CI thì có một test slow hỏng
  RIÊNG trên CI (ứng viên: `test_dau_cuoi_HEAD_so_voi_chinh_no`, vốn cần
  `git worktree`).
- **Bước `Cổng §E`** (`ops/readiness_gate.py`) — biên lai `.slow_receipt.json`
  do bước `pytest -m slow` ngay trước đó sinh ra trong CÙNG job, cùng
  commit, nên băm phải khớp và cổng phải PASS. Nếu bước này đỏ thì cơ chế
  biên lai KHÔNG hoạt động trên runner sạch — một lỗ hổng khác hẳn.

**Chưa kiểm được vì máy này không có `gh` và tôi không đọc được log CI.**
Ghi ra thay vì đoán. Việc cần làm: mở log CI #8, xem bước nào đỏ trong job
"Cổng §E", rồi ghi tiếp vào mục này.


## CI matrix 3.9 + 3.11 — đóng khoảng trống "interpreter thật chưa được kiểm" (2026-08-15)

### Vấn đề

`docs/STATE.md` đã ghi từ 2026-08-14: cả hai launchd job của forward test
gọi `/Users/lbeyewear/regime-trader-crypto/.venv/bin/python` = **Python
3.9.6**, trong khi `pyproject.toml` khai `python_version = "3.11"`, `ruff`
dùng `target-version = py311`, `CLAUDE.md` §Phong cách nói "Python 3.11+",
và CI chỉ kiểm 3.11.

**Đường code đang sinh ra dữ liệu của thí nghiệm 12 tháng chưa từng được
CI kiểm lần nào.** Mọi khẳng định "CI xanh" đều nói về một interpreter
khác với interpreter thật.

### KHÔNG nâng `.venv`

Nâng venv lên 3.11 giữa chừng sẽ cài lại `numpy`/`hmmlearn` ở phiên bản
khác — đủ để đổi kết quả EM và do đó đổi regime, allocation, mọi thứ. Đó
là **đổi điều kiện của chính thí nghiệm đang chạy**, cùng loại vi phạm với
sửa `forward/config_frozen.yaml`.

Cách đúng: CI kiểm **cả hai**.

### Đo trước khi quyết định `mypy python_version`

| | Kết quả |
|---|---|
| `mypy .` (python_version = 3.11) | Success, 108 file |
| `mypy --python-version 3.9 .` | Success, 108 file |
| `ruff check . --target-version py39` | All checks passed |

**Cả ba đều sạch.** Nên matrix là phòng xa, không phải vá một lỗi đang có.

**Quyết định: giữ `python_version = "3.11"` trong `pyproject.toml`, và CI
truyền `--python-version` theo matrix.**

Hạ `pyproject` xuống 3.9 bị loại vì nó **hợp thức hoá cái venv 3.9 thành
mục tiêu** thay vì giữ nó là một khoảng trống cần đóng. Mục tiêu khai báo
của dự án vẫn là 3.11+; sự thật hôm nay là forward test chạy 3.9; hai điều
đó cùng đúng và tài liệu phải phản ánh cả hai, không phải làm phẳng một
cái cho tiện.

### `fail-fast: false`

Nếu 3.9 đỏ và 3.11 xanh, ta cần THẤY cả hai. Dừng sớm che mất thông tin
phân biệt "lỗi của phiên bản" với "lỗi của code".

### Bước "PHẠM VI ĐÃ KIỂM" in interpreter

`CLAUDE.md` #19 — hai job cùng tên nhau sẽ không phân biệt được trong log
nếu không in ra chúng chạy Python nào. Tên job cũng mang `py${{ matrix.python-version }}`.

### Nếu job 3.9 đỏ

Đó là **lỗi thật đã tồn tại từ lâu**, không phải lỗi mới do matrix tạo ra
— và nó nằm trên đúng interpreter đang chạy thí nghiệm. Sửa thật, KHÔNG
loại 3.9 khỏi matrix cho xanh.


## Chuỗi kích hoạt liên tiếp của `WARNING_TREND_LEN` và `WINDOW_DAYS` (2026-08-15)

Bù nốt bước còn thiếu của `CLAUDE.md` #18: ba ngưỡng đã đo TỶ LỆ ở ĐO
#1/#2/#3 (2026-08-14), nhưng **chưa đo HỆ QUẢ VẬN HÀNH** — tỷ lệ thấp mà
gom cụm vẫn làm người vận hành tắt cảnh báo.

### `WARNING_TREND_LEN`

**Nguồn: mô phỏng giả thuyết không**, không phải `forward/log_v2.csv`.
Log forward hiện có 9 bar / 2 lần retrain — không đủ dựng phân phối, và
dùng nó sẽ cho một con số trông có thẩm quyền mà không có. Mô phỏng 20 000
chuỗi × 60 lần retrain (~14 tháng):

| L | Kích hoạt / lần kiểm | Chuỗi dài nhất (TB) | p95 | Tuyệt đối |
|---|---|---|---|---|
| 3 | 16.70% | 2.29 | 4 | 7 |
| **4 (đang dùng)** | **4.14%** | **1.28** | **3** | 7 |
| 5 | 0.84% | 0.41 | 2 | 5 |

Retrain 7 ngày/lần, nên p95 = 3 nghĩa là **tối đa ~3 tuần** cảnh báo lặp
trong trường hợp xấu điển hình. Chấp nhận được. Ở L=3 con số là 4 tuần với
tần suất kích hoạt gấp 4 lần — thêm một lý do nữa cho việc đã đổi sang 4.

### `WINDOW_DAYS` — tỷ lệ thấp nhưng GOM CỤM MẠNH

Trượt cửa sổ qua baseline Phase 7, đếm cửa sổ có ít nhất một cảnh báo
drift (ngưỡng §C.1 + dải p1–p99):

| W | Kích hoạt | Số chuỗi | Chuỗi dài nhất | p95 |
|---|---|---|---|---|
| **30 (đang dùng)** | **1.02%** | **2** | **20** | 19 |
| 60 | 2.37% | 6 | 17 | 17 |
| 90 | 2.45% | 5 | 19 | 19 |

**1.02% nghe vô hại; "2 chuỗi, dài nhất 20 cửa sổ" thì không.** Với bar
1D, 20 cửa sổ liên tiếp = **20 ngày cảnh báo drift liền**.

**Vẫn giữ W = 30. Ba lý do, theo thứ tự quan trọng:**

1. **Drift là cảnh báo QUAN SÁT, không phải cổng CHẶN.** Khác §E.1 — nơi
   một chuỗi dài chặn deploy và sẽ bị người ta đi vòng. Ở đây chuỗi dài
   nghĩa là hành vi thật sự đã lệch suốt 20 ngày, và đó chính là thứ cần
   biết.
2. `AlertManager` đã rate-limit theo loại sự kiện
   (`alert_rate_limit_seconds` = 900), nên 20 ngày KHÔNG thành 20 lần báo
   động dồn dập.
3. Kéo W lên 60/90 làm tỷ lệ kích hoạt TĂNG (1.02% → 2.37%) mà chuỗi dài
   nhất gần như không đổi (20 → 17 → 19). Không mua được gì.

**Điều PHẢI ghi lại để không đọc nhầm:** một đợt drift kéo 20 ngày là
**MỘT sự kiện**, không phải 20 sự kiện. Đọc `drift.json` đỏ ba tuần liền
mà tưởng là ba tuần sự cố riêng biệt sẽ dẫn tới kết luận sai về tần suất.

### Kết quả: bảng phân loại còn 0 ngưỡng "chưa đo"

`CLAUDE.md` #18 cập nhật thành bốn nhóm, mỗi ngưỡng thuộc đúng một nhóm:
6 đã đo bằng phân phối, 2 có căn cứ từ ràng buộc sàn, 1 hoãn có lý do và
điều kiện gỡ, 2 là lựa chọn vận hành không có sự thật nền.

`unfilled_order_degraded_seconds` là mục HOÃN chứ không phải "chưa đo":
backtest không mô phỏng độ trễ khớp nên không tồn tại phân phối để trượt
cửa sổ. Gỡ khi testnet tích đủ vài chục lệnh thật.

## Phân kỳ EM trong backtest kiểm định — đo, không suy (2026-08-15)

### Vì sao đo

`tests/regression_harness.py` đỏ trên `ubuntu-latest` trong khi xanh trên
macOS. Log CI kèm hàng chục `hmmlearn: Model is not converging` với
`delta = -161`. Delta âm 161 không phải nhiễu làm tròn — EM có tính chất
log-likelihood **đơn điệu tăng**, nên một lần giảm 161 là phân kỳ thật.

Câu hỏi thật không phải "CI có xanh không" mà: **nếu BIC đang chọn model
giữa các lần fit phân kỳ như vậy, lựa chọn đó do nhiễu quyết định**, và
mọi kết luận Phase 7 đứng trên nó.

### Cách đo

Bọc `GaussianHMM.fit` và `HMMRegimeEngine.scan_bic`, chạy đúng backtest
ghim của harness (pruned-8, 2018-02-09 → 2026-08-04, 13 cửa sổ
walk-forward). Bắt cảnh báo qua `logging` handler trên logger `hmmlearn`.

**Hai sai lầm đo lường đã mắc và sửa** — ghi lại vì cả hai đều cho ra một
con số trông hợp lý:

1. Bản đầu đọc `monitor_.converged`. Vô dụng: hmmlearn trả `True` khi
   `iter == n_iter`, tức **chạm trần lặp cũng được tính là hội tụ**. Nó
   báo `0/650 không hội tụ` trong khi thực tế 68 lần phát cảnh báo.
2. Bản hai khớp model được chọn bằng `id(best_model)`. `id()` được **tái
   sử dụng** sau khi các candidate thua bị GC, nên nó khớp nhầm bản ghi —
   báo `n_components` được chọn là 3,5,3,6,… trong khi bảng BIC nói
   5,6,4,6,…. Sửa bằng nhãn gắn trên chính đối tượng.

Không phát hiện được (2) nếu không đối chiếu chéo với bảng BIC. Một con
số đơn lẻ không tự tố cáo mình sai.

### Kết quả

| | |
|---|---|
| tổng số lần `.fit()` | 650 |
| phát ít nhất một `not converging` | **68 (10.5%)** |
| có cảnh báo với `\|delta\| > 1` | **27 (4.2%)** |
| chạm trần `n_iter` | 2 (0.3%) |
| `\|delta\|` lớn nhất | **128.8** |
| `\|delta\|` trung vị trong nhóm > 1 | 78.9 |
| `overflow`/`divide by zero`/`invalid` trong `matmul` | **62.534 lần** |

Phân kỳ tăng theo số trạng thái — đúng như kỳ vọng với
`covariance_type="full"` (số tham số tăng bậc hai):

| `n_components` | fit phân kỳ `\|delta\|>1` |
|---|---|
| 3 | 0/130 (0.0%) |
| 4 | 2/130 (1.5%) |
| 5 | 5/130 (3.8%) |
| 6 | 7/130 (5.4%) |
| 7 | 13/130 (10.0%) |

### Kết luận cho Phase 7 (CHỈ với pruned-8): lựa chọn không do nhiễu quyết định

**0/13 cửa sổ chọn phải một model phân kỳ.** Ở cả 13 cửa sổ, model được
BIC chọn có `0` cảnh báo `|delta|>1` và không chạm trần lặp — dù 10/13
cửa sổ CÓ chứa ít nhất một restart phân kỳ trong số 50 lần thử.

> **SỬA PHẠM VI 2026-08-16.** Câu trên đo trên **pruned-8** và được viết
> ra KHÔNG kèm phạm vi. Với bộ feature đầy đủ (14) thì nó **SAI**: BIC
> chọn phải model phân kỳ với `n_components=7`, `|delta|` 271.5. Xem mục
> "Hợp đồng EM nổ trên cấu hình mặc định" bên dưới.

Cơ chế bảo vệ là vòng random restart: `scan_bic` giữ restart có
log-likelihood **cao nhất**, và một fit đã phân kỳ thì log-likelihood
tệ, nên nó luôn thua. Cơ chế này chưa từng được viết ra như một phép
phòng thủ có chủ ý — nó là hệ quả phụ của `n_init`, và giờ được ghi lại
kèm số đo.

**Biên BIC giữa lựa chọn thắng và á quân** (thứ quyết định lựa chọn có
mong manh không):

| | |
|---|---|
| biên nhỏ nhất | **3.0** (0.04% của `\|BIC\|`) |
| biên trung vị | 47.5 (0.66%) |

Một cửa sổ có biên 3.0 điểm BIC. Đó là cửa sổ dễ lật nhất dưới một thay
đổi số học nhỏ, và là ứng viên hàng đầu cho chỗ đường equity bắt đầu lệch
giữa hai máy.

### Điều này KHÔNG chứng minh

Không chứng minh kết quả Phase 7 đúng. Nó chỉ loại một chế độ hỏng cụ
thể: "BIC chọn phải model rác". Vấn đề `covariance_type="full"` sinh
62.534 lần tràn số vẫn còn nguyên, và 10.5% số fit đi qua vùng số học
không tin cậy được là một khoản nợ đã lượng hoá chứ không phải đã trả.

Chi tiết từng lần fit: `reports/do_hoi_tu_em.json`.

## Tất định NỘI MÁY vs LIÊN MÁY — lần thứ tư (2026-08-15)

`tests/regression_harness.py` xanh trên macOS, đỏ trên `ubuntu-latest`.
`max_drawdown_pct` khớp 9 chữ số; đường equity lệch từ một bar ở giữa.

**Đây là lần thứ TƯ một khẳng định "sạch" hoá ra hẹp hơn nó tự nhận**,
sau: `regression_harness` chưa từng được thu thập; cổng §E chưa từng kích
hoạt; thí nghiệm kiểm chứng cổng §E chưa từng chạy. Ba lần trước là phạm
vi CHẠY. Lần này là phạm vi MÔI TRƯỜNG — mọi con số "bit-for-bit" và
ngưỡng 0.001 của dự án này chỉ có nghĩa trên *cùng máy, cùng BLAS, cùng
số thread*, và điều đó chưa bao giờ được viết ra.

`ops/kiem_tat_dinh.py` cơ giới hoá cả hai phần: `--runs 0` in dấu vân tay
môi trường, `--runs N` chạy backtest ghim N lần trong CÙNG tiến trình và
so hash `repr()` từng float (không làm tròn, không `allclose` — câu hỏi
là "cùng bit không", khác hẳn câu hỏi "chiến lược có trôi không").

**Thứ tự bắt buộc:** loại bất định NỘI MÁY trước. Nếu cùng máy chạy hai
lần đã khác nhau thì mọi so sánh liên máy đều vô nghĩa.

### Đo được ở local (macOS arm64)

| | |
|---|---|
| BLAS | **accelerate** |
| threadpool mặc định | `openblas=10; openmp=10` |
| python / numpy / scipy / sklearn / hmmlearn | 3.9.6 / 2.0.2 / 1.13.1 / 1.6.1 / 0.3.3 |
| 2 lần chạy, `*_NUM_THREADS=1` | **CÙNG hash** `470fdff6…` |
| 2 lần chạy, thread mặc định (10) | **CÙNG hash** `470fdff6…` |

**Bốn lần chạy, MỘT hash.** Tất định nội máy ĐẠT ở local, và — quan trọng
hơn — **số thread KHÔNG đổi kết quả trên máy này**. Hash của bộ 1 thread
và bộ 10 thread giống hệt nhau.

Điều này LOẠI threading khỏi danh sách nghi phạm ở phía macOS. Nó không
loại được ở phía Ubuntu: Accelerate và OpenBLAS có chiến lược chia khối
khác nhau, nên "1 thread không đổi gì" ở đây không suy ra được điều tương
tự ở kia. Biến `*_NUM_THREADS=1` vẫn giữ trong CI — nó rẻ, và giá trị của
nó là làm cho số liệu Ubuntu **diễn giải được**, không phải để sửa lỗi.

Nghi phạm còn lại, theo thứ tự: (1) thư viện BLAS — `accelerate` vs
`openblas`; (2) kiến trúc — `arm64` vs `x86_64`, khác nhau ở FMA và độ
rộng thanh ghi vector.

Ubuntu runner còn chờ số liệu. Cho tới lúc đó, KHÔNG kết luận nguyên
nhân — mẫu hỏng của dự án này là kết luận từ đọc code thay vì từ số đo,
và nó đã lặp bốn lần.

## Cửa sổ nào lệch, và `n_init` hiệu dụng (2026-08-15)

### Mục 1 — giả thuyết "cửa sổ biên BIC 3.0 gây ra bar lệch" là SAI

Bar đầu tiên lệch giữa local và CI là **2025-05-16**. Cửa sổ có biên BIC
3.0 điểm là **#11**, và OOS của nó là **2025-10-20 → 2026-04-19** —
**không chứa** 2025-05-16.

| # | IS | OOS | biên BIC | % | thắng | nhì |
|---|---|---|---|---|---|---|
| 9 | 2023-10-22 → 2024-10-20 | 2024-10-21 → 2025-04-20 | 25.0 | 0.35% | 4 | 5 |
| **10** | **2024-04-21 → 2025-04-20** | **2025-04-21 → 2025-10-19** | **36.4** | **0.53%** | **5** | **4** |
| 11 | 2024-10-20 → 2025-10-19 | 2025-10-20 → 2026-04-19 | **3.0** | 0.04% | 4 | 5 |
| 12 | 2025-04-20 → 2026-04-19 | 2026-04-20 → 2026-08-04 | 99.3 | 1.51% | 5 | 4 |

Cửa sổ THẬT SỰ giao dịch 2025-05-16 là **#10**: biên BIC **36.4 (0.53%)**,
thắng `n_components=5`, á quân `4`. Đó là biên rộng thứ 5 trong 13 — **không
có gì mong manh bất thường**.

**Vòng nhân quả KHÔNG đóng được.** Cơ chế "nhiễu liên máy lật lựa chọn BIC
ở cửa sổ mong manh nhất" không giải thích được bar 2025-05-16, vì cửa sổ
mong manh nhất trade một giai đoạn KHÁC, muộn hơn 5 tháng.

Giả thuyết thay thế còn để ngỏ, chưa đo: khác biệt không đến từ việc lật
`n_components`, mà từ việc cùng `n_components=5` cho ra **tham số model
khác nhau** (restart nào thắng, hoặc EM dừng ở điểm khác) — thứ không lộ
ra ở bảng BIC. Muốn phân biệt cần so `means_`/`transmat_` của cửa sổ #10
giữa hai máy, không phải so BIC.

Cửa sổ #11 (biên 3.0) vẫn là điểm yếu đã biết, chỉ là **không phải điểm
yếu đã kích hoạt**: nếu chạy đủ dài để tới OOS của nó, nó là chỗ dễ lật
nhất.

### Mục 3 — `n_init` hiệu dụng

Định nghĩa "dùng được" quyết định câu trả lời, nên ghi cả hai:

| tiêu chí | restart dùng được | `n_init` hiệu dụng |
|---|---|---|
| không phân kỳ `\|delta\|>1`, không chạm trần `n_iter` | 621/650 (95.5%) | **9.55/10** |
| thêm điều kiện **không tràn số** | **0/650 (0.0%)** | **0** |

Tiêu chí thứ hai không phân biệt được gì — **mọi** lần fit đều tràn:
tối thiểu 111 lần `overflow`/`divide by zero` trong `matmul` mỗi fit,
trung vị 273, cực đại 810. Đó không phải một phép đo hỏng; đó là câu trả
lời: với `covariance_type="full"` và 8 feature, **không có model nào
trong dự án này được fit mà không đi qua tràn số**.

Theo tiêu chí phân kỳ thì không gian tìm kiếm KHÔNG hẹp hơn danh nghĩa
đáng kể — ô (cửa sổ, `n_components`) tệ nhất vẫn còn 7/10 restart dùng
được, và 0/65 ô có ≤ 3.

| # | n=3 | n=4 | n=5 | n=6 | n=7 |
|---|---|---|---|---|---|
| tệ nhất | 10 | 8 | 9 | 8 | **7** |

Con số 7/10 đó là căn cứ của `MIN_N_INIT = 6`:
`ceil(ln(0.001)/ln(0.3)) = 6` — xác suất MỌI restart trong một ô đều bẩn
dưới 0.1%.

### Mục 2 — lớp bảo vệ phụ phẩm thành hợp đồng

Trước hôm nay, "fit phân kỳ luôn thua vì log-likelihood tệ" là một **hệ
quả phụ** của `n_init`, không phải một phòng thủ có chủ ý, và không phép
kiểm nào giữ nó lại. Ba thứ đã thêm: `MIN_N_INIT` (suy từ số đo),
`check_n_init_floor` trong cổng cấu hình, và `EMDivergenceError` raise từ
`select_and_train`.

**Đột biến, hai vòng.** Vòng đầu 6/8 đỏ, hai sống sót — cả hai là lỗ hổng
thật, không phải đột biến tương đương:

- *"bỏ khẳng định trong `select_and_train`"* sống vì mọi test gọi THẲNG
  `_assert_chosen_model_converged()`. Không ai kiểm nó ĐƯỢC GỌI — đúng
  mẫu hỏng của cổng §E: một cổng không nối vào đường thật là một cổng
  không tồn tại.
- *"phân kỳ của restart thắng luôn = 0"* sống vì không test nào kiểm
  trường `max_em_divergence` được ĐIỀN từ fit thật. Cả cổng đứng trên nó.

Thêm ba test đi qua `scan_bic`/`select_and_train` với một `GaussianHMM`
giả (không có cách nào ép EM thật phân kỳ một cách tất định — một test
dựa vào "nó thường xảy ra" là test ngẫu nhiên đội lốt). Vòng hai: **8/8
đỏ**.

## Cổng §E — kiểm chứng HAI CHIỀU trên CI thật (2026-08-16)

Cổng §E giờ đã được kiểm chứng bằng THÍ NGHIỆM trên CI thật, không bằng
đọc code. Hai chiều, hai commit khác nhau trên nhánh `test-cong-e-doi-core`:

| chiều | commit chạm | kỳ vọng | quan sát |
|---|---|---|---|
| (a) | `core/signal_generator.py` | chạy `pytest -m slow` | **chạy** — và ĐỎ ở `test_regression_vs_phase7_baseline`, một test slow thật |
| (b) | chỉ `docs/`, `ops/` | bỏ qua | **bỏ qua**, job 36s |

Chiều (a) tự chứng minh theo cách mạnh hơn cả kỳ vọng: cổng không chỉ
chạy, nó còn BẮT được một khác biệt thật (harness đỏ trên Ubuntu). Chiều
(b) là chiều hay bị bỏ quên — **một cổng LUÔN chạy cũng vô dụng như một
cổng không bao giờ chạy**, vì nó sẽ bị tắt trong tuần đầu.

### Nguyên nhân gốc của CI #8 và CI #14 vẫn KHÔNG BIẾT

Cổng hoạt động, nhưng **không có nghĩa là đã hiểu vì sao nó từng không
hoạt động**. Bản sửa gộp ba khiếm khuyết độc lập cùng lúc (mốc so tính
trong cùng bước; không xác định được mốc thì `exit 1`; `git diff` không đi
vào pipe), nên nó không phân biệt được cái nào là nguyên nhân thật.

Ba giả thuyết đều đã bị làm yếu bằng bằng chứng, không cái nào bị loại
hẳn:

| | trạng thái |
|---|---|
| H1 `before` = 40 số 0 | không giải thích được CI #8 — `before` ở đó là `a488ccd`, SHA thật |
| H2 checkout nông | `slow-gate` ĐÃ có `fetch-depth: 0` (đọc từ YAML đã parse) |
| H3 biến shell không sống qua bước | SAI — `BASE` đi qua `$GITHUB_OUTPUT` |
| H4 `before` mồ côi sau force-push | chỉ áp cho CI #14, không cho CI #8 |

Bước "CHẨN ĐOÁN (tạm)" được thêm vào job §E để trả lời đúng câu này và
**chưa ai đọc output của nó**. Máy làm việc không có `gh`, nên mọi thông
tin CI trong phiên này đều phải do người dùng dán vào.

**Giữ bước chẩn đoán lại.** Gỡ nó bây giờ là đóng hồ sơ bằng "đã sửa,
không rõ vì sao" — đúng thứ CLAUDE.md #18 cấm. Ghi đây thành một khoảng
trống ĐÃ BIẾT thay vì để nó im lặng biến mất khi cổng đã xanh.

### Sửa bản ghi sai trước đó

Mục "3. Cổng §E đỏ ở commit chạm tầng quyết định" ở trên **SAI**: nó nói
job "Cổng §E" chạy đúng vai và đỏ ở `c7fb25b`. Thực tế job đó SUCCEEDED
trong 1m55s và bước cuối ghi "Bỏ qua (diff không chạm tầng quyết định)" —
cổng chưa từng chạy `pytest -m slow` ở commit đó. Nguyên nhân bản ghi sai:
đọc nhầm ảnh chụp CI #1 thành CI #8.

## Số liệu CI phải ra NGOÀI log — sửa chữa QUY TRÌNH (2026-08-16)

### Nút thắt

Máy làm việc không có `gh`. Mọi số liệu CI trong phiên 2026-08-15/16 chỉ
tới được nơi cần nó bằng cách người dùng mở trang GitHub và **chép tay
từng bước**. Điều đó chặn công việc ít nhất năm lần, và mỗi lần chặn lại
đẻ ra một vòng đoán mò:

| lần chặn | hệ quả |
|---|---|
| cổng §E không kích hoạt | 4 vòng giả thuyết (H1, H2, H3, H4) — **ba cái sai** |
| bước CHẨN ĐOÁN chưa ai đọc | nguyên nhân gốc CI #8/#14 tới giờ **vẫn không biết** |
| harness đỏ trên Ubuntu | phải đoán từ hai dòng người dùng chép lại |
| tất định nội máy trên Ubuntu | chưa có số liệu |
| ~~bất đối xứng py3.9 vs py3.11~~ | **KHÔNG TỒN TẠI** — xem mục "Bất đối xứng py3.9/py3.11 là ảo ảnh" bên dưới |

Đây không phải năm sự cố riêng lẻ. Đó là **một** khiếm khuyết kiến trúc
lặp lại: phép đo chạy ở nơi A, người cần nó ở nơi B, và giữa hai nơi chỉ
có thao tác thủ công.

### Sửa

GitHub Actions có hai kênh hiện ra NGOÀI log, đọc được không cần đăng
nhập và không cần công cụ:

- `::notice title=X::...` → mục **Annotations** ngay trang run
- `$GITHUB_STEP_SUMMARY` → trang **Summary**, hỗ trợ markdown

`ops/ci_bao_cao.py` gói cả hai. Bốn phép đo giờ phát ra cả hai kênh:

| phép đo | annotation |
|---|---|
| tất định nội máy | `TAT DINH NOI MAY` — `run1=… run2=… giong=yes/no` |
| môi trường số học | `DAU VAN TAY` — `blas=… arch=… python=… numpy=…` |
| chẩn đoán cổng §E | `CHAN DOAN E` — `before=… base_resolve=… ket_luan=H1/H2/H2-SAI` |
| pytest đỏ | `PYTEST FAILED <job>` — **tên test, không traceback** |

Step summary nhận bảng chi tiết: phiên bản công cụ (mỗi job matrix riêng),
phạm vi đã kiểm, dấu vân tay đầy đủ, và danh sách test đỏ.

### Ràng buộc phải tôn trọng

GitHub hiện **tối đa 10** annotation mỗi run, cắt mỗi cái ở **~4000 ký
tự**. Nên kênh này chỉ chở **KẾT LUẬN ĐÃ RÚT GỌN**. Đổ log thô vào đây
làm chính nó vô dụng: 10 annotation đầy chữ, không cái nào trả lời được
câu hỏi nào. Chi tiết đi vào step summary, nơi không có giới hạn đó.

Khi cắt, cắt ở **ĐẦU** chứ không ở cuối — kết luận nằm cuối một danh
sách, và một annotation mất kết luận thì không khác gì không có.

### Ba chi tiết đã suýt làm kênh hỏng IM LẶNG

1. **Thứ tự escape.** `%` phải escape TRƯỚC `\n`; ngược lại `%0A` do
   escape xuống dòng sinh ra bị escape lần hai thành `%250A`.
2. **Tiêu đề có dấu tiếng Việt.** GitHub không hiện, và nó không báo lỗi
   — annotation đơn giản không xuất hiện. `notice()` giờ `raise` với
   tiêu đề không phải ASCII, biến lỗi vô hình thành lỗi thấy lúc viết.
3. **`pytest | tee`.** Mã thoát sau pipe là của `tee` (CLAUDE.md #17).
   Ghi ra file, đọc `$?`, rồi mới báo cáo — và bộ báo cáo LUÔN trả 0, vì
   một bộ báo cáo tự làm job đỏ sẽ che mất nguyên nhân thật.

### `bash -n` cho mọi bước `run:`

Mất một vòng vì chuyện này: một lần sửa `ci.yml` bằng script tự động làm
hỏng thụt lề của hai khối `{ … }`. **YAML vẫn parse được** — lỗi chỉ lộ
ra khi runner chạy. `tests/test_ci_bao_cao.py` giờ chạy `bash -n` từng
bước: 200ms ở local thay cho một vòng push-chờ-đọc-log.

### Đột biến

12 phép, hai vòng. Vòng đầu 11/12; sống sót là *"gỡ báo cáo pytest khỏi
job fast"* — test chỉ kiểm `"--tu-pytest" in lenh`, và job slow-gate vẫn
còn một cái. **Một trong hai job mù mà cổng vẫn xanh** — đúng chế độ hỏng
file này sinh ra để gác.

Thay bằng ràng buộc cấu trúc: mỗi `pytest … > X.log` phải đi kèm
`--tu-pytest X.log` TRONG CÙNG bước, và cả hai job đều phải có. Vòng hai
**12/12 đỏ**.

## Cổng §E: cả H1, H2, H3 đều đã LOẠI BẰNG ĐO — nguyên nhân gốc vẫn chưa xác định (2026-08-16)

Bước CHẨN ĐOÁN trên `2f1c961` phát ra annotation `CHAN DOAN E` và **đã
đọc được** — lần đầu một phép đo CI tới thẳng nơi cần nó, không qua thao
tác chép tay:

```
base_resolve=OK   git_diff=OK   shallow=false   n_commit=127
```

| | trạng thái | loại bằng |
|---|---|---|
| H1 `before` = 40 số 0 | **LOẠI** | `before` là SHA thật ở CI #8 (`a488ccd`); `base_resolve=OK` |
| H2 checkout nông | **LOẠI** | `shallow=false`, `n_commit=127`, `git_diff=OK` — đo trên runner thật |
| H3 biến shell không sống qua bước | **LOẠI** | `BASE` đi qua `$GITHUB_OUTPUT`, đọc từ `ci.yml` đã parse |
| H4 `before` mồ côi sau force-push | còn ngỏ, chỉ áp cho CI #14 | — |

**Nguyên nhân gốc của CI #8 và CI #14 KHÔNG XÁC ĐỊNH ĐƯỢC.** Ghi đúng như
vậy, không viết thành "đã sửa".

Ba lý do vì sao nó có thể mãi mãi không xác định được, và cả ba đều là
hệ quả của việc sửa trước khi đo:

1. Bản sửa gộp **ba** khiếm khuyết độc lập trong một lần. Kể cả có log
   đầy đủ của CI #8 bây giờ, nó cũng không nói được cái nào là nguyên
   nhân — chỉ nói được cả ba đã biến mất cùng lúc.
2. Bước CHẨN ĐOÁN chạy trên **`ci.yml` MỚI**. Nó đo runner hôm nay, không
   đo runner của CI #8. `shallow=false` hôm nay không chứng minh
   `shallow=false` hôm đó.
3. Không chạy lại được CI #8: `c7fb25b` đã bị revert, và nhánh mang nó đã
   xoá.

Điều rút ra, quan trọng hơn chính câu hỏi: **thứ tự đúng là ĐO rồi mới
SỬA.** Ở đây làm ngược — sửa ba thứ cùng lúc rồi mới thêm bước đo — nên
đổi lấy một cổng hoạt động bằng cái giá là một nguyên nhân vĩnh viễn
không biết. Với một cổng CI thì đánh đổi đó chấp nhận được; với một bug
giao dịch thì không.

Bước CHẨN ĐOÁN giữ lại tới khi nhánh tạm merge, rồi gỡ — nó đã trả lời
xong phần trả lời được.

## Lỗ trong chính kênh báo cáo: bước ĐỎ TRƯỚC pytest không tự báo tên (2026-08-16)

Trang run của `2f1c961` hiện `CHAN DOAN E` nhưng **không** hiện
`PYTEST FAILED` dù job py3.11 đỏ. Hai khả năng có ý nghĩa khác hẳn nhau:
kênh báo cáo hỏng ở đúng chỗ cần nhất, hay pytest chưa từng chạy.

**Nghiệm bằng đột biến, không bằng đọc code.** `tests/test_ci_bao_cao.py`
giờ trích kịch bản `run:` của bước ra khỏi `ci.yml`, dựng một `pytest`
GIẢ luôn đỏ trên `PATH`, chạy đúng những dòng runner chạy, rồi đọc stdout
và mã thoát. Kết quả: bước **CÓ** phát `::error title=PYTEST FAILED`, kèm
tên test, và **giữ** mã thoát 1 — ở cả hai job.

Nên kênh không hỏng. Lỗ nằm chỗ khác: `ruff` và `mypy` chạy **TRƯỚC**
`pytest`. Một job đỏ ở mypy thì pytest **không bao giờ chạy**, không có
annotation nào, và trang run trông y hệt trường hợp kênh hỏng.

Đây là **lần thứ tư** của mẫu "lỗi bị che bởi lỗi đứng trước": sau
`cmd | tail` nuốt mã thoát, bước PHẠM VI không chạy vì pytest đỏ trước,
và `test_snapshot` gọi mạng mà job không đỏ vì mypy chết trước.

Sửa: `ruff` và `mypy` giờ cũng phát `::error` kèm **20 dòng đầu** của
output. Cắt ở ĐẦU, ngược với pytest — output của linter có phần dùng
được ở đầu, còn pytest có kết luận ở cuối. Cắt nhầm chiều là mất đúng thứ
cần đọc.

### `DAU VAN TAY` chuyển sang job `fast`

Nó tốn ~0 giây nhưng đang nằm trong `slow-gate`, tức là **vắng mặt ở đúng
những lần chạy cần nó nhất** — mọi commit không chạm `core/`. Chuyển sang
job `fast` cũng có nghĩa nó chạy ở CẢ `py3.9` LẪN `py3.11`, và bất đối
xứng giữa hai phiên bản có thể nằm ngay trong bảng đó (numpy/scipy wheel
khác nhau theo phiên bản Python).

Phép đo tất định 2 lần (~4 phút) giữ nguyên ở `slow-gate` — đúng chỗ.

Đột biến 9 phép, **9/9 đỏ**, gồm cả hai đột biến vị trí ("dấu vân tay
quay về chỉ ở slow-gate", "kéo phép đo 4 phút vào job fast").

## Bất đối xứng py3.9/py3.11 là ẢO ẢNH — đọc trạng thái CI khi chưa chạy xong (2026-08-16)

Ghi trước đó: "CI #21 py3.11 ĐỎ, py3.9 XANH — bất đối xứng theo phiên bản
Python, chưa từng thấy". **SAI.** Cả hai job đỏ ở **cùng một test**. Job
py3.9 hiện xanh vì lúc đọc nó **chưa chạy xong**.

Chế độ hỏng: "chưa xong" và "đã xong, xanh" trông giống nhau đủ để đọc
nhầm khi đang vội. Cùng họ với ba lần trước trong dự án này — "không có
kết quả" đọc thành "sạch", "Bỏ qua" đọc thành "đã kiểm", `grep` trên thư
mục không tồn tại đọc thành "không có vi phạm".

Cái giá: một vòng chẩn đoán cho một hiện tượng không tồn tại, gồm cả việc
đi tìm interpreter 3.11 trên máy dev (không có) và cân nhắc tải một bản
standalone về chỉ để tái hiện nó.

**Quy tắc rút ra: đọc kết luận CI chỉ khi run đã kết thúc.** Annotation
giúp ở đây — nó chỉ tồn tại sau khi bước phát ra nó chạy xong, nên "không
có annotation" là tín hiệu rõ hơn "job hiện màu xanh".

## Test đỏ trên CI: `GITHUB_STEP_SUMMARY`, KHÔNG phải năm biến thread (2026-08-16)

`tests/test_env_isolation.py::test_khong_bien_moi_truong_nao_bi_bo_quen`
đỏ ở cả hai job. Giả thuyết ban đầu, nghe rất hợp lý: `ci.yml` vừa đặt
`OMP/OPENBLAS/MKL/NUMEXPR/VECLIB_*_THREADS` ở tầng job, và
`ops/kiem_tat_dinh.py` đọc chúng — năm tên mới chưa phân loại.

**ĐO trước khi sửa, và phép đo bác bỏ giả thuyết đó:**

```
tổng biến dò được : 20
THREAD vars       : KHÔNG có cái nào
chưa phân loại    : không có
```

Bộ dò dùng **regex trên chuỗi hằng**, còn `kiem_tat_dinh.py` đọc qua biến
lặp (`for b in _BIEN_THREAD: os.environ.get(b, ...)`). Regex mù hoàn toàn
với cách đó. Năm biến ấy **chưa từng** nằm trong phép chống trôi.

Nguyên nhân thật là `GITHUB_STEP_SUMMARY` — một chuỗi hằng trong
`ops/ci_bao_cao.py`, thêm ở `2f1c961` và phân loại ở `2e07b95`. Lần chạy
CI đọc được là của `2f1c961`; nó đã được sửa trước khi ai đọc nó.

Bằng chứng xác định lần chạy: `2f1c961` có
`os.environ.get("GITHUB_STEP_SUMMARY")` trong `ops/ci_bao_cao.py` và
KHÔNG có tên đó trong `tests/conftest.py`; số test collect ở đó là 1089,
khớp với "1 failed, 1087 passed" mà CI báo.

### Điểm mù thật, tìm ra nhờ đi tìm nhầm chỗ

Giả thuyết sai vẫn dẫn tới một phát hiện đúng: **phép chống trôi danh
sách đen tự nó có điểm mù** — nó không thấy biến đọc qua biến lặp. Đó là
một khẳng định "đã phân loại hết" hẹp hơn nó tự nhận (CLAUDE.md #19), và
nó vô hình đúng lúc `ci.yml` bắt đầu đặt năm biến đó.

Sửa: thêm đường quét **AST** nối biến-lặp với tuple hằng nó duyệt
(`_ten_qua_bien_lap`). Sau đó bộ dò thấy 25 biến thay vì 20, và năm biến
thread được xếp vào `NON_SECRET_ENV` — tham số vận hành, không bí mật,
**nhưng chúng đổi KẾT QUẢ SỐ**, nên phải có mặt trong dấu vân tay chứ
không được lặng lẽ vắng.

Đột biến 6 phép, **6/6 đỏ**, gồm cả đột biến "bộ dò đếm BỪA mọi tuple
chuỗi" — chiều ngược lại cũng phải đúng, một bộ dò đếm bừa buộc phân loại
những tên không phải biến môi trường và làm danh sách mất nghĩa.

## Kênh annotation đã thay thế được vòng "người dùng copy log" (2026-08-16)

Xác nhận trên CI thật. Ba câu hỏi từng chặn cả phiên được trả lời **chỉ
bằng cách đọc mục Annotations của trang run**, không mở log, không cần
`gh`, không chép tay:

| câu hỏi | annotation trả lời |
|---|---|
| cổng §E hỏng ở đâu | `CHAN DOAN E` — `base_resolve=OK git_diff=OK shallow=false n_commit=127` |
| test nào đỏ trên CI | `PYTEST FAILED` — tên test, không traceback |
| bất đối xứng 3.9/3.11 có thật không | hai bản ghi `PYTEST FAILED` giống hệt nhau → không |

Trước đó mỗi câu hỏi tốn một vòng: đề nghị người dùng mở log → chép tay →
dán → phân tích. Vòng đó biến mất.

**Điều còn thiếu và đã bịt:** `ruff`/`mypy` chạy TRƯỚC `pytest`, nên một
job đỏ ở mypy không phát annotation nào và trang run trông y hệt trường
hợp kênh hỏng. Cả hai giờ cũng phát `::error` kèm 20 dòng đầu.

Chi phí duy trì: `tests/test_ci_bao_cao.py` ghim rằng mỗi
`pytest … > X.log` phải kèm `--tu-pytest X.log` trong CÙNG bước, và cả
hai job đều phải có. Không có ràng buộc đó thì kênh mất dần theo từng lần
sửa `ci.yml`, và mất im lặng.

## Giả thuyết "số luồng OpenBLAS" — BỊ BÁC BỎ bằng đo (2026-08-16)

**Một giả thuyết bị bác bỏ là KẾT QUẢ, không phải thất bại.** Ghi lại để
ba tháng nữa không có người thử lại đúng nó.

### Giả thuyết

`regression_harness` đỏ ở CI #18 và xanh ở CI #26; khác biệt duy nhất
thấy được là `*_NUM_THREADS=1`. Lập luận: cộng dồn đa luồng không cố định
thứ tự → lệch chữ số cuối → EM khuếch đại (10.5% số fit phân kỳ, 62.534
lần tràn số trong `matmul`) → equity rẽ ở bar 1845. Accelerate trên macOS
không nhạy với số luồng nên phép đo ở local không thấy.

Nghe rất hợp lý. Và sai.

### Thí nghiệm (CI #27, `cf17c27`) — đúng MỘT biến

`OPENBLAS_NUM_THREADS: 1 → 4` ở `slow-gate`. Bốn biến thread kia giữ
nguyên `1`, để cô lập đúng OpenBLAS chứ không phải "đa luồng nói chung".

```
DAU VAN TAY      omp=1 openblas=4 threadpool=openblas=4; openmp=1
TAT DINH NOI MAY run1=470fdff6… run2=470fdff6… giong=yes
PYTEST OK slow   9 passed
```

**Biến ĐÃ có hiệu lực** — `threadpool` xác nhận `openblas=4`, không phải
một thí nghiệm rỗng. Nhưng hash vẫn `470fdff6…`: giống hệt 1 luồng, giống
hệt macOS/Accelerate. `regression_harness` XANH.

**Số luồng OpenBLAS KHÔNG phải nguyên nhân.**

### Vì sao vẫn đáng làm

Việc cô lập đúng một biến biến một câu trả lời "không" thành thông tin:

- Loại hẳn OpenBLAS khỏi danh sách, thay vì loại "đa luồng" một cách mơ hồ.
- Làm lộ ứng viên tiếp theo: `omp=1` VẪN CÒN. `hmmlearn` gọi qua
  `scikit-learn`, và sklearn dùng **OpenMP** chứ không phải BLAS cho phần
  lớn vòng lặp. Ở CI #18, `threadpool` là `openmp=4`.
  `OMP_NUM_THREADS` là biến DUY NHẤT còn khác giữa hai lần chạy.
- Củng cố tính tất định liên máy: hash `470fdff6…` giờ khớp qua **ba** cấu
  hình — macOS/Accelerate/arm64/numpy 2.0.2, Ubuntu/OpenBLAS/x86_64/numpy
  2.4.6 với 1 luồng, và cùng Ubuntu với OpenBLAS 4 luồng.

### Nếu thí nghiệm OMP cũng xanh

Thì cả hai giả thuyết luồng sai, và khác biệt nằm trong **mã**, không phải
môi trường — phải bisect sáu commit giữa `76c380e` (CI #18, đỏ) và
`2ae254a` (CI #26, xanh). Ứng viên hàng đầu: `99babef` (hợp đồng phân kỳ
EM — `EMDivergenceError` + `MIN_N_INIT=6`), vì nó chạm thẳng vào đường
chọn model.

## Giả thuyết "số luồng OpenMP" — BỊ BÁC BỎ. Cả hai giả thuyết luồng sai (2026-08-16)

CI #28 (`5e69527`), đúng một biến đổi so với vòng một:

```
DAU VAN TAY      omp=4 openblas=1 threadpool=openblas=1; openblas=1; openmp=4
TAT DINH NOI MAY run1=470fdff6… run2=470fdff6… giong=yes
PYTEST OK slow   9 passed in 110.26s
```

`OMP_NUM_THREADS=4` có hiệu lực thật (`threadpool` xác nhận `openmp=4`),
hash không đổi, `regression_harness` xanh.

**Hash `470fdff6…` giờ khớp qua BỐN cấu hình môi trường:**

| # | môi trường |
|---|---|
| 1 | macOS · Accelerate · arm64 · numpy 2.0.2 · 1 luồng |
| 2 | macOS · Accelerate · arm64 · numpy 2.0.2 · 10 luồng (mặc định) |
| 3 | Ubuntu · OpenBLAS · x86_64 · numpy 2.4.6 · 1 luồng |
| 4 | Ubuntu · OpenBLAS · x86_64 · numpy 2.4.6 · `openblas=4` rồi `openmp=4` |

Tất định của backtest ghim mạnh hơn nhiều so với những gì dự án từng dám
khẳng định — nhưng vẫn là **khẳng định có phạm vi**: bốn cấu hình này,
không phải "mọi máy".

Cả hai giả thuyết luồng sai, nên khác biệt ở CI #18 nằm trong **mã** hoặc
trong một biến chưa nghĩ tới.

## Hợp đồng EM nổ trên cấu hình MẶC ĐỊNH — và forward test chạy cấu hình đó (2026-08-16)

`ops/compare_versions.py` (chạy `main_mod.load_settings()`, tức cấu hình
SẢN XUẤT) dừng ngay ở ref `2ae254a`:

```
EMDivergenceError: Model được BIC chọn (n_components=7, BIC=6002.95)
phân kỳ: log-likelihood GIẢM 271.5
```

### Ba cấu hình, ba số feature khác nhau

| cấu hình | số feature |
|---|---|
| pruned-8 — bộ **ĐÃ KIỂM ĐỊNH**, ghim trong `regression_harness` | **8** |
| `config/settings.yaml` — sản xuất | **14** |
| `forward/config_frozen.yaml` — **ĐANG CHẠY THẬT từ 2026-08-06** | **14** |

Sáu feature thừa: `atr_norm_14`, `distance_to_sma200_pct`,
`log_return_20`, `roc_10`, `roc_20`, `rsi_zscore_14` — đúng những cái
ablation đã LOẠI để ra pruned-8.

`covariance_type="full"` làm số tham số tăng bậc hai theo số feature, nên
8 → 14 không nhỏ. Đó là lý do BIC chọn phải model phân kỳ ở bộ đầy đủ mà
không ở pruned-8.

### Lần thứ NĂM của mẫu "khẳng định hẹp hơn nó tự nhận"

Bốn lần trước là phát hiện về code hoặc công cụ. **Lần này do chính bản
ghi trong file này gây ra** — mục "Phân kỳ EM trong backtest kiểm định"
viết `0/13 cửa sổ chọn phải model phân kỳ` mà không nói phạm vi pruned-8,
và `CLAUDE.md` #13b chép lại y nguyên. Trong đúng mục lập luận cho kỷ
luật #19.

Vì sao không test nào đỏ: `regression_harness` ghim pruned-8, nên **không
phép kiểm nào chạm cấu hình mặc định**. Bán kính ảnh hưởng của hợp đồng
`EMDivergenceError` chưa từng được đo trước khi commit.

### Hệ quả CHƯA XỬ LÝ, cần quyết định của người

1. **Merge nhánh tạm vào `main` sẽ làm thí nghiệm forward RAISE.**
   `forward/logger.py:564` gọi `select_and_train`, và tuy `logger.py` đóng
   băng thì `core/hmm_engine.py` KHÔNG — hợp đồng nằm ở đó. Lần retrain kế
   tiếp sau merge sẽ ném `EMDivergenceError`.
2. **Thí nghiệm forward 12 tháng đang kiểm định một cấu hình chưa từng
   được kiểm định.** `VALIDATION_REPORT` (Sharpe 0.94, §4.9) nói về 8
   feature; thứ đang chạy là 14. Khoảng trống này có TỪ TRƯỚC hợp đồng EM;
   hợp đồng chỉ làm nó lộ ra.
3. `forward/config_frozen.yaml` ghim SHA256 — sửa nó = kết thúc thí nghiệm
   hiện tại (CLAUDE.md #15). Nên đây không phải thứ sửa lặng lẽ được.

Ba lựa chọn cho (2), chưa chọn: chấp nhận và ghi rõ thí nghiệm đang đo cấu
hình chưa kiểm định; kết thúc và khởi động lại với pruned-8; hoặc thu hẹp
`n_candidates` bỏ 7.
