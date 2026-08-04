# Brain — Crypto Edition

**Regime-based allocation bot cho BTC/USDT trên Bybit spot.**
Bản chuyển đổi từ spec gốc (Alpaca / US equities). Kiến trúc giữ nguyên; các giả định thị trường được tính lại cho crypto.

---

## 0. Tóm tắt thay đổi so với bản gốc

Đọc phần này trước. Nếu bạn (hoặc coding agent) copy nguyên các con số từ spec equities sang, hệ thống sẽ sai một cách âm thầm — backtest vẫn chạy, kết quả vẫn đẹp, nhưng live sẽ lỗ.

| Hạng mục | Bản gốc (Alpaca equities) | Bản này (Bybit spot) | Lý do |
|---|---|---|---|
| Sàn | Alpaca | Bybit v5 (`pybit`), CCXT làm fallback | Alpaca crypto không cho margin, phí 0.15–0.25%, hạn chế địa lý |
| Tài sản | 10 cổ phiếu | BTC/USDT duy nhất | Alt tương quan ~0.9 với BTC → đa dạng hoá là ảo tưởng |
| Leverage | 1.25x ở low-vol | **1.0x, không leverage** | Spot không có margin. Perps để Phase 10 |
| Short | Không | Không (giữ nguyên) | Luận điểm gốc vẫn đúng, càng đúng hơn với crypto |
| Phí | $0 commission | **0.10% mỗi chiều** (Bybit spot VIP0) | Đây là thay đổi lớn nhất về kinh tế của hệ thống |
| Slippage | 0.05% | 0.03% (BTC/USDT thanh khoản rất sâu) | |
| Ngưỡng rebalance | 10% | **25%** | Phí round-trip 0.2% × churn cao sẽ ăn hết edge |
| Năm giao dịch | 252 ngày | **365 ngày** | 24/7 |
| Khung thời gian | 5-min live, 1D bar | **1D bar (đóng 00:00 UTC)** | Xem §2.1 |
| Giờ thị trường | Có, phải chờ mở cửa | Không tồn tại | Bỏ toàn bộ logic `is_market_open()` |
| Gap qua đêm | Rủi ro chính | Không tồn tại | Bỏ rule "overnight 3x gap sizing" |
| Circuit breaker | DD 2%/3% ngày | **DD 4%/6% ngày** (mặc định khởi điểm) | Vol BTC ~2.5–3x SPY; ngưỡng cũ sẽ bắn liên tục |
| Số lượng đặt lệnh | `int()` — cổ phiếu nguyên | **`Decimal` làm tròn theo `basePrecision`** | Crypto chia lẻ được. `int()` là bug nghiêm trọng |
| Correlation check | 60-day rolling, reject >0.85 | Bỏ ở v1 (một tài sản) | Giữ interface, không implement |

**Ba thứ giữ nguyên không đổi và là phần giá trị nhất của spec gốc:**

1. Forward algorithm thay cho Viterbi (`test_look_ahead.py` là bài test bắt buộc)
2. Chọn số regime bằng BIC
3. Risk manager có quyền phủ quyết tuyệt đối, hoạt động độc lập hoàn toàn với HMM

### 0.1 Ba bổ sung mang tính thiết kế, không phải tinh chỉnh tham số

Ba mục dưới đây không có trong spec gốc và không suy ra được từ nó. Chúng xử lý các đặc tính của crypto mà mô hình equity không có.

**A. Structural Trend Gate (Phase 3.5) — quan trọng nhất.**
Chế độ thất bại chí mạng của thiết kế gốc khi áp lên crypto: **thị trường giảm kéo dài với biến động thấp.** Suốt phần lớn năm 2022, BTC bào mòn đi xuống trong khi realized vol *giảm dần*. HMM sẽ phân loại đó là "vol thấp" và strategy sẽ vào 95%. Ở equities điều này ít nguy hiểm vì giai đoạn vol thấp gắn với drift đi lên; ở crypto không có bảo đảm đó. Cần một tầng lọc xu hướng cấu trúc, độc lập với HMM, chỉ có quyền *giảm* tỷ trọng.

**B. Feature crypto-native (Phase 2.3).**
Bộ feature gốc (ADX, RSI, khoảng cách SMA200, volume z-score) là feature của thị trường equity. Crypto có nguồn tín hiệu riêng phản ánh trực tiếp positioning và đòn bẩy của thị trường — funding rate, open interest, basis perp-spot, tỷ lệ taker mua/bán. Quan trọng hơn: **volume của crypto không đáng tin** (wash trading), nên `trade_count` là thay thế tốt hơn.

**C. Kỷ luật kiểm định (Phase 4.8).**
Dữ liệu BTC chỉ có ~2 chu kỳ. Rủi ro overfit cao hơn equities một bậc. Cần các bài kiểm định mà spec gốc không cần: kiểm tra riêng giai đoạn 2022, kiểm tra độ nhạy với mốc đóng bar, và dùng ETH làm tập kiểm định ngoài mẫu.

### 0.2 Nguyên tắc kiến trúc: mỗi tầng chỉ được GIẢM

Sau khi thêm Structural Trend Gate, hệ thống có ba tầng quyết định tỷ trọng. Quy tắc kết hợp:

```
final_allocation = min(
    hmm_strategy_allocation,   # Phase 3 — theo vol regime
    structural_trend_cap,      # Phase 3.5 — theo xu hướng dài hạn
    risk_manager_cap           # Phase 5 — theo P&L thực tế
)
```

**Không tầng nào được phép làm tăng tỷ trọng do tầng khác đề xuất.** Đây là bất biến kiến trúc, không phải lựa chọn triển khai. Nó bảo đảm: khi bất kỳ tầng nào hỏng theo hướng "quá lạc quan", các tầng còn lại vẫn chặn được. Nếu code ở đâu đó dùng `max()` hoặc trung bình cộng để hoà giải giữa các tầng, đó là bug.

---

## PHASE 1: Scaffolding & Environment

Tạo project Python tên `regime-trader-crypto`:

```
regime-trader-crypto/
├── config/
│   ├── settings.yaml
│   └── credentials.yaml.example
│
├── core/
│   ├── __init__.py
│   ├── hmm_engine.py            # HMM regime detection
│   ├── regime_strategies.py     # Vol-based allocation
│   ├── trend_gate.py            # Structural trend cap      ← MỚI
│   ├── risk_manager.py          # Sizing, drawdown limits, veto
│   └── signal_generator.py      # HMM + gate + risk → signal
│
├── broker/
│   ├── __init__.py
│   ├── base.py                  # ExchangeClient ABC  ← MỚI
│   ├── bybit_client.py          # Bybit v5 qua pybit
│   ├── ccxt_client.py           # Fallback đa sàn      ← MỚI
│   ├── order_executor.py
│   ├── position_tracker.py
│   └── instrument_rules.py      # tick/lot/precision   ← MỚI, quan trọng
│
├── data/
│   ├── __init__.py
│   ├── market_data.py
│   ├── history_loader.py        # Tải & cache OHLCV dài hạn ← MỚI
│   └── feature_engineering.py
│
├── monitoring/
│   ├── __init__.py
│   ├── logger.py
│   ├── dashboard.py
│   └── alerts.py
│
├── backtest/
│   ├── __init__.py
│   ├── backtester.py
│   ├── performance.py
│   ├── cost_model.py            # Phí + slippage tách riêng ← MỚI
│   └── stress_test.py
│
├── tests/
│   ├── test_hmm.py
│   ├── test_look_ahead.py       # BẮT BUỘC
│   ├── test_strategies.py
│   ├── test_trend_gate.py       # Gate chỉ giảm, không tăng ← MỚI
│   ├── test_layer_composition.py # min() chứ không max()    ← MỚI
│   ├── test_risk.py
│   ├── test_orders.py
│   ├── test_precision.py        # Làm tròn qty/price  ← MỚI
│   └── test_cost_model.py       # Phí tính đúng        ← MỚI
│
├── main.py
├── requirements.txt
├── .env.example
└── README.md
```

**requirements.txt:**

```
hmmlearn
pybit                 # SDK chính thức Bybit v5
ccxt                  # fallback đa sàn + tải dữ liệu lịch sử
pandas
numpy
scipy
scikit-learn
ta
pyyaml
python-dotenv
websockets
rich
pyarrow               # cache OHLCV dạng parquet
```

**settings.yaml** — tất cả tham số, nhóm theo section, có comment và giá trị mặc định:

