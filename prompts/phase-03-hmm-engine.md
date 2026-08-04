# Phase 3 — HMM Engine + Feature Engineering (tầng 1)

Đọc `docs/Brain-Crypto-Bybit.md` PHASE 2 toàn bộ. Đây là phase quan trọng nhất về mặt kỹ thuật.

**Chỉ implement feature tầng 1 (OHLCV).** Tầng 2 và 3 để sau, sau khi tầng 1 đã được validate.

## Việc cần làm

### `data/feature_engineering.py`

Pure function, không state, không I/O. Toàn bộ feature tầng 1 trong spec §2.3.

Dùng `trade_count` thay cho `volume` — lý do trong spec.

Rolling z-score lookback **365**. Mọi `.rolling()` phải `center=False`.

### `core/hmm_engine.py`

- `GaussianHMM`, `covariance_type="full"`, thử `n_components` 3–7, chọn theo BIC, `n_init=10`
- Log **toàn bộ** BIC của mọi ứng viên, không chỉ cái được chọn
- Gán nhãn regime bằng cách sắp theo mean return (chỉ để người đọc)
- **`predict_regime_filtered()`** — forward algorithm tự implement, log space, cache alpha
- Bộ lọc ổn định 3 bar, flicker rate, uncertainty mode
- `RegimeInfo` và `RegimeState` dataclass
- Lưu model + metadata (n_regimes, bic, training_date, labels, feature_list, data_hash)

### `tests/test_look_ahead.py` — VIẾT TRƯỚC KHI VIẾT HMM

```python
def test_no_look_ahead_bias():
    # Chạy predict_regime_filtered trên dữ liệu tới bar N
    # Chạy lại trên dữ liệu tới bar N+50, cắt lấy kết quả tại bar N
    # PHẢI giống hệt nhau

def test_viterbi_does_have_look_ahead():
    # Cùng phép so sánh nhưng dùng model.predict()
    # PHẢI khác nhau — chứng minh test ở trên có tác dụng
```

## Nghiệm thu

- [ ] `pytest tests/test_look_ahead.py -v` — cả hai test xanh. Dán nguyên output.
- [ ] `grep -rn "\.predict(\|\.decode(" core/` — chỉ được xuất hiện trong test, không có trong `hmm_engine.py`
- [ ] `grep -rn "center=True" data/` — không có kết quả
- [ ] `grep -rn "252" core/ data/ backtest/` — không có kết quả
- [ ] Train trên dữ liệu thật, in bảng BIC của cả 5 ứng viên và cái được chọn
- [ ] In ma trận chuyển tiếp và `expected_volatility` của từng regime
- [ ] Xác nhận thứ tự nhãn (theo return) **khác** thứ tự vol rank — nếu trùng nhau hoàn toàn thì đáng ngờ, kiểm tra lại

Nếu `test_look_ahead.py` không xanh, **dừng lại**. Mọi thứ phía sau đều vô nghĩa.
