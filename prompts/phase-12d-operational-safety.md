# Phase 12d — Operational Safety Harness

Đọc `CLAUDE.md`, `ops/RUNBOOK.md`, `docs/STATE.md`.

**Mục tiêu:** bot dừng an toàn khi có gì đó sai, kể cả khi chính bot là thứ bị sai. Không thêm logic giao dịch.

**Nguyên tắc xuyên suốt:** mọi cơ chế ở đây phải chạy **ngoài tiến trình bot**. Một tiến trình bị treo không tự phát hiện được rằng nó đang treo.

---

## A. `monitoring/watchdog.py`

### A.1 Không dùng `threading.Timer` trong cùng process

Nếu vòng lặp chính deadlock, giữ GIL, hoặc kẹt trong một lệnh gọi mạng không timeout, thread timer hoặc cũng kẹt theo, hoặc vẫn chạy và báo "khoẻ" trong khi bot đã chết. Cả hai đều vô dụng.

### A.2 macOS không có tương đương `systemd Type=notify`

`launchd` có `KeepAlive` — khởi động lại khi tiến trình **thoát**, nhưng không phát hiện được tiến trình **treo**. Với macOS chỉ còn một phương án: **tiến trình watchdog riêng.**

Nếu sau này chạy trên Linux thì dùng `systemd` `WatchdogSec=60` + `sd_notify`, tốt hơn vì kernel giám sát. Ghi cả hai đường vào `ops/RUNBOOK.md`.

### A.3 Thiết kế

Bot ghi `${STATE_DIR}/heartbeat.json` mỗi vòng lặp: `{"pid": ..., "updated_at": "...", "bar_ts": "...", "loop_seq": N}`.

Watchdog là tiến trình riêng, chạy dưới launchd, kiểm tra mỗi 30 giây:

- `mtime` của heartbeat > 90 giây → bot treo
- `loop_seq` không tăng qua 3 lần kiểm tra liên tiếp → bot treo (bắt được cả trường hợp file được ghi lại nhưng vòng lặp đứng)
- PID trong file không còn tồn tại → bot chết

### A.4 Cách kết thúc — thứ tự bắt buộc

```
1. SIGTERM, chờ tối đa 30 giây
   → cho bot chạy shutdown handler: đóng kết nối, ghi state_snapshot.json
2. Còn sống sau 30 giây → SIGKILL
3. Ghi ${STATE_DIR}/watchdog_kill.json: thời điểm, lý do, tín hiệu đã dùng,
   heartbeat cuối cùng đọc được
4. Gửi cảnh báo WATCHDOG_KILL
5. KHÔNG tự khởi động lại bot
```

Mục 5 quan trọng. Watchdog giết bot rồi launchd khởi động lại ngay sẽ tạo vòng lặp crash không ai để ý. Khởi động lại là quyết định của con người sau khi chạy `recovery_checklist.py`.

**SIGKILL thẳng là sai:** bot có thể đang giữa lúc gửi lệnh. Giết ngay để lại lệnh mồ côi mà `state_snapshot.json` không ghi được. SIGTERM trước cho nó cơ hội ghi lại mình đang làm gì.

---

## B. `monitoring/data_harness.py`

Tiến trình riêng, giám sát chất lượng dữ liệu.

### B.1 Nhịp kiểm tra khớp với nhịp bar

Bot chạy bar 1D. Kiểm tra mỗi 30 giây là thừa — mỗi ngày chỉ có một bar mới. Chia làm hai:

- **Khi có bar mới:** chạy đủ bộ kiểm tra tính đúng đắn
- **Mỗi 15 phút:** chỉ kiểm tra độ tươi — bar mới nhất có cũ hơn 1.5× chu kỳ bar không

### B.2 Kiểm tra tính đúng đắn

- `low <= close <= high`, `low <= open <= high`, `low <= high`
- `volume >= 0`, `trade_count >= 0`
- Không thiếu bar: chuỗi timestamp liên tục theo chu kỳ
- Không timestamp trùng
- Không bar nào có `volume == 0` (với BTC/USDT bar ngày, đó chắc chắn là lỗi dữ liệu)