```yaml
exchange:
  name: bybit
  category: spot              # spot | linear (linear = perps, Phase 10)
  testnet: true               # LUÔN true khi bắt đầu
  symbol: BTCUSDT
  quote_asset: USDT
  timeframe: "1D"             # bar đóng 00:00 UTC
  recv_window_ms: 5000

costs:
  taker_fee_pct: 0.10         # Bybit spot VIP0, XÁC MINH LẠI trước khi live
  maker_fee_pct: 0.10
  slippage_pct: 0.03
  assume_taker: true          # bảo thủ: giả định luôn khớp taker

hmm:
  n_candidates: [3, 4, 5, 6, 7]
  n_init: 10
  covariance_type: full
  min_train_bars: 730         # 2 năm × 365 ngày
  zscore_lookback: 365        # KHÔNG phải 252
  retrain_interval_days: 7
  stability_bars: 3
  flicker_window: 20
  flicker_threshold: 4

features:
  tier1_ohlcv: true             # bắt buộc
  tier2_derivatives: false      # bật sau khi tier1 đã validate
  tier3_temporal: false         # cẩn trọng — dễ overfit
  use_trade_count_not_volume: true

trend_gate:
  enabled: true
  sma_period: 200
  slope_lookback: 30
  buffer_pct: 2.0               # dải chết quanh SMA200
  confirm_bars: 5               # chậm hơn HMM (3) một cách có chủ đích
  cap_bull_structure: 1.00
  cap_transition: 0.60
  cap_bear_structure: 0.30

strategy:
  min_confidence: 0.55
  rebalance_threshold_pct: 25 # KHÔNG phải 10
  max_allocation: 1.00        # spot: không vượt 1.0
  low_vol_allocation: 0.95
  mid_vol_allocation_trend_ok: 0.95
  mid_vol_allocation_trend_broken: 0.60
  high_vol_allocation: 0.50   # thấp hơn equities (0.60)
  uncertainty_size_multiplier: 0.5

risk:
  max_position_pct: 100       # một tài sản duy nhất
  max_risk_per_trade_pct: 1.0
  min_order_value_usdt: 5     # > mức tối thiểu 1 USDT của sàn, có biên
  circuit_breaker:
    daily_dd_reduce_pct: 4.0
    daily_dd_halt_pct: 6.0
    weekly_dd_reduce_pct: 10.0
    weekly_dd_halt_pct: 14.0
    peak_dd_halt_pct: 20.0
    day_boundary_utc: "00:00"

backtest:
  is_bars: 365                # in-sample 1 năm
  oos_bars: 182               # out-of-sample 6 tháng
  step_bars: 182
  fill_delay_bars: 1
```

**Bắt buộc:** thêm `.env`, `credentials.yaml`, `data/cache/` vào `.gitignore`.

Phase này **chỉ tạo skeleton** — import, class stub, type hint, docstring. Không implement logic.

---

## PHASE 2: HMM Regime Detection Engine

Implement `core/hmm_engine.py` và `data/feature_engineering.py`.

### Triết lý (không đổi so với bản gốc)

HMM là **bộ phân loại biến động**, không phải bộ dự báo hướng giá. Nó xác định thị trường đang ở môi trường vol thấp, trung bình hay cao. Lớp strategy dùng phân loại đó để đặt tỷ trọng danh mục.

### 2.1 Chọn khung thời gian — quyết định trước khi code

Crypto 24/7 nên "1 ngày" là quy ước, không phải sự thật thị trường. Ba lựa chọn:

- **1D đóng 00:00 UTC** — mặc định. Ít nhiễu nhất, chi phí thấp nhất, HMM ổn định nhất. Nhược điểm: chậm 1 ngày khi vol tăng đột ngột.
- **4H** — nhạy hơn, nhưng bar/năm gấp 6 → phí gấp ~6 → cần ngưỡng rebalance cao hơn nữa.
- **1D nhưng đánh giá lại mỗi 4H** trên rolling window — thoả hiệp. Phức tạp hơn, làm sau khi v1 chạy ổn.

Bắt đầu bằng 1D. Đừng tối ưu khung thời gian trước khi có backtest sạch.

### 2.2 Gaussian HMM với chọn model tự động

- Thử `n_components = [3, 4, 5, 6, 7]`
- Với mỗi ứng viên: train và tính BIC = `-2 * log_likelihood + n_params * log(n_samples)`
- Chọn BIC thấp nhất (model đơn giản nhất giải thích được dữ liệu)
- Mỗi ứng viên chạy `n_init = 10` khởi tạo ngẫu nhiên
- **Log toàn bộ BIC của mọi ứng viên** và cái nào được chọn

Sau khi train, sắp xếp regime theo mean return tăng dần để **gán nhãn** (chỉ để con người đọc):

- 3 regime: `BEAR, NEUTRAL, BULL`
- 4: `CRASH, BEAR, BULL, EUPHORIA`
- 5: `CRASH, BEAR, NEUTRAL, BULL, EUPHORIA`
- 6: `CRASH, STRONG_BEAR, WEAK_BEAR, WEAK_BULL, STRONG_BULL, EUPHORIA`
- 7: `CRASH, STRONG_BEAR, WEAK_BEAR, NEUTRAL, WEAK_BULL, STRONG_BULL, EUPHORIA`

**Quan trọng:** nhãn sắp theo *return*. Lớp strategy sắp theo *volatility* một cách độc lập. Nhãn không điều khiển quyết định giao dịch. Với crypto điều này còn quan trọng hơn equities, vì `EUPHORIA` trong crypto thường là regime vol **cao nhất** — nếu bạn để nhãn dẫn dắt strategy, bot sẽ all-in đúng đỉnh.

### 2.3 Features (đầu vào HMM)

Implement trong `data/feature_engineering.py` dưới dạng **pure function**. Chuẩn hoá **tất cả** feature bằng rolling z-score, **lookback 365** (không phải 252).

Feature chia làm ba tầng. Xây theo thứ tự, mỗi tầng phải chứng minh giá trị bằng ablation test trước khi thêm tầng sau.

#### Tầng 1 — Bắt buộc, chỉ cần OHLCV (bắt đầu ở đây)

- **Returns:** log return 1, 5, 20 chu kỳ
- **Volatility:** realized vol (rolling std 20), vol ratio (5 / 20)
- **Trend:** ADX(14), slope SMA 50
- **Mean reversion:** z-score của RSI(14), khoảng cách tới SMA 200 tính theo % giá
- **Momentum:** ROC 10 và 20
- **Range:** ATR(14) chuẩn hoá theo close

**Thay đổi so với bản gốc — bỏ volume z-score, dùng `trade_count`.** Volume trên sàn crypto bị bóp méo nghiêm trọng bởi wash trading và không so sánh được giữa các sàn. Số lượng giao dịch mỗi bar (`turnover`/`trade_count` có trong kline response của Bybit và Binance) là thước đo hoạt động thật đáng tin hơn nhiều. Giữ nguyên cách xử lý: z-score vs mean 50, cộng slope SMA 10.

#### Tầng 2 — Crypto-native, phản ánh positioning và đòn bẩy

Đây là nhóm feature mà thị trường equity **không có tương đương**. Chúng đo trực tiếp mức độ đòn bẩy và tâm lý của thị trường phái sinh — thứ điều khiển phần lớn các cú vol spike của crypto. Lấy được miễn phí kể cả khi bot chỉ giao dịch spot.

| Feature | Nguồn | Ý nghĩa |
|---|---|---|
| `funding_rate` (8h, làm mượt 3 chu kỳ) | `GET /v5/market/funding-history` | Funding dương cao = long chen chúc = rủi ro long squeeze |
| `funding_zscore` (lookback 90 ngày) | tính từ trên | Chuẩn hoá theo chế độ thị trường |
| `oi_change_pct` (thay đổi OI 24h) | `GET /v5/market/open-interest` | OI tăng + giá tăng = tiền mới vào; OI tăng + giá giảm = đòn bẩy tích tụ |
| `oi_price_divergence` | tính từ trên | Cờ báo sớm cho thanh lý dây chuyền |
| `perp_spot_basis` | mark price perp − index spot | Basis giãn rộng = đầu cơ quá mức |
| `taker_buy_ratio` | tỷ lệ khối lượng taker mua / tổng | Áp lực mua bán chủ động |

