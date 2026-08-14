# Readiness Gate — các cổng phải qua trước khi thay đổi được coi là xong

> **File này KHÔNG phải nguồn sự thật cho §A–§D.** Nội dung bất biến nằm ở
> `CLAUDE.md`; bảng dưới đây gom chúng lại một chỗ và đặt nhãn §A–§E để
> tiện dẫn chiếu. Mâu thuẫn giữa file này và `CLAUDE.md` thì `CLAUDE.md`
> đúng. §E là cổng duy nhất được ĐỊNH NGHĨA ở đây.

| | Cổng | Điều kiện | Kiểm bằng | Cơ giới hoá? |
|---|---|---|---|---|
| §A | Tất định backtest | Hai lần chạy cùng cấu hình cho kết quả giống bit-for-bit | `pytest tests/test_determinism.py` | Có — bộ mặc định (bản nhanh) + `-m slow` (bản đầy đủ) |
| §B | Bảy file test bắt buộc | Không skip, không xfail, không comment out | `pytest` | Có — `CLAUDE.md` #15 liệt kê đủ bảy file |
| §C | Đột biến trước khi tin một phép kiểm mới | Phá thứ nó đáng lẽ bắt được → đỏ → revert | Kịch bản đột biến, xem `CLAUDE.md` #16 | Không — kỷ luật con người |
| §D | Mainnet | Forward test đạt mốc 12 tháng (2027-08-06) **và** §4.9 đánh giá lại trên dữ liệu forward | `docs/DECISIONS.md`, `docs/VALIDATION_REPORT.md` | Không — quyết định con người |
| §E | **Test chậm khi chạm tầng quyết định** | Diff chạm `core/` hoặc `backtest/` → `pytest -m slow` **BẮT BUỘC** | `python ops/readiness_gate.py --base <base>` | **Có** — cổng + CI |

---

## §E — `pytest -m slow` là BẮT BUỘC khi diff chạm tầng quyết định

### Quy tắc

Nếu `git diff --name-only <base>..HEAD` có bất kỳ dòng nào khớp
`^(core|backtest)/` thì `pytest -m slow` là **bắt buộc**, không phải tuỳ
chọn. Có kết quả mà chưa chạy slow → **gate FAIL**.

Phạm vi bao gồm — và rộng hơn — năm mục quy tắc gốc nêu tên:

```
core/regime_strategies.py
core/trend_gate.py
core/signal_generator.py
core/hmm_engine.py
backtest/
```

Bản thực thi lấy toàn bộ `core/` + `backtest/`, rộng hơn danh sách trên,
**có chủ ý**: một danh sách tên file phải cập nhật tay mỗi lần thêm module
vào `core/`, và lần quên đầu tiên sẽ im lặng — đúng kiểu hỏng mà cổng này
sinh ra để chặn. `core/risk_manager.py` cũng là một tầng trong
`min(hmm, trend_gate, risk)` nên nó thuộc phạm vi, không phải ngoại lệ.

### Vì sao

`tests/test_snapshot.py` đã ghi rõ, kèm bảng ĐO trên hai cửa sổ khác nhau:
smoke test (~8s) **không** bắt được đột biến `_EMA_PERIOD` 50 → 40 trên
đường allocation, trong khi `tests/regression_harness.py` (`-m slow`) bắt
ngay (Sharpe lệch 0.031).

**Docstring đó giữ nguyên và vẫn là chỗ giải thích TẠI SAO.** Cổng này lo
phần **BẮT BUỘC** — nó không thuyết phục ai, nó chặn.

Lý do cần cả hai: `pyproject.toml` đặt `addopts = "-m 'not slow'"`, nghĩa
là `pytest` trần không còn là "chạy tất cả". Đó là đánh đổi có chủ ý (vòng
lặp phát triển nhanh), và cái giá của nó là **quên `-m slow` không hề báo
lỗi** — bộ test vẫn xanh, vẫn in "553 passed". Một lời giải thích trong
docstring chỉ có tác dụng với người đã đọc nó, vào đúng lúc cần.

### Chạy tại máy

```bash
pytest -m slow && python ops/readiness_gate.py --base origin/main
```

Lệnh đầu sinh biên lai `.slow_receipt.json` (tự động, qua
`tests/conftest.py::pytest_sessionfinish`); lệnh sau đọc nó.

### Bằng chứng "đã chạy slow"

Biên lai ghi **SHA256 của toàn bộ `core/**/*.py` + `backtest/**/*.py`** tại
thời điểm chạy, không phải commit SHA. Cổng băm lại và so.

Vì sao không dùng commit SHA: chạy slow xong rồi sửa tiếp `core/` mà chưa
commit sẽ cho một biên lai "khớp HEAD" nhưng vô giá trị. Băm nội dung
không quan tâm tới commit, chỉ quan tâm tới câu hỏi đúng — *mã đang được
gác đã đổi kể từ lần chạy slow chưa?*

Biên lai chỉ được sinh khi phiên slow **xanh hoàn toàn** (`exitstatus == 0`
và không test nào fail). Một phiên slow đỏ mà vẫn cấp biên lai thì cổng chỉ
kiểm "đã chạy", không kiểm "đã qua".

`.slow_receipt.json` nằm trong `.gitignore`: nó gắn với nội dung mã trên
MỘT máy, vô nghĩa khi commit.

### Ở CI

`.github/workflows/ci.yml`, job `slow-gate`:

1. `checkout` với `fetch-depth: 0` — `git diff <base>..HEAD` cần `base` có
   trong repo. Một checkout nông làm cổng hỏng theo kiểu "không kiểm được"
   trong khi job vẫn xanh, đúng chế độ hỏng đã xảy ra ba lần trong dự án
   này (`CLAUDE.md` #16).
2. Đếm file khớp `^(core|backtest)/`. Đếm dòng chứ **không đọc exit code
   sau pipe** (`CLAUDE.md` #17) — `grep` không tìm thấy trả 1, và ở đây
   "không tìm thấy" là kết quả hợp lệ.
3. Có kết quả → chạy `pytest -m slow`, rồi chạy `ops/readiness_gate.py`.
   Không có → bỏ qua, in lý do.

### Khi cổng FAIL

Báo cáo in ra danh sách file trong phạm vi. Hai nguyên nhân:

- **Không có biên lai** → chạy `pytest -m slow`.
- **Biên lai ĐÃ CŨ** → `core/`/`backtest/` đã đổi kể từ lần chạy slow. Chạy
  lại `pytest -m slow`.

**Không có đường vòng.** Nếu thay đổi thật sự không cần slow, thứ phải sửa
là phạm vi cổng (`GATED_PREFIXES` trong `ops/readiness_gate.py`) kèm lý do
ghi vào `docs/DECISIONS.md` — không phải bỏ qua một lần cho tiện.