Vi phạm bất kỳ điều nào → ghi `data_quality.lock`, bot đọc và **dừng sinh signal mới, giữ nguyên vị thế và stop**.

### B.3 Giá nhảy lớn — không tự động dừng

Ngưỡng 50%/bar là ngưỡng hợp lý cho **lỗi dữ liệu**. Nhưng BTC đã từng giảm ~40% trong một ngày (3/2020), và một cú sập thật là lúc bạn **cần bot hoạt động nhất**, không phải lúc để nó tự khoá.

Quy tắc phân biệt:

```
Nếu |Δ| > 30% trong một bar:
  1. Lấy cùng bar đó từ nguồn thứ hai (sàn khác qua ccxt)
  2. Hai nguồn khớp (chênh < 2%) → biến động THẬT
     → cảnh báo LARGE_PRICE_MOVE, KHÔNG ghi lock, bot chạy tiếp
  3. Hai nguồn lệch nhau → LỖI DỮ LIỆU
     → ghi data_quality.lock, dừng signal
  4. Không lấy được nguồn thứ hai → ghi lock (thận trọng)
```

Đây là điểm khác biệt quan trọng nhất giữa "bảo vệ" và "tự bắn vào chân": khoá bot đúng lúc thị trường sập là cách chắc chắn để bỏ lỡ chính hành vi phòng vệ mà bạn đã xây bảy phase để có.

### B.4 Xoá lock

`data_quality.lock` phải xoá **thủ công**, giống `trading_halted.lock`. File ghi rõ: kiểm tra nào thất bại, bar nào, giá trị thực tế.

---

## C. `config/validate.py`

Chạy trước khi bot khởi động, và trong CI.

### C.1 Kiểm tra cấu hình

- Đủ mọi section bắt buộc trong `settings.yaml`
- Mọi biến môi trường bắt buộc tồn tại và không rỗng — báo đúng tên biến còn thiếu
- Hash `forward/config_frozen.yaml` khớp `tests/golden/frozen_hashes.json`
- `exchange.testnet: true` trừ khi có xác nhận mainnet tường minh

### C.2 Kiểm tra bất biến — dùng AST, KHÔNG dùng grep

Dự án này đã gặp đúng vấn đề đó: grep tìm `.predict(` bắt nhầm docstring đang *giải thích* tại sao không được dùng `predict()`, buộc phải viết lại docstring cho vừa công cụ. Đó là công cụ sai bắt code phải chiều nó.

Dùng `ast` để phân tích, bỏ qua comment và docstring:

- `core/hmm_engine.py` không có lời gọi `.predict()` hoặc `.decode()` (CLAUDE.md #1)
- `core/` và `data/` không có `.rolling(center=True)` (CLAUDE.md #11)
- `core/signal_generator.py` dùng `min()` khi kết hợp tầng, không phải `max()` (CLAUDE.md #2)
- `core/risk_manager.py` không import `hmm_engine` hay `regime_strategies` (CLAUDE.md #4)
- `ops/shadow_runner.py` (nếu tồn tại) không import `order_executor`
- Không có literal `252` trong `core/`, `data/`, `backtest/` (CLAUDE.md #9)

Kiểm chứng bằng đột biến (CLAUDE.md #16): thêm tạm một lời gọi vi phạm cho mỗi kiểm tra, xác nhận validator từ chối, revert.

---

## D. `scripts/emergency_kill.py`

### D.1 Việc phải làm

```
1. Ghi trading_halted.lock kèm lý do và thời điểm
2. Huỷ mọi lệnh VÀO/REBALANCE đang chờ
3. KHÔNG đóng vị thế spot
4. Ghi state_snapshot.json
5. SIGTERM tới bot, chờ 30s, rồi SIGKILL
6. In tóm tắt: lệnh đã huỷ, vị thế còn lại, stop còn hiệu lực
```

### D.2 TUYỆT ĐỐI KHÔNG huỷ lệnh stop-loss

Đây là chỗ dễ sai nhất và hậu quả nặng nhất. "Huỷ lệnh chờ" nghe như một hành động, nhưng lệnh stop-loss **cũng là lệnh chờ**. Huỷ hết nghĩa là để lại vị thế trần trụi trong đúng tình huống khẩn cấp.

Phải phân loại theo `orderType`/`reduceOnly`, huỷ đúng lệnh vào và lệnh rebalance, giữ nguyên mọi lệnh bảo vệ. Viết test riêng cho đúng điểm này.

### D.3 Không đóng vị thế spot

Đóng vị thế trong hoảng loạn là hiện thực hoá khoản lỗ ở đúng thời điểm tệ nhất, và nó mâu thuẫn với chính luận điểm của hệ thống — giảm tỷ trọng theo biến động, không thoát sạch. Vị thế có stop rồi; để stop làm việc của nó.

---

## E. `scripts/recovery_checklist.py`

Chạy sau mọi lần dừng bất thường. **Chỉ đọc và báo cáo, không tự sửa.**

```
1. Đọc state_snapshot.json và watchdog_kill.json (nếu có)
2. Lấy từ sàn: số dư, vị thế, lệnh đang mở
3. Đối soát, in bảng ba cột: snapshot / sàn / chênh lệch
4. Với mọi chênh lệch: TIN SÀN, in ra lệnh cần chạy để đồng bộ lại
   nhưng KHÔNG tự chạy
5. Kiểm tra vị thế còn stop bảo vệ không — thiếu stop là mục ưu tiên cao nhất
6. Kiểm tra lệnh mồ côi: lệnh trên sàn không có trong snapshot
7. Kiểm tra các file lock: trading_halted, data_quality — in nội dung
8. In checklist người dùng phải xác nhận thủ công trước khi khởi động lại
```

Mục 5 là mục quan trọng nhất. Sau một lần crash, kịch bản tệ nhất không phải là mất đồng bộ trạng thái, mà là **có vị thế mà không có stop** — và không có gì tự động phát hiện được điều đó.

---

## RÀNG BUỘC

1. Watchdog, data harness chạy **tiến trình riêng**, không phải thread.
2. Không script nào ở đây tự khởi động lại bot. Khởi động lại là quyết định của con người.
3. Không script nào tự sửa trạng thái. Chúng phát hiện và báo cáo.
4. Không đường code nào ghi vào `forward/`.
5. `config/validate.py` dùng AST, không dùng grep, cho mọi kiểm tra bất biến.
6. Không thêm logic giao dịch.

---

## Nghiệm thu

- [ ] Mô phỏng bot treo (`kill -STOP <pid>`) → watchdog phát hiện trong 90s, gửi SIGTERM, escalate SIGKILL sau 30s, ghi `watchdog_kill.json`, **không** khởi động lại
- [ ] Cắt heartbeat nhưng giữ tiến trình sống → watchdog vẫn phát hiện qua `loop_seq` đứng yên
- [ ] Tiêm bar hỏng (`close > high`) → `data_quality.lock` được ghi, bot dừng signal, giữ vị thế
- [ ] Tiêm biến động thật −35% khớp ở hai nguồn → cảnh báo phát ra, **không** ghi lock
- [ ] Tiêm biến động giả −35% chỉ có ở một nguồn → ghi lock
- [ ] `config/validate.py`: với mỗi bất biến, tiêm vi phạm, xác nhận từ chối, revert
- [ ] `config/validate.py` **không** báo lỗi khi docstring có nhắc `predict()` — chứng minh dùng AST chứ không grep
- [ ] `emergency_kill.py`: dựng ca có cả lệnh vào lẫn lệnh stop đang mở → chỉ lệnh vào bị huỷ, stop còn nguyên
- [ ] `recovery_checklist.py`: dựng ca vị thế không có stop → báo cáo nêu đó là mục ưu tiên cao nhất
- [ ] `pytest` và `pytest -m slow` đều xanh, `ruff check . && mypy .` sạch kèm phạm vi đã kiểm