**Cảnh báo về dữ liệu:** funding và OI có lịch sử ngắn hơn giá nhiều (Bybit có từ ~2020). Thêm tầng 2 sẽ **rút ngắn khoảng backtest khả dụng**. Bắt buộc chạy backtest hai lần — một lần chỉ tầng 1 trên toàn bộ lịch sử, một lần tầng 1+2 trên khoảng ngắn hơn — rồi so sánh. Nếu tầng 2 không cải thiện Sharpe OOS đủ để bù cho việc mất dữ liệu, bỏ nó.

#### Tầng 3 — Cấu trúc thời gian, tuỳ chọn

- **`weekend_flag`** + tỷ lệ vol cuối tuần / vol ngày thường. Thanh khoản Thứ Bảy–Chủ Nhật mỏng hơn rõ rệt; cùng một mức realized vol mang ý nghĩa khác nhau vào cuối tuần và giữa tuần.
- **`cycle_position`** — số ngày kể từ lần halving gần nhất, chuẩn hoá về [0, 1]. Chu kỳ 4 năm chi phối BTC mạnh hơn bất kỳ nhịp nào ở equities. **Dùng hết sức thận trọng:** bạn chỉ có ~2–3 chu kỳ trong dữ liệu, nên feature này gần như chắc chắn sẽ overfit. Nếu thêm, phải kiểm chứng bằng cách train trên chu kỳ 2016–2020 và test trên 2020–2024, không được dùng walk-forward thông thường.

#### Kỷ luật chọn feature

`covariance_type="full"` làm số tham số tăng theo **bậc hai** với số feature. Với 12 feature và 5 regime, model đã có hơn 400 tham số. BIC sẽ phạt điều này, nhưng bạn vẫn có thể overfit trong phạm vi BIC cho phép.

Quy trình bắt buộc: bắt đầu với tầng 1, ghi lại BIC và Sharpe OOS làm chuẩn. Thêm từng feature một. **Chỉ giữ nếu cải thiện Sharpe OOS ít nhất 0.1 và không làm xấu BIC.** Ghi toàn bộ kết quả ablation vào `feature_ablation.csv` — đây là bằng chứng cho thấy bạn đã chọn feature có kỷ luật, không phải chọn theo cảm tính.

### 2.4 Training

- `hmmlearn.GaussianHMM`, `covariance_type="full"`
- Tối thiểu 730 bar (2 năm)
- Retrain theo cửa sổ mở rộng, chu kỳ cấu hình được (mặc định 7 ngày)
- Lưu model bằng pickle + metadata: `n_regimes`, `bic`, `training_date`, `labels`, `feature_list`, `data_hash`
- Log: likelihood, BIC, hội tụ hay không, số vòng lặp

### 2.5 Phát hiện regime — KHÔNG ĐƯỢC CÓ LOOK-AHEAD BIAS

**Đây là chi tiết kỹ thuật quan trọng nhất của toàn bộ dự án.**

**Không dùng `model.predict()`.** `predict()` chạy Viterbi, xử lý toàn bộ chuỗi và sửa lại các state quá khứ bằng dữ liệu tương lai. Đó là look-ahead bias, làm backtest đẹp một cách giả tạo.

Thay vào đó implement **forward algorithm** (filtered inference):

```python
def predict_regime_filtered(self, features_up_to_now):
    """
    Tính P(state_t | observations_1:t) bằng forward algorithm.
    CHỈ dùng dữ liệu quá khứ và hiện tại. Không có dữ liệu tương lai.
    """
    # Dùng startprob_, transmat_, means_, covars_ của model
    # 1. alpha_0 = startprob * emission_prob(obs_0)
    # 2. alpha_t = (alpha_{t-1} @ transmat) * emission_prob(obs_t)
    # 3. Chuẩn hoá ở mỗi bước (làm việc trong log space)
    # 4. alpha_T = phân phối filtered tại thời điểm hiện tại
    # Cache alpha trước đó để tối ưu trong vòng lặp live/backtest
```

**Test bắt buộc — `tests/test_look_ahead.py`:**

```python
def test_no_look_ahead_bias():
    """
    Chạy predict_regime_filtered trên dữ liệu tới bar N.
    Chạy lại trên dữ liệu tới bar N+50, cắt lấy kết quả tại bar N.
    Hai kết quả PHẢI giống hệt nhau.
    Nếu khác → có look-ahead bias → dừng lại, sửa trước khi đi tiếp.
    """
```

Chạy thêm cùng phép so sánh với `model.predict()` để thấy nó **fail** — đó là bằng chứng test có tác dụng.

### 2.6 Bộ lọc ổn định regime

- Chỉ "xác nhận" đổi regime sau khi trạng thái mới duy trì N bar (mặc định 3)
- Trong giai đoạn chuyển tiếp: giữ regime cũ, giảm size 25%
- Theo dõi flicker rate (số lần đổi / 20 bar)
- Nếu flicker rate > ngưỡng (mặc định 4): bật chế độ uncertainty

### 2.7 Method bổ sung

`predict_regime_proba()`, `get_regime_stability()`, `get_transition_matrix()`, `detect_regime_change()`, `get_regime_flicker_rate()`, `is_flickering()`

### 2.8 Metadata

```python
@dataclass
class RegimeInfo:
    regime_id: int
    regime_name: str
    expected_return: float
    expected_volatility: float
    recommended_strategy_type: str
    max_allocation_pct: float      # thay cho max_leverage_allowed
    min_confidence_to_act: float

@dataclass
class RegimeState:
    label: str
    state_id: int
    probability: float
    state_probabilities: np.ndarray
    timestamp: datetime
    is_confirmed: bool
    consecutive_bars: int
```

---

## PHASE 3: Volatility-Based Allocation Strategy

Implement `core/regime_strategies.py`.

### Luận điểm — và điểm cần kiểm chứng lại cho crypto

Bản gốc lập luận: cổ phiếu tăng khoảng 70% thời gian trong giai đoạn vol thấp; các đợt sụt mạnh nhất tụ lại ở các cú vol spike; nên chỉ cần giảm tỷ trọng khi vol cao là compounding có lợi.

**Với crypto, nửa đầu của lập luận này chưa được chứng minh.** BTC không có drift cấu trúc như equity index và đã nhiều lần sụt 70–80%. Nửa sau — drawdown lớn tụ ở vùng vol cao — thì vẫn đúng và mạnh hơn cả equities.

Kết luận: edge từ vol-sizing nhiều khả năng vẫn tồn tại, nhưng bạn **phải chạy walk-forward ở Phase 4 rồi mới tin**. Nếu Phase 4 cho thấy vol-sizing không đánh bại buy-and-hold sau phí, dừng dự án hoặc đổi luận điểm. Đừng đi tiếp tới live.

### LUÔN LONG. KHÔNG BAO GIỜ SHORT.

Giữ nguyên từ bản gốc, và với crypto lý do còn mạnh hơn: hồi phục hình chữ V ở crypto nhanh và bạo lực hơn equities, HMM luôn chậm 2–3 bar. Phản ứng đúng với vol cao là **giảm tỷ trọng**, không phải đảo chiều.

### Ba lớp strategy (theo thứ hạng volatility)

**1. `LowVolBullStrategy`** — 1/3 regime có vol thấp nhất
- Direction: LONG
- Allocation: **95%**
- Leverage: **1.0x** (spot không có margin)
- Stop: `max(price - 3*ATR, EMA50 - 0.5*ATR)`

**2. `MidVolCautiousStrategy`** — 1/3 giữa
- Direction: LONG
- Nếu `price > EMA50`: allocation **95%** (xu hướng còn nguyên)
- Nếu `price < EMA50`: allocation **60%** (xu hướng gãy)
- Stop: `EMA50 - 0.5*ATR`

**3. `HighVolDefensiveStrategy`** — 1/3 vol cao nhất
- Direction: LONG (KHÔNG short)
- Allocation: **50%** — thấp hơn mức 60% của equities, vì đuôi phân phối của crypto dày hơn nhiều
- Stop: `EMA50 - 1.0*ATR` (rộng hơn cho điều kiện biến động)

### Ánh xạ thứ hạng vol → strategy

```
position = rank / (n_regimes - 1)     # 0.0 = vol thấp nhất, 1.0 = cao nhất

position <= 0.33  -> LowVolBullStrategy
position >= 0.67  -> HighVolDefensiveStrategy
else              -> MidVolCautiousStrategy
```

### StrategyOrchestrator

