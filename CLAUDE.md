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

`tests/test_layer_composition.py` phải kiểm chứng điều này bằng property test trên giá trị ngẫu nhiên.

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
- `test_layer_composition.py` — các tầng chỉ giảm
- `test_cost_model.py` — phí tính đúng

Bốn file này không được skip, không được xfail, không được comment out.

---

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
