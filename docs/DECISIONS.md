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

Đánh giá trên cấu hình pruned-8, `reports/pruned8_base` (+ `reports/pruned8_eth`
cho tiêu chí 7). **Kiểm đếm: 4 PASS / 2 FAIL / 2 CHƯA CHẠY.**

> Lưu ý: một lần trao đổi trước ghi "3 PASS / 2 FAIL / 3 chưa chạy" — con số
> đó chưa được tính ra bằng dữ liệu thật tại thời điểm nói (lệnh tính khi đó
> lỗi `ModuleNotFoundError: pandas` do quên activate venv, không có bảng nào
> thực sự được đưa ra trước đó). Bảng dưới đây là lần đầu tính đủ cả 8 tiêu
> chí bằng số thật; nếu 4/2/2 mâu thuẫn với kỳ vọng, cần đối chiếu lại thay
> vì lấy 3/2/3 làm chuẩn.

| # | Tiêu chí | Số liệu | Kết quả |
|---|---|---|---|
| 1 | Sharpe OOS > 1.0 sau chi phí | 0.9411 | **FAIL** |
| 2 | Calmar > buy-and-hold | 0.5996 vs 0.5217 | PASS |
| 3 | Sharpe > vol-target tĩnh ít nhất +0.2 | 0.9411 vs 0.9142, chênh **+0.027** | **FAIL** (cần +0.2) |
| 4 | Ngoài 2 độ lệch chuẩn của random allocation | z = (0.9411−0.6251)/0.1065 = **2.97σ** | PASS |
| 5 | 2022 không lỗ nặng hơn buy-and-hold | CHƯA CHẠY lại trên pruned-8 (chỉ có số từ baseline 14-feature: chiến lược −29.3% vs BH −65.4%, pass trên cấu hình cũ) | **CHƯA CHẠY** |
| 6 | Sharpe 4 lần bar-offset chênh ≤ 0.3 | CHƯA CHẠY lại trên pruned-8 (chỉ có sweep offset trên 14-feature) | **CHƯA CHẠY** |
| 7 | ETH không tune: Sharpe > 0.5 | 0.9278 | PASS |
| 8 | Phí < 30% lợi nhuận gộp | 11.68% (fee 4445.04 + slippage 1333.51 = 5778.55 USDT / gross 49489.51 USDT) | PASS |

**Không đủ 8/8 — theo CLAUDE.md bất biến #12, chưa được xây tầng thực thi
(Phase 5 / risk manager).** Hai fail (1, 3) không phải biên nhỏ có thể tranh
cãi: cần thêm +0.06 Sharpe cho tiêu chí 1, và +0.173 chênh lệch cho tiêu chí
3 — còn khá xa. Hai tiêu chí chưa chạy (5, 6) cần rerun trên pruned-8 trước
khi có thể tuyên bố bất kỳ điều gì về chúng.

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

## Câu hỏi mở cho phiên sau

- `(full,5)`, `(full,11)`, `(diag,7)` được nhắc tới trong một yêu cầu chẩn
  đoán nhưng không tìm thấy định nghĩa ở đâu trong repo tại thời điểm viết
  file này (`docs/DECISIONS.md` không tồn tại trước bản này). Nếu đây là
  tên cấu hình đã thống nhất ở đâu đó ngoài repo, cần ghi định nghĩa chính
  xác vào đây khi nhắc lại.
- Tiêu chí 5, 6 cần rerun trên `--feature-subset` pruned-8 (`--period 2022`,
  `--bar-offset 0,6,12,18`) trước khi §4.9 có thể đóng.
- Thí nghiệm giả thuyết uncertainty-mode (2026-08-05, xem phần dưới nếu đã
  điền) — kiểm chứng liệu giảm nửa allocation ở bar confidence thấp có phải
  phản ứng đúng, dựa trên phát hiện: trên 371 bar <50% confidence, chiến
  lược kém buy-and-hold 0.715 Sharpe.