- Nhận `regime_infos` từ HMM
- Sắp theo `expected_volatility` tăng dần để tính `vol_rank`
- Ánh xạ `regime_id → vol_rank → strategy class`
- **Phép sắp này độc lập hoàn toàn với phép sắp theo return dùng để gán nhãn.** Nhãn `BULL` không có nghĩa là vol thấp. Orchestrator bỏ qua nhãn.

### Confidence và uncertainty

- Ngưỡng confidence tối thiểu: 0.55
- Bật uncertainty khi: `prob < threshold` HOẶC `is_flickering == True`
- Trong uncertainty: **giảm một nửa** allocation mục tiêu
- Nối `"[UNCERTAINTY — size halved]"` vào `reasoning`

### Rebalancing

Chỉ rebalance khi allocation mục tiêu lệch **>25%** so với hiện tại.

Ngưỡng này cao gấp 2.5 lần bản gốc vì chi phí round-trip là 0.2% phí + 0.06% slippage ≈ **0.26%**, so với ~0.1% ở equities miễn phí commission. Ít giao dịch = ít trượt giá = kết quả thực tế tốt hơn.

**Việc cần làm ở Phase 4:** quét ngưỡng rebalance trong khoảng 10–40% và chọn theo Sharpe OOS, không phải theo trực giác. Đây là một trong số ít tham số đáng tối ưu.

### Signal dataclass

```python
@dataclass
class Signal:
    symbol: str
    direction: Direction              # LONG | FLAT
    confidence: float
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Optional[Decimal]
    target_allocation_pct: Decimal    # 0.50 → 0.95
    leverage: Decimal                 # luôn 1.0 ở spot
    regime_id: int
    regime_name: str
    regime_probability: float
    timestamp: datetime
    reasoning: str
    strategy_name: str
    metadata: dict
```

Giữ alias tương thích ngược: `CrashDefensiveStrategy = HighVolDefensiveStrategy`, `BearTrendStrategy = HighVolDefensiveStrategy`, `MeanReversionStrategy = MidVolCautiousStrategy`, `BullTrendStrategy = LowVolBullStrategy`, `EuphoriaCautiousStrategy = LowVolBullStrategy`. Và một dict `LABEL_TO_STRATEGY` phủ mọi nhãn có thể.

---

## PHASE 3.5: Structural Trend Gate — MỚI, không có trong bản gốc

Implement `core/trend_gate.py`.

### Vấn đề cần giải quyết

Toàn bộ luận điểm của spec gốc dựa trên một quan sát về equities: **giai đoạn vol thấp gắn liền với xu hướng tăng.** Ở chỉ số cổ phiếu điều này đúng vì có drift cấu trúc đi lên — doanh nghiệp tạo ra lợi nhuận, chỉ số loại bỏ công ty yếu, lạm phát đẩy giá danh nghĩa lên.

**BTC không có cơ chế nào tương tự.** Và hệ quả cụ thể là chế độ thất bại sau:

> Năm 2022, BTC giảm từ ~48.000 xuống ~16.000 trong 12 tháng. Nhưng đó không phải một cú sập — đó là chuỗi bào mòn kéo dài, và realized vol **giảm dần** qua phần lớn giai đoạn đó. Một HMM phân loại theo volatility sẽ đọc nhiều đoạn trong 2022 là "vol thấp" và strategy sẽ đặt tỷ trọng 95%.

Đây không phải rủi ro lý thuyết. Nó là kịch bản có thật, đã xảy ra, và thiết kế gốc không có gì chặn được nó. Circuit breaker ở Phase 5 sẽ bắt được các cú sụt nhanh, nhưng một đợt giảm 20% trải qua 4 tháng không kích hoạt breaker nào cả — nó chỉ đơn giản là bào mòn tài khoản.

### Thiết kế

Một bộ lọc xu hướng **chậm, đơn giản, hoàn toàn độc lập với HMM**. Nó không tạo tín hiệu vào lệnh. Nó chỉ đặt **trần** cho tỷ trọng.

```python
class StructuralTrendGate:
    """
    Độc lập hoàn toàn với HMM. Chỉ có quyền GIẢM tỷ trọng.
    Đơn giản một cách có chủ đích — càng ít tham số càng ít overfit.
    """
    def get_allocation_cap(self, bars) -> Decimal: ...
    def get_structure_state(self, bars) -> StructureState: ...
```

Hai đầu vào duy nhất, tính trên bar ngày:

- `price_vs_sma200` — giá đóng cửa so với SMA 200
- `sma200_slope_30` — độ dốc của SMA 200 trong 30 ngày qua

Ba trạng thái:

| Trạng thái | Điều kiện | Trần tỷ trọng |
|---|---|---|
| `BULL_STRUCTURE` | `price > SMA200` **và** `slope > 0` | **100%** (để HMM quyết định) |
| `TRANSITION` | hai điều kiện mâu thuẫn nhau | **60%** |
| `BEAR_STRUCTURE` | `price < SMA200` **và** `slope < 0` | **30%** |

Kết hợp với các tầng khác theo nguyên tắc §0.2:

```python
final_allocation = min(hmm_allocation, trend_gate_cap, risk_manager_cap)
```

### Chống nhiễu

SMA200 crossover hay bị whipsaw. Áp dụng:

- **Buffer 2%:** chỉ đổi trạng thái khi giá vượt SMA200 quá 2% theo hướng tương ứng. Nằm trong dải ±2% thì giữ trạng thái cũ.
- **Xác nhận 5 ngày:** trạng thái mới phải duy trì 5 bar mới có hiệu lực. Chậm hơn bộ lọc 3 bar của HMM một cách có chủ đích — đây là tầng cấu trúc, nó *nên* chậm.
- **Chỉ siết một chiều trong ngày:** trần có thể giảm ngay lập tức, nhưng chỉ được nới lên sau khi xác nhận đủ. Bất đối xứng có chủ đích.

### Cố tình giữ đơn giản

Bạn sẽ bị cám dỗ thêm tham số vào tầng này — nhiều đường MA hơn, ngưỡng động, thêm xác nhận momentum. **Đừng.** Giá trị của tầng này nằm ở chỗ nó gần như không thể overfit: hai đầu vào, ba trạng thái, ba tham số. Nếu nó cần điều chỉnh tinh vi mới hoạt động, nghĩa là nó không hoạt động.

### Nghiệm thu bắt buộc ở Phase 4

Chạy backtest **có** và **không có** trend gate, so sánh riêng ba giai đoạn:

- **2022 (bear kéo dài, vol giảm)** — trend gate phải cải thiện rõ rệt. Đây là lý do tồn tại của nó.
- **2020–2021 (bull mạnh)** — trend gate sẽ làm giảm lợi nhuận vì cắt bớt tỷ trọng ở các nhịp điều chỉnh. Cần đo mức thiệt hại này.
- **Toàn kỳ** — Calmar ratio phải cải thiện, kể cả khi tổng lợi nhuận giảm.

**Tiêu chí chấp nhận:** giữ trend gate nếu nó cải thiện max drawdown ít nhất 25% với chi phí không quá 20% CAGR. Nếu không đạt, ghi lại kết quả và bỏ — nhưng phải chạy thử nghiệm này, không được bỏ qua vì trực giác.

---

## PHASE 4: Walk-Forward Backtesting & Validation

Implement `backtest/backtester.py`, `performance.py`, `cost_model.py`, `stress_test.py`.

Đây là backtester **theo allocation**, không theo từng lệnh vào/ra. Mỗi bar nó đặt một tỷ trọng danh mục mục tiêu dựa trên regime vol phát hiện được, và rebalance khi tỷ trọng lệch đủ nhiều.

### 4.0 Dữ liệu lịch sử — làm trước tiên

**Vấn đề:** Bybit spot BTC/USDT chỉ có dữ liệu từ khoảng 2021. HMM cần tối thiểu 2 năm để train, và walk-forward cần nhiều cửa sổ. Với chỉ ~5 năm dữ liệu bạn được rất ít cửa sổ OOS độc lập.

**Giải pháp:** dùng CCXT tải OHLCV BTC/USDT từ **Binance** (có từ 2017) cho backtest, execute trên Bybit. Chênh lệch giá giữa hai sàn ở BTC/USDT là không đáng kể so với sai số của chính chiến lược.

`data/history_loader.py` phải:

- Tải qua CCXT có phân trang (mỗi request tối đa ~1000 bar)
- Cache ra parquet, chỉ tải phần thiếu ở lần chạy sau
- Kiểm tra tính toàn vẹn: không thiếu bar, không trùng timestamp, không bar có volume = 0
- Ghi lại nguồn dữ liệu và khoảng thời gian vào metadata của mỗi backtest

