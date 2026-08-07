# Phase 12c — Shadow Deploy + Trace Context

Đọc `CLAUDE.md` (đặc biệt bất biến #2 và #12), `prompts/phase-12b-harness-engineering.md`, và `forward/README.md`.

**Mục tiêu:** không để phiên bản mới chạm vào lệnh thật cho tới khi chứng minh được nó cho ra tín hiệu giống phiên bản đang chạy — và khi có gì bất thường, truy được toàn bộ chuỗi quyết định bằng một lệnh grep.

**Không thêm logic giao dịch nào.**

**Điều kiện tiên quyết:** §0 của `phase-12b` (backtest tất định) phải xong. Nếu backtest chưa tất định, cổng so sánh ở phần A vô nghĩa và phase này không làm được.

---

## 0. TẠI SAO KHÔNG PHẢI BLUE-GREEN

Ghi phần này vào `ops/RUNBOOK.md` — nó giải thích một quyết định kiến trúc mà người đọc sau này sẽ muốn đảo ngược.

Blue-green giả định các instance **không chia sẻ trạng thái**. Với bot giao dịch, trạng thái thật nằm ở **sàn**, không nằm trong tiến trình. Hai instance cùng quản một tài khoản sẽ tính rebalance độc lập và cùng gửi lệnh.

`orderLinkId` sinh deterministic chỉ chặn được lệnh trùng khi hai instance tính ra **cùng** allocation. Nếu chúng khác nhau — mà khác nhau chính là lý do bạn đang deploy — cả hai lệnh đều qua, và vị thế nhân đôi.

Vì vậy: **không bao giờ có hai tiến trình cùng khả năng đặt lệnh trên một tài khoản.** Đây là bất biến, không phải lựa chọn triển khai.

Mô hình thay thế:

| Giai đoạn | Cơ chế |
|---|---|
| Kiểm logic | So sánh ngoại tuyến tất định trên toàn bộ dữ liệu lịch sử |
| Kiểm tầng sàn | Shadow mode chỉ-đọc, 24–48 giờ |
| Chuyển đổi | Restart một instance duy nhất, bàn giao qua `state_snapshot.json` |
| Rollback | `git checkout` commit cũ, restart |

---

## A. CỔNG CHÍNH — SO SÁNH NGOẠI TUYẾN

Đây là cổng mạnh nhất và rẻ nhất. Chạy trước mọi thứ khác.

### `ops/compare_versions.py`

```python
def compare_versions(ref_a: str, ref_b: str, start: date, end: date) -> ComparisonReport:
    """
    Chạy backtest ở hai git ref trên cùng dữ liệu, so từng bar.
    ref có thể là commit hash, tag, hoặc branch.
    Dùng git worktree để checkout song song, không đụng cây làm việc hiện tại.
    """
```

So sánh từng bar, không chỉ tổng hợp:

| Trường | Ngưỡng |
|---|---|
| `regime_id` | phải khớp 100% |
| `hmm_allocation` | phải khớp 100% |
| `trend_gate_cap` | phải khớp 100% |
| `final_allocation` | phải khớp 100% |
| `equity` | chênh ≤ 1e-9 |

Khác biệt **bất kỳ** → in bar đầu tiên lệch, giá trị hai bên, và 10 bar xung quanh để lấy bối cảnh.

**Không có ngưỡng dung sai cho thay đổi được coi là "không ảnh hưởng logic".** Nếu một refactor được cho là thuần tuý mà output lệch, thì giả định sai — điều tra, đừng nới ngưỡng.

Nếu thay đổi **cố ý** làm lệch output (ví dụ sửa một tham số chiến lược): so sánh này sẽ FAIL, và đó là đúng. Lúc đó ghi vào `docs/DECISIONS.md` rằng đây là thay đổi có chủ đích, kèm bảng so sánh hiệu năng cũ/mới, rồi cập nhật baseline.

### CLI

```bash
python -m ops.compare_versions --ref-a HEAD~1 --ref-b HEAD
python -m ops.compare_versions --ref-a v1.0 --ref-b main --start 2018-02-09 --end 2026-08-04
```

---

## B. SHADOW MODE — CHỈ KIỂM TẦNG SÀN

Sau khi phần A xanh. Shadow mode **không** dùng để kiểm logic — phần A đã làm việc đó tốt hơn nhiều: hàng nghìn bar thay vì vài chục, tái lập được, mất vài giây.

Shadow mode chỉ trả lời những câu hỏi mà backtest không trả lời được: phản hồi API thật, dữ liệu WebSocket thật, lệch đồng hồ, làm tròn theo `instrumentRules` thật của sàn, hành vi khi mạng chập chờn.

### `ops/shadow_runner.py`

Chạy vòng lặp chính đầy đủ — data → feature → HMM → strategy → trend gate → risk manager — nhưng **không có đường code nào tới `order_executor`**.

**Chặn ở tầng kiến trúc, không bằng cờ boolean.** Cùng cách `forward/logger.py` đã làm: shadow runner không import `broker.order_executor`. Một cờ `dry_run=True` có thể bị lật nhầm; một import không tồn tại thì không.

Ghi `ops/shadow/YYYY-MM-DD.jsonl`, mỗi bar một dòng, cùng schema với log của instance production.

### `ops/shadow_diff.py`

So log shadow với log production trên cùng khoảng thời gian:

- `regime_id`, `hmm_allocation`, `trend_gate_cap`, `final_allocation` — phải khớp 100%
- `instrument_rules` lấy từ sàn — phải khớp
- Chênh lệch `clock_skew_ms`, `api_latency_ms` — chỉ ghi nhận, không phải tiêu chí

Chạy tối thiểu **24 giờ**, khuyến nghị 48. Bất kỳ khác biệt nào ở bốn trường đầu → dừng, điều tra. Đó là khác biệt tầng sàn mà backtest không thấy được.

---

## C. TRACE CONTEXT

### C.1 `monitoring/trace.py`

```python
from contextvars import ContextVar

trace_id: ContextVar[str] = ContextVar("trace_id", default="-")

def new_bar_trace(bar_timestamp: datetime, symbol: str) -> str:
    """
    Sinh deterministic: f"{bar_timestamp.isoformat()}:{symbol}"

    KHÔNG dùng UUID ngẫu nhiên. Lý do giống orderLinkId: chạy lại cùng bar
    phải cho cùng id, để log backtest / shadow / forward / live so sánh
    trực tiếp được mà không cần parser riêng.
    """
```

**Bắt buộc dùng `contextvars`, không truyền `trace_id` qua chữ ký hàm.**

Lý do: nếu phải truyền tay qua mọi hàm, sớm muộn một nhánh bị quên, chuỗi đứt — và nó **đứt im lặng**, chỉ phát hiện đúng lúc đang cần truy vết. `contextvars` chạy xuyên cả call stack đồng bộ lẫn async mà không chạm chữ ký nào.

Thêm logging filter tự chèn `trace_id` vào mọi bản ghi. Không module nào phải tự nhớ ghi nó.

### C.2 Phạm vi là chu kỳ bar, không phải lệnh

`trace_id` sinh ở **đầu chu kỳ bar**, trước cả khi tính feature.

Đây là điểm khác biệt với `trade_id` đã có ở Phase 9. `trade_id` chỉ tồn tại khi có lệnh. Nhưng thứ khó truy nhất lại là những gì **không** thành lệnh: signal bị risk manager từ chối, bar bị trend gate chặn, bar không làm gì.

Quan hệ: một bar → một `trace_id` → N signal → N `trade_id`. Mỗi bản ghi `trade_id` phải mang theo `trace_id` cha.

### C.3 Mỗi tầng ghi input và output

```
trace=2026-08-06T00:00:00Z:BTCUSDT layer=features   n_features=8
trace=2026-08-06T00:00:00Z:BTCUSDT layer=hmm        regime=STRONG_BEAR conf=0.9996 vol_rank=0 alloc_out=0.95
trace=2026-08-06T00:00:00Z:BTCUSDT layer=trend_gate state=BEAR_STRUCTURE cap=0.30
trace=2026-08-06T00:00:00Z:BTCUSDT layer=risk       cap=1.00 breaker=normal
trace=2026-08-06T00:00:00Z:BTCUSDT layer=compose    final=0.30 capped_by=trend_gate
trace=2026-08-06T00:00:00Z:BTCUSDT layer=rebalance  skipped reason=below_threshold delta=0.02
```

**`capped_by` là trường có giá trị nhất trong toàn bộ thiết kế này.** Kiến trúc là `min()` của ba tầng; khi có gì bất thường, câu hỏi đầu tiên luôn là *tầng nào đang giới hạn*. Không có trường này thì phải đọc bốn file log rồi tự suy ra.

Tính nó ở tầng `compose`, đừng để người đọc log tự suy.

### C.4 Đồng bộ định dạng

`forward/logger.py`, `ops/shadow_runner.py`, và `main.py` phải phát **cùng một định dạng trace**. Đó là điều kiện để `shadow_diff.py` và các so sánh về sau không cần parser riêng cho từng nguồn.

**Cảnh báo:** sửa `forward/logger.py` để thêm trace là thay đổi hành vi ghi log của một thí nghiệm đang chạy. Chỉ thêm **cột log**, tuyệt đối không đụng vào đường tính toán. `tests/test_forward_golden.py` phải còn xanh sau thay đổi này — nếu nó đỏ, revert ngay, thí nghiệm quan trọng hơn tiện lợi khi debug.

---

## D. QUY TRÌNH TRIỂN KHAI

Ghi thành checklist trong `ops/RUNBOOK.md`:

```
1. pytest tests/ -v                         → toàn bộ xanh
2. python -m ops.compare_versions           → khớp 100%, hoặc lệch có chủ đích đã ghi DECISIONS.md
3. tests/regression_harness.py              → xanh
4. tests/test_forward_golden.py             → xanh
5. ops/shadow_runner.py 24-48h              → shadow_diff khớp 100% bốn trường chính
6. Kiểm tra điều kiện thời điểm (§E)
7. Dừng instance production, lưu state_snapshot.json
8. git checkout <ref mới>, restart
9. Theo dõi 2 giờ: health.json status=ok, drift.json không cảnh báo
10. Ghi vào DECISIONS.md: ref cũ, ref mới, ngày, kết quả so sánh
```

Rollback: `git checkout <ref cũ>` rồi restart. Trạng thái đọc lại từ `state_snapshot.json` và đối soát với sàn — cùng cơ chế Phase 10 đã yêu cầu cho khôi phục sau crash.

---

## E. ĐIỀU KIỆN THỜI ĐIỂM — không dùng lịch

Crypto chạy 24/7. Luật "không deploy tối Thứ Sáu" là luật của thị trường có giờ đóng cửa, tồn tại vì không có ai trực — không phải vì Thứ Sáu nguy hiểm. Áp nó vào đây là mang theo một giả định đã chết.

Thay bằng ba điều kiện đo được:

1. **Realized vol 24h không nằm trên phân vị 80** của phân bố lịch sử. Deploy giữa lúc thị trường động là tự chồng thêm biến số.
2. **Không có lệnh đang chờ, không có circuit breaker đang hoạt động.**
3. **Bạn có mặt được ít nhất 2 giờ tới.** Đây là điều kiện về người, không về lịch — và nó là điều kiện thật đằng sau luật Thứ Sáu.

Implement §E.1 và §E.2 thành hàm kiểm tra tự động; §E.3 là câu hỏi xác nhận thủ công trong runbook.

---

## RÀNG BUỘC BẮT BUỘC

1. **Không bao giờ hai tiến trình cùng khả năng đặt lệnh trên một tài khoản.** Ghi vào `CLAUDE.md` như một bất biến mới.
2. `ops/shadow_runner.py` **không import** `broker.order_executor`. Chặn bằng kiến trúc, không bằng cờ.
3. Không đường code nào trong phase này **ghi** vào `forward/`. Đọc thì được.
4. `tests/test_forward_golden.py` phải còn xanh sau toàn bộ phase.
5. Không thêm logic giao dịch.

---

## Nghiệm thu

- [ ] `python -m ops.compare_versions --ref-a HEAD --ref-b HEAD` → khớp 100% (sanity: so với chính nó)
- [ ] Sửa một hằng số trong `core/regime_strategies.py`, commit tạm → `compare_versions` FAIL và in đúng bar đầu tiên lệch. Revert sau khi thử.
- [ ] `grep -rn "order_executor\|submit_order" ops/shadow_runner.py` → không có kết quả
- [ ] Chạy shadow 24h song song với `--dry-run`, `shadow_diff` khớp 100% bốn trường chính
- [ ] Grep một `trace_id` bất kỳ trong log → tái dựng đủ chuỗi 6 dòng từ `features` tới `rebalance`
- [ ] `capped_by` xuất hiện đúng: dựng ca test mà trend gate là tầng giới hạn, và ca mà risk manager là tầng giới hạn
- [ ] Chạy lại cùng một bar hai lần → `trace_id` giống hệt (tính deterministic)
- [ ] `pytest tests/test_forward_golden.py -v` xanh
- [ ] Kiểm tra điều kiện §E.1 chạy được trên dữ liệu thật, in ra phân vị vol hiện tại
- [ ] `ruff check . && mypy .` sạch