**Cảnh báo:** ~5–8 năm dữ liệu BTC bao gồm 2 chu kỳ. Đó là một mẫu rất nhỏ để kết luận về chế độ thị trường. Hãy coi kết quả backtest là *bằng chứng yếu*, không phải sự thật.

### 4.1 Walk-forward engine

Cửa sổ trượt:
- **In-sample (IS):** 365 bar để train HMM + chọn model bằng BIC
- **Out-of-sample (OOS):** 182 bar để đánh giá
- **Step:** 182 bar

Với mỗi cửa sổ:

1. Train HMM trên IS (chọn model bằng BIC)
2. Tính thứ hạng vol từ `regime_infos` của model vừa train
3. Đi qua OOS từng bar một:
   - Tính feature **chỉ từ dữ liệu tới bar hiện tại**
   - Chạy HMM filtered (forward algorithm)
   - Lấy signal: allocation mục tiêu theo thứ hạng vol
   - Nếu allocation lệch >25% so với hiện tại → rebalance
   - Mark to market: `equity = cash + qty * price`
4. Ghi lại regime dự đoán và equity ở mỗi bar
5. Ghi một "trade" mỗi lần allocation thay đổi

### 4.2 Toán allocation — phải chính xác tuyệt đối

**Đây là chỗ bản gốc sẽ sinh bug nếu copy nguyên.**

Bản gốc dùng `target_shares = int(...)` vì cổ phiếu là số nguyên. **Crypto chia lẻ được.** Dùng `int()` với BTC ở giá $100k sẽ làm tròn mọi vị thế dưới $100k về 0.

```python
from decimal import Decimal, ROUND_DOWN

equity = cash + qty * current_price

target_notional = equity * target_allocation
target_qty_raw  = target_notional / current_price

# Làm tròn XUỐNG theo basePrecision của sàn (BTC/USDT spot: 6 chữ số thập phân)
target_qty = Decimal(target_qty_raw).quantize(
    base_precision, rounding=ROUND_DOWN
)

delta = target_qty - current_qty

# Bỏ qua nếu giá trị lệnh dưới mức tối thiểu — tránh lệnh bị sàn từ chối
if abs(delta) * current_price < min_order_value_usdt:
    delta = Decimal(0)

fee       = abs(delta) * current_price * taker_fee_pct
slip_cost = abs(delta) * current_price * slippage_pct

cash -= delta * current_price + fee + slip_cost
qty   = current_qty + delta
```

**Không có margin ở spot.** `target_allocation` không bao giờ vượt 1.0, `cash` không bao giờ âm. Nếu backtest cho ra cash âm, có bug.

Dùng `Decimal` cho toàn bộ số lượng và giá trong đường thực thi. `float` cho feature và thống kê thì không sao.

### 4.3 Mô hình chi phí (`cost_model.py`) — tách riêng, không nhét vào backtester

Tách ra thành module riêng để có thể unit test và quét tham số:

```python
class CostModel:
    def rebalance_cost(self, delta_qty, price) -> Decimal:
        """Phí + slippage cho một lần rebalance."""

    def total_cost_report(self, trade_log) -> dict:
        """Tổng phí đã trả, % lợi nhuận gộp bị phí ăn mất."""
```

**Báo cáo bắt buộc trong mọi backtest:** tổng phí đã trả tính theo USDT **và** theo % của lợi nhuận gộp. Nếu phí ăn hơn 30% lợi nhuận gộp, chiến lược đang giao dịch quá nhiều — tăng ngưỡng rebalance hoặc chuyển sang khung thời gian dài hơn.

### 4.4 Mô phỏng thực tế

- Slippage: 0.03% mỗi lần rebalance (cấu hình được)
- Phí: 0.10% mỗi chiều, giả định luôn là taker (bảo thủ)
- Ngưỡng rebalance: 25%
- Fill delay: 1 bar (signal ở bar N → rebalance ở open bar N+1)
- Không có stop từng lệnh trong backtester (stop chỉ dùng ở live)

### 4.5 Chỉ số hiệu suất (`performance.py`)

**Cốt lõi:** Total return, CAGR, Sharpe (annualize bằng **√365**, không phải √252), Sortino, Calmar, max drawdown (% và số ngày), win rate, avg win/loss, profit factor, tổng số giao dịch, thời gian nắm giữ trung bình.

**Theo regime** — dạng bảng:

```
Regime | % Time In | Return Contribution | Avg P&L | Win Rate | Sharpe
```

**Theo confidence bucket** (<50%, 50–60%, 60–70%, 70%+): nếu nhóm confidence cao vượt trội nhóm thấp → HMM có giá trị thật. Nếu không → HMM chỉ là bộ tạo nhiễu đắt tiền.

**So sánh benchmark** (chạy tự động với cờ `--compare`):

1. **Buy-and-hold BTC** — benchmark quan trọng nhất. Nếu không đánh bại được cái này sau phí thì dừng.
2. **200 SMA trend** — long khi trên SMA200, cash khi dưới. Đơn giản, mạnh bất ngờ với BTC.
3. **Random allocation** — đổi allocation ngẫu nhiên cùng tần suất, cùng rule sizing. 100 seed, báo cáo mean/std. Nếu chiến lược của bạn nằm trong 1 std của random thì bạn không có edge.
4. **Vol-target tĩnh** — nhắm vol danh mục cố định bằng realized vol, không dùng HMM. **MỚI, và là benchmark khắt khe nhất.** Nếu HMM không đánh bại vol-targeting đơn giản, toàn bộ tầng HMM là phức tạp thừa.

Benchmark thứ 4 là bài kiểm tra thật sự. Hãy chạy nó sớm.

**Trường hợp xấu nhất:** ngày tệ nhất, tuần tệ nhất, tháng tệ nhất, chuỗi thua dài nhất, thời gian dưới nước lâu nhất.

**Output:** bảng `rich` ra terminal + `equity_curve.csv`, `trade_log.csv`, `regime_history.csv`, `benchmark_comparison.csv`, `cost_report.csv`.

### 4.6 Stress testing (`stress_test.py`)

**a. Crash injection** — chèn gap -15% đến -40% (crypto, không phải -5% đến -15% như equities) tại 10 điểm ngẫu nhiên. 100 lần Monte Carlo. Báo cáo: mean max loss, worst case, % số lần circuit breaker kích hoạt.

**b. Gap risk** — chèn gap 2–5× ATR. Ở crypto gap xảy ra *trong phiên* (flash crash, thanh lý dây chuyền), không phải qua đêm. Báo cáo tổn thất kỳ vọng vs thực tế.

**c. Regime misclassification** — cố tình xáo trộn nhãn regime. Kiểm chứng risk management vẫn giới hạn được thiệt hại dù regime sai hoàn toàn. Nếu hệ thống nổ tung → risk management chưa đủ độc lập.

**d. Exchange outage** — MỚI. Mô phỏng sàn không phản hồi trong 1–6 giờ giữa lúc cần rebalance. Kiểm chứng hệ thống hồi phục đúng trạng thái, không đặt trùng lệnh.

### 4.7 CLI

```bash
python main.py backtest --start 2018-01-01 --end 2025-12-31
python main.py backtest --start 2018-01-01 --end 2025-12-31 --compare
python main.py backtest --stress-test
python main.py backtest --sweep rebalance_threshold --range 10,40,5
python main.py backtest --no-trend-gate            # so sánh có/không Phase 3.5
python main.py backtest --period 2022              # kiểm tra riêng bear kéo dài
python main.py backtest --bar-offset 0,6,12,18     # kiểm tra độ nhạy mốc đóng bar
python main.py backtest --symbol ETHUSDT           # kiểm định ngoài mẫu
python main.py backtest --ablation                 # quét feature từng cái một
```

### 4.8 Kiểm định chống overfit — MỚI, bắt buộc

Bạn có khoảng 8 năm dữ liệu BTC, chứa **2 chu kỳ**. Đó là cỡ mẫu cực nhỏ. Walk-forward thông thường tạo cảm giác an toàn giả — các cửa sổ OOS chồng lấn về mặt chế độ thị trường và không thực sự độc lập.

Bốn bài kiểm định sau không có trong spec gốc. Chạy đủ cả bốn trước khi tin bất kỳ con số nào.

**a. Kiểm tra riêng giai đoạn 2022** (`--period 2022`)
Đây là bài test khắc nghiệt nhất: bear kéo dài với vol giảm dần. Bất kỳ hệ thống nào phân loại theo volatility đều dễ thất bại ở đây. Báo cáo riêng: return, max DD, % thời gian ở mỗi mức allocation, và so với buy-and-hold cùng kỳ.
**Nếu hệ thống lỗ nặng hơn buy-and-hold trong 2022, thiết kế có lỗi cơ bản, không phải cần chỉnh tham số.**

**b. Độ nhạy với mốc đóng bar** (`--bar-offset`)
Mốc 00:00 UTC là quy ước tuỳ ý — thị trường crypto không đóng cửa. Chạy lại toàn bộ backtest với bar đóng lúc 00:00, 06:00, 12:00, 18:00 UTC.
**Kết quả bốn lần chạy phải tương đương nhau.** Nếu Sharpe dao động mạnh (chênh lệch > 0.3), bạn đang khớp vào nhiễu của một mốc thời gian cụ thể chứ không phải nắm được cấu trúc thị trường. Đây là bài test rẻ và phát hiện overfit hiệu quả bất ngờ.

**c. ETH làm tập kiểm định ngoài mẫu** (`--symbol ETHUSDT`)
Train và tune **hoàn toàn** trên BTC. Sau khi đã chốt mọi tham số, chạy đúng cấu hình đó trên ETH, **không chỉnh gì cả**.
ETH tương quan cao với BTC nên đây không phải kiểm định độc lập hoàn hảo, nhưng nó bắt được một dạng overfit cụ thể: tham số khớp vào đặc thù lịch sử giá của riêng BTC. Nếu hệ thống sập trên ETH, các tham số của bạn là kết quả của việc dò dữ liệu.

**d. Ablation feature** (`--ablation`)
Chạy backtest với từng tổ hợp feature (tầng 1, tầng 1+2, tầng 1+2+3, và từng feature bỏ ra một). Xuất `feature_ablation.csv`.
Nếu bỏ một feature ra mà kết quả không xấu đi, feature đó chỉ là tham số thừa làm tăng rủi ro overfit. Bỏ nó.

### 4.9 Tiêu chí đi tiếp — viết ra trước khi chạy

Ghi các ngưỡng này vào README **trước khi** chạy backtest đầu tiên. Quyết định tiêu chí sau khi nhìn kết quả là hình thức tự lừa dối phổ biến nhất trong xây dựng hệ thống giao dịch.

Hệ thống chỉ được đi tiếp sang Phase 5 nếu **đồng thời** thoả:

1. Sharpe OOS > 1.0 sau toàn bộ chi phí
2. Đánh bại buy-and-hold BTC về **Calmar ratio** (không nhất thiết về tổng lợi nhuận)
3. Đánh bại vol-targeting tĩnh về Sharpe ít nhất 0.2
4. Nằm ngoài 2 độ lệch chuẩn của phân phối benchmark ngẫu nhiên
5. Trong 2022 không lỗ nặng hơn buy-and-hold
6. Sharpe của bốn lần chạy bar-offset chênh nhau không quá 0.3
7. Trên ETH không tune: Sharpe > 0.5
8. Phí chiếm dưới 30% lợi nhuận gộp

Không thoả đủ 8 điều kiện thì không xây tầng thực thi. Ghi lại kết quả, quay lại Phase 2–3, hoặc dừng dự án.

---

## PHASE 5: Risk Management Layer

Implement `core/risk_manager.py`.

Risk manager hoạt động **hoàn toàn độc lập với HMM**. Kể cả khi HMM hỏng hoàn toàn, circuit breaker vẫn bắt được drawdown dựa trên P&L thực tế. Phòng thủ nhiều lớp. Risk manager có **quyền phủ quyết tuyệt đối** với mọi signal.

### 5.1 Giới hạn danh mục

- Max allocation: **100%** (một tài sản, spot, không leverage)
- Min cash buffer: **5%** — giữ USDT để trả phí và xử lý lệnh khớp một phần
- Max giao dịch mỗi ngày: **6** (thấp hơn nhiều so với 20 của equities — phí cao hơn)
- Max leverage: **1.0x** (spot, cứng)

### 5.2 Circuit breakers

Kích hoạt theo **P&L thực tế**, độc lập với regime. Ranh giới ngày dùng **00:00 UTC** (không có giờ đóng cửa thị trường).

| Điều kiện | Hành động |
|---|---|
| Daily DD > 4% | Giảm mọi size 50% cho phần còn lại của ngày |
| Daily DD > 6% | Đóng toàn bộ vị thế, dừng hết ngày |
| Weekly DD > 10% | Giảm mọi size 50% cho phần còn lại của tuần |
| Weekly DD > 14% | Đóng toàn bộ, dừng hết tuần |
| Peak DD > 20% | **Dừng toàn bộ giao dịch**, ghi file `trading_halted.lock`, phải xoá thủ công mới chạy lại |

**Các con số này là điểm khởi đầu, không phải kết luận.** Sau khi có backtest, hãy tính phân vị của phân phối lợi nhuận ngày trong dữ liệu của bạn và đặt ngưỡng "giảm size" ở khoảng phân vị 2–3%, ngưỡng "dừng" ở phân vị 0.5%. Ngưỡng đặt bằng cảm tính sẽ hoặc bắn liên tục hoặc không bao giờ bắn.

Log mọi lần kích hoạt kèm: loại breaker, DD thực tế, equity, vị thế đã đóng, **regime của HMM tại thời điểm đó** (để theo dõi HMM có sai không).

### 5.3 Rủi ro vị thế

- Mọi vị thế **bắt buộc** có stop loss — hệ thống từ chối lệnh không có stop
- Max risk mỗi giao dịch: 1% danh mục
- `position_size = (equity * 0.01) / abs(entry - stop_loss)`, cap theo max của regime rồi cap theo max danh mục
- Giá trị lệnh tối thiểu: 5 USDT (trên mức 1 USDT của sàn, có biên an toàn)
- **Bỏ rule gap qua đêm** — không tồn tại ở thị trường 24/7

### 5.4 Rule đặc thù crypto — MỚI

- **Kiểm tra spread:** từ chối nếu bid-ask spread > 0.10% (BTC/USDT bình thường < 0.02%; spread rộng = thanh khoản đang hỏng)
- **Vệ sinh stablecoin:** nếu USDT lệch peg quá 0.5% so với USD, tạm dừng giao dịch mới và cảnh báo
- **Cảnh báo tập trung sàn:** log tổng số dư đang giữ trên sàn. Rủi ro đối tác của sàn là có thật và không được mô hình hoá ở đâu khác trong hệ thống này.

### 5.5 Kiểm tra lệnh

Kiểm tra số dư khả dụng, trạng thái giao dịch của symbol, spread. Chặn lệnh trùng (cùng symbol + hướng trong 60 giây). Log mọi lần từ chối kèm lý do có cấu trúc.

### 5.6 Correlation check

Bỏ ở v1 (một tài sản). **Giữ nguyên interface** `check_correlation(signal, positions) -> RiskDecision` trả về approved, để khi mở rộng đa tài sản không phải sửa kiến trúc.

### 5.7 Interface

```python
class RiskManager:
    def validate_signal(self, signal, portfolio_state) -> RiskDecision: ...

@dataclass
class RiskDecision:
    approved: bool
    modified_signal: Optional[Signal]
    rejection_reason: Optional[str]
    modifications: list[str]

@dataclass
class PortfolioState:
    equity: Decimal
    cash: Decimal
    available_balance: Decimal
    positions: dict
    daily_pnl: Decimal
    weekly_pnl: Decimal
    peak_equity: Decimal
    drawdown: Decimal
    circuit_breaker_status: dict
    flicker_rate: float

class CircuitBreaker:
    def check(self) -> BreakerStatus: ...
    def update(self, pnl) -> None: ...
    def reset_daily(self) -> None: ...   # 00:00 UTC
    def reset_weekly(self) -> None: ...  # Thứ Hai 00:00 UTC
    def get_history(self) -> list: ...
```

Mọi ngưỡng đọc từ `settings.yaml`.

---

## PHASE 6: Bybit Integration

### 6.1 `broker/base.py` — ABC, viết trước tiên

Đây là thay đổi kiến trúc quan trọng nhất so với bản gốc. Bản gốc gắn chặt vào Alpaca. Bản này định nghĩa interface trừu tượng trước, để đổi sàn hoặc chuyển spot → perps không phải viết lại tầng trên.

```python
class ExchangeClient(ABC):
    @abstractmethod
    def get_balance(self) -> Balance: ...
    @abstractmethod
    def get_positions(self) -> list[Position]: ...
    @abstractmethod
    def get_instrument_rules(self, symbol) -> InstrumentRules: ...
    @abstractmethod
    def get_historical_klines(self, symbol, interval, start, end) -> pd.DataFrame: ...
    @abstractmethod
    def submit_order(self, order: OrderRequest) -> OrderResult: ...
    @abstractmethod
    def cancel_order(self, order_id) -> bool: ...
    @abstractmethod
    def get_open_orders(self) -> list[Order]: ...
    @abstractmethod
    def subscribe_klines(self, symbol, interval, callback) -> None: ...
    @abstractmethod
    def subscribe_executions(self, callback) -> None: ...
```

`BybitClient` và `CCXTClient` đều implement interface này. Tầng strategy và risk **không bao giờ** import trực tiếp `pybit`.

### 6.2 `broker/instrument_rules.py` — MỚI, bắt buộc

Không có đối tượng tương đương ở equities. Lấy từ `/v5/market/instruments-info` khi khởi động và cache:

```python
@dataclass
class InstrumentRules:
    symbol: str
    base_precision: Decimal    # bước số lượng, BTC/USDT spot: 0.000001
    quote_precision: Decimal
    tick_size: Decimal         # bước giá
    min_order_qty: Decimal
    min_order_amt: Decimal     # tối thiểu tính theo USDT (BTC/USDT: 1 USDT)
    max_order_qty: Decimal

    def round_qty(self, qty: Decimal) -> Decimal:
        """Làm tròn XUỐNG theo base_precision. Luôn xuống, không bao giờ lên."""

    def round_price(self, price: Decimal) -> Decimal:
        """Làm tròn theo tick_size."""

    def is_valid_order(self, qty, price) -> tuple[bool, str]:
        """Kiểm tra trước khi gửi. Rẻ hơn nhiều so với bị sàn từ chối."""
```

**`tests/test_precision.py` phải cover:** làm tròn ở biên, số lượng cực nhỏ, số lượng vượt max, giá trị lệnh ngay dưới mức tối thiểu. Đây là nguồn lỗi runtime phổ biến nhất khi chuyển từ equities sang crypto.

### 6.3 `broker/bybit_client.py`

- Bọc SDK `pybit` (Bybit v5 unified API)
- Credentials từ `.env`, **không bao giờ hardcode**, `.env` trong `.gitignore`
- Testnet: `api-testnet.bybit.com` — **MẶC ĐỊNH**
- Mainnet: `api.bybit.com`
- Nếu `testnet: false`, bắt buộc xác nhận:
  `⚠️ LIVE TRADING VỚI TIỀN THẬT. Gõ 'YES I UNDERSTAND THE RISKS' để xác nhận.`
- Rate limit: 600 request / 5 giây / IP. Implement token bucket ở tầng client, không chờ tới khi bị sàn chặn.
- Xử lý `recv_window` và lệch đồng hồ — đồng bộ thời gian với server lúc khởi động, cảnh báo nếu lệch > 1 giây. Đây là nguyên nhân số 1 gây lỗi auth với Bybit.
- Health check khi khởi động, tự kết nối lại với exponential backoff

### 6.4 `broker/order_executor.py`

- `submit_order(signal)`: lệnh **LIMIT** mặc định, đặt ±0.05% quanh giá hiện tại; huỷ sau 30 giây nếu chưa khớp; tuỳ chọn đặt lại bằng lệnh MARKET
- **`orderLinkId` — quan trọng:** Bybit hỗ trợ client order ID. Dùng nó làm khoá idempotency. Sinh deterministic từ `(symbol, bar_timestamp, target_allocation)`. Nếu bot restart giữa chừng và gửi lại cùng một lệnh, sàn sẽ từ chối trùng thay vì đặt hai lần. **Không có cơ chế này, một lần crash-restart có thể nhân đôi vị thế.**
- Xử lý **khớp một phần** — ở crypto phổ biến hơn equities. Theo dõi qty đã khớp, chỉ rebalance phần còn thiếu.
- `modify_stop(symbol, new_stop)`: chỉ được siết chặt, không bao giờ nới rộng
- `cancel_order()`, `close_position()`, `close_all_positions()`
- `trade_id` duy nhất nối `signal → risk_decision → order → fill`

**Lưu ý về stop loss ở spot:** Bybit spot hỗ trợ lệnh conditional/TP-SL, nhưng cơ chế khác perps. Kiểm chứng kỹ trên testnet trước. Nếu không tin cậy được, implement stop ở phía bot (theo dõi giá qua WebSocket, gửi lệnh market khi thủng) — nhưng phải hiểu rằng như vậy stop sẽ mất tác dụng khi bot offline. Ghi rõ đánh đổi này vào README.

### 6.5 `broker/position_tracker.py`

- Đăng ký WebSocket private stream để nhận thông báo khớp lệnh tức thì
- Cập nhật `PortfolioState` và `CircuitBreaker` sau mỗi lần khớp
- Theo dõi từng vị thế: thời gian/giá vào, giá hiện tại, P&L chưa thực hiện, mức stop, thời gian nắm giữ, regime lúc vào vs hiện tại
- **Đối soát khi khởi động:** so số dư thực tế trên sàn với trạng thái đã lưu. Ở thị trường 24/7 bot có thể offline trong lúc thị trường chạy — đây không phải trường hợp hiếm mà là chuyện thường ngày. Nếu lệch, tin sàn và ghi log cảnh báo.

### 6.6 `data/market_data.py`

- `get_historical_klines()` có phân trang
- `subscribe_klines()` qua WebSocket public
- `get_latest_kline()`, `get_orderbook()` (để kiểm tra spread)
- **Heartbeat WebSocket:** Bybit ngắt kết nối im lặng. Implement ping/pong và tự kết nối lại. Nếu không nhận dữ liệu quá 2× chu kỳ bar → coi như mất feed, tạm dừng signal, cảnh báo.

---

## PHASE 7: Main Loop & Orchestration

Implement `main.py`.

### Khởi động

1. Load config, kết nối Bybit, xác minh tài khoản
2. **Đồng bộ thời gian với server**, cảnh báo nếu lệch > 1s
3. Lấy và cache `InstrumentRules`
4. Load hoặc train HMM (retrain nếu model cũ hơn 7 ngày hoặc không tồn tại)
5. Khởi tạo risk manager với số dư thực tế từ sàn
6. Khởi tạo position tracker, **đối soát** với sàn
7. Kiểm tra `state_snapshot.json` (hồi phục phiên trước)
8. Kiểm tra `trading_halted.lock` — nếu tồn tại, in lý do và thoát
9. Mở WebSocket feed
10. In trạng thái hệ thống, log "System online"

**Không có bước "chờ thị trường mở".** Bỏ toàn bộ logic giờ giao dịch.

### Vòng lặp chính (mỗi khi bar đóng)

1. Nhận bar mới từ WebSocket
2. Tính feature (rolling window, không dữ liệu tương lai)
3. Dự đoán HMM filtered (chỉ forward algorithm)
4. Kiểm tra ổn định regime (3 bar)
5. Kiểm tra flicker rate → bật uncertainty nếu cao
6. `StrategyOrchestrator` → allocation mục tiêu
7. `risk_manager.validate_signal()`
   - approved → `order_executor.submit_order()`
   - modified → log, gửi bản đã sửa
   - rejected → log lý do
8. Cập nhật trailing stop theo regime
9. Kiểm tra circuit breaker
10. Refresh dashboard
11. Ghi `state_snapshot.json`
12. Hàng tuần: retrain HMM

**Vì thị trường 24/7, vòng lặp này chạy vĩnh viễn.** Bot phải sống sót qua restart, mất mạng, sàn bảo trì. Chạy dưới `systemd` hoặc `supervisor` với auto-restart. Ghi snapshot trạng thái mỗi bar, không chỉ khi thoát.

### Tắt (SIGINT/SIGTERM)

- Đóng WebSocket
- **Không** đóng vị thế (stop đã đặt)
- Ghi `state_snapshot.json`
- In tổng kết phiên

### Xử lý lỗi

- Lỗi API: 3 lần thử lại, exponential backoff
- Rate limit (`retCode 10006`): backoff và thử lại, không coi là lỗi nghiêm trọng
- Lỗi HMM: giữ nguyên regime hiện tại
- Mất data feed: tạm dừng signal, giữ stop hoạt động
- Lỗi không bắt được: log traceback, ghi trạng thái, cảnh báo

### CLI

```
--dry-run       Chạy full pipeline, không đặt lệnh
--backtest      Walk-forward backtester
--train-only    Train HMM rồi thoát
--stress-test   Chạy stress test
--compare       So sánh benchmark
--dashboard     Xem dashboard của instance đang chạy
--testnet       Ép dùng testnet (mặc định)
--live          Ép dùng mainnet (yêu cầu xác nhận gõ tay)
```

---

## PHASE 8: Monitoring, Alerts & Dashboard

### 8.1 `monitoring/logger.py`

Log JSON có cấu trúc, file xoay vòng (10MB, giữ 30 ngày): `main.log`, `trades.log`, `alerts.log`, `regime.log`

Mỗi entry gồm: timestamp (UTC), regime, probability, equity, positions, daily_pnl, **cumulative_fees_paid**.

### 8.2 `monitoring/dashboard.py` (thư viện `rich`)

```
┌─ REGIME ───────────────────────────────────────────────┐
│ WEAK_BULL (72%) │ Vol rank: LOW │ Ổn định: 14 bar      │
│ Flicker: 1/20   │ Xác nhận: ✅                          │
├─ PORTFOLIO ────────────────────────────────────────────┤
│ Equity: 10,523 USDT │ Ngày: +34 (+0.32%)               │
│ Allocation: 95%     │ BTC: 0.098421 │ Cash: 526 USDT   │
├─ VỊ THẾ ───────────────────────────────────────────────┤
│ BTCUSDT │ LONG │ 104,230 │ +1.2% │ Stop: 98,400 │ 3d   │
├─ SIGNAL GẦN ĐÂY ───────────────────────────────────────┤
│ 00:00 UTC │ Rebalance 60%→95% │ Vol thấp, xu hướng OK  │
├─ RISK ─────────────────────────────────────────────────┤
│ DD ngày: 0.3%/6% ✅ │ Từ đỉnh: 1.2%/20% ✅              │
│ Phí tháng này: 24 USDT (8.1% lợi nhuận gộp)            │
├─ HỆ THỐNG ─────────────────────────────────────────────┤
│ WS: ✅ 12s trước │ API: ✅ 89ms │ Lệch giờ: 34ms        │
│ HMM: 2 ngày trước │ TESTNET                             │
└────────────────────────────────────────────────────────┘
```

Refresh mỗi 5 giây. Thanh risk có màu. **Ô "Phí" là bổ sung so với bản gốc và cần nhìn thấy thường xuyên** — nó là chỉ báo sớm cho việc giao dịch quá nhiều.

### 8.3 `monitoring/alerts.py`

Kích hoạt khi: đổi regime, circuit breaker, P&L lớn, mất data feed, mất API, HMM retrain xong, flicker vượt ngưỡng, **USDT lệch peg**, **spread bất thường**, **lệch đồng hồ > 1s**.

Gửi qua: console, log file, **Telegram** (thực tế nhất cho crypto — chạy 24/7 nên cần nhận cảnh báo trên điện thoại), email tuỳ chọn, webhook tuỳ chọn.

Giới hạn tần suất: 1 cảnh báo mỗi loại sự kiện mỗi 15 phút.

---

## PHASE 9: Integration Testing & Documentation

### 9.1 Tests

**a. Dry run đầu-cuối:** data → HMM → strategy → risk → lệnh mô phỏng

**b. Look-ahead bias:** `test_look_ahead.py` pass; backtest cho kết quả giống hệt khi đổi ngày kết thúc

**c. Precision:** `test_precision.py` — mọi trường hợp biên của làm tròn qty/price

**d. Cost model:** `test_cost_model.py` — phí tính đúng, tổng khớp với trade log

**e. Risk stress:** signal cực đoan bị cap, lệnh dồn dập bị chặn, lệnh không stop bị từ chối

**f. Bybit testnet:** đặt lệnh limit, sửa stop, huỷ, kiểm tra trạng thái sạch. **Chạy tối thiểu 2 tuần liên tục trên testnet trước khi nghĩ tới tiền thật.**

**g. Recovery:** kill process giữa lúc có lệnh đang chờ, restart, kiểm tra hồi phục trạng thái đúng và **không đặt trùng lệnh** (đây là chỗ `orderLinkId` chứng minh giá trị)

**h. Idempotency:** gửi cùng một signal hai lần, xác nhận chỉ một lệnh được đặt

### 9.2 README.md

- Triết lý: **"quản trị rủi ro quan trọng hơn tạo tín hiệu"**
- Sơ đồ kiến trúc: `data → features → HMM → vol rank → allocation → risk → exchange`
- Quick start 6 bước
- Tham chiếu CLI
- Hướng dẫn cấu hình
- **Phần "Khác biệt so với bản equities"** — chép §0 của tài liệu này vào
- FAQ: forward algorithm là gì, tại sao chọn BIC, tại sao lệnh bị từ chối, chuyển sang live thế nào, tại sao ngưỡng rebalance cao như vậy
- **Disclaimer:** mục đích học tập, không đảm bảo lợi nhuận, chạy testnet trước, crypto biến động cực đoan và sàn có rủi ro đối tác

---

## PHASE 10 (tuỳ chọn, sau khi v1 chạy ổn): Chuyển sang Perpetual Futures

Chỉ làm sau khi spot chạy có lãi trên testnet ít nhất 2 tháng. Nếu spot không có lãi, thêm leverage chỉ làm khoản lỗ to hơn.

Cần thêm:

**1. Funding rate** — perps thu/trả funding mỗi 8 giờ. Ở regime low-vol khi giữ 95% allocation nhiều tuần liền trong thị trường tăng, funding có thể ngốn 10–30%/năm. Phải:
- Đưa vào `cost_model.py` như **một dòng chi phí riêng**, không gộp vào slippage
- Đưa funding rate vào backtest bằng dữ liệu funding lịch sử
- Cân nhắc dùng funding rate làm feature cho HMM (nó là thước đo positioning của thị trường)

**2. Liquidation guard** — không tồn tại ở spot. `risk_manager.py` phải:
- Tính giá thanh lý cho mọi vị thế
- Bắt buộc stop loss cách giá thanh lý tối thiểu 2× ATR
- **Từ chối mọi signal mà stop nằm dưới giá thanh lý** — nếu không sàn sẽ đóng lệnh trước stop của bạn và toàn bộ tầng risk management trở thành vô nghĩa

**3. Bật lại leverage** — `LowVolBullStrategy` được dùng tới 1.25x. Ép về 1.0x khi: regime không chắc chắn, bất kỳ circuit breaker nào đang hoạt động, flicker rate cao.

**4. Chuyển category** — `settings.yaml: category: linear`. Nếu Phase 6 làm đúng (mọi thứ đi qua `ExchangeClient` ABC), thay đổi ở tầng trên là tối thiểu.

---

## Thứ tự thực hiện đề xuất

Không làm tuần tự Phase 1→9. Làm theo thứ tự rủi ro giảm dần:

1. **Phase 1** (scaffold) + **Phase 4.0** (tải dữ liệu lịch sử) — cần dữ liệu trước tiên
2. **Phase 2** (HMM, feature tầng 1) + `test_look_ahead.py` — nếu test này không pass, mọi thứ phía sau vô nghĩa
3. **Phase 3** (strategy) + **Phase 3.5** (trend gate) + **Phase 4** (backtest, cost model, benchmark)
4. **ĐIỂM DỪNG** — chạy đủ §4.8, đối chiếu 8 tiêu chí ở §4.9. Không đủ thì không đi tiếp.
5. Quay lại **Phase 2.3** thêm feature tầng 2, chạy lại §4.8, giữ nếu cải thiện thật
6. **Phase 5** (risk) + **Phase 6** (Bybit) + **Phase 7** (main loop)
7. **Phase 8** (monitoring) + **Phase 9** (test)
8. Testnet 2 tuần → mainnet với số vốn nhỏ nhất bạn chấp nhận mất hoàn toàn

Bước 4 là bước quan trọng nhất trong danh sách này. Phần lớn dự án loại này thất bại không phải vì code sai, mà vì người xây bỏ qua bước 4 và dành ba tháng hoàn thiện tầng thực thi cho một chiến lược không có edge.
