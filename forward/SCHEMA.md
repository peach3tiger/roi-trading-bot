# Schema log forward test

Forward test ghi log **chỉ append**. Khi schema đổi, ta **cuộn sang file
mới** thay vì sửa file cũ — file đã đóng không bao giờ được chạm nữa.

Lý do: sửa header của một file đang chạy để "vá cho khớp" nghĩa là viết
lại bằng chứng thí nghiệm sau khi đã thấy kết quả. Kể cả khi phép sửa
đúng về kỹ thuật, nó phá tính chất khiến log này đáng tin — rằng mỗi dòng
được ghi đúng một lần, tại thời điểm đó, và không bao giờ đổi.

## Bảng phiên bản

| Phiên bản | File | Bar | Số cột | Khác biệt |
|---|---|---|---|---|
| v1 | `log.csv` | 2026-08-05 (1 bar) | 31 | schema gốc |
| v2 | `log_v2.csv` | từ 2026-08-06 | 32 | **+ `warning_count`** (index 6, ngay sau `hmm_train_bars`) |

`log.csv` (v1) **ĐÃ ĐÓNG**. Không đường nào ghi vào nó nữa —
`forward/runner.py` trỏ `forward.logger._LOG_PATH` sang file đang hoạt
động. SHA256 của nó được ghim trong `tests/golden/frozen_hashes.json`;
`tests/test_frozen_files.py` đỏ nếu nó đổi dù một ký tự.

## Đọc dữ liệu — dùng `load_all_bars()`, không đọc file trực tiếp

Code phân tích ở mốc 3/6/12 tháng **phải đọc cả hai file**:

```python
from forward.runner import load_all_bars

df = load_all_bars()   # đã nối, đã sắp theo date, có cột `source_log`
```

Đọc thẳng `log_v2.csv` sẽ mất bar 2026-08-05. Một bar trên 365 thì vô hại
về thống kê, nhưng "vô hại" không phải thứ nên để mỗi chỗ gọi tự phán
đoán lại.

### `warning_count` của v1 là `NaN`, KHÔNG phải `0`

Bản chạy v1 không có cơ chế đếm warning. `0` sẽ là khẳng định sai — "đã
đo, không có warning" — thay vì ô trống trung thực: "không biết". Mọi
phép thống kê trên cột này phải **bỏ qua NaN**, không cộng chúng vào như
số không.

`load_all_bars()` trả về đúng như vậy; đây là một lý do nữa để không tự
đọc file rồi `fillna(0)`.

## KHÔNG cuộn schema lần nữa trong thời gian thí nghiệm

**Không tạo `log_v3.csv` cho tới khi thí nghiệm kết thúc (2027-08-06).**

Cần thêm cột thì ghi vào một **file phụ riêng**:

```
forward/extra_<tên>.csv     # khoá theo bar_date, nối với file chính khi phân tích
```

File chính giữ nguyên → mọi trạng thái suy ra từ nó không bị reset.

### Vì sao — chi phí của việc cuộn không chỉ là "hai schema phải đọc"

`load_all_bars()` nối MỌI file, nhưng `run_forward_test()` thì **không** —
nó chỉ đọc file ĐANG HOẠT ĐỘNG. Bất cứ thứ gì được suy ra bằng cách quét
lịch sử log sẽ mất ở mỗi lần cuộn.

Lần cuộn v1 → v2 đã dạy điều đó bằng một sai lệch thật. Lịch retrain xác
định bằng:

```python
retrained_rows = existing[existing["hmm_retrained"]]
last_retrain_date = retrained_rows["date"].max().date()  # None nếu rỗng
```

Lần retrain 2026-08-05 nằm ở `log.csv` (đã đóng), không còn nhìn thấy từ
`log_v2.csv` → `last_retrain_date = None` → runner retrain ngay ở lần chạy
kế tiếp (08-08), sớm hơn lịch 4 ngày. Chi tiết đầy đủ + tại sao không sửa
được: `docs/DECISIONS.md`, mục **"Sai lệch thí nghiệm #1"**.

Điều đáng lo không phải bản thân lần lệch đó — nó vô hại. Điều đáng lo là
**ta chỉ phát hiện ra nó ba ngày sau, một cách tình cờ**, khi thắc mắc vì
sao hai lần retrain cách nhau 3 ngày. Không có gì bảo đảm lần cuộn sau sẽ
reset một thứ vô hại tương tự, và cũng không có gì bảo đảm ta phát hiện ra.

### Nếu vẫn buộc phải cuộn (chỉ sau khi thí nghiệm kết thúc)

1. Ghi entry `docs/DECISIONS.md` **TRƯỚC**, tại thời điểm thay đổi. Không
   phải sau, không phải "để sau gộp chung". Sự cố 2026-08-06 xảy ra vì
   `warning_count` được thêm vào file đóng băng mà không có entry nào.
2. **Liệt kê mọi trạng thái suy ra từ lịch sử log** và ghi rõ cái nào sẽ
   bị reset. Tính tới 2026-08-08 mới biết một cái (lịch retrain) — đừng
   giả định đó là cái duy nhất, hãy đọc lại `run_forward_test()`.
3. Đổi `ACTIVE_LOG_PATH` trong `forward/runner.py` sang `log_v3.csv`.
4. Thêm file vừa đóng vào `CLOSED_LOG_PATHS`.
5. Ghim SHA256 file vừa đóng vào `tests/golden/frozen_hashes.json`.
6. Cập nhật bảng phiên bản ở trên.

**Không** sửa `forward/logger.py` để làm việc này. Nó đóng băng, và
`append_row()`/`read_existing_log()` đã chừa sẵn cửa: chúng tra `_LOG_PATH`
ở *thời điểm gọi*, nên `runner.py` gán lại biến module là đủ.

## Vì sao sự cố 2026-08-06 xảy ra

`append_row()` chỉ ghi header khi file **chưa tồn tại**:

```python
is_new = not target.exists()
```

Đúng cho append-only, nhưng hệ quả là một file đã bắt đầu **không bao giờ
học được cột mới**. Thêm `warning_count` vào `_CSV_FIELDNAMES` khi
`log.csv` đã có 1 bar → header ở lại 31 cột, dòng mới ghi 32 cột,
`read_existing_log()` chết ở `pd.read_csv` mọi lần chạy sau đó. launchd
vẫn chạy đều, exit 1 mỗi lần, không có gì báo trong 3 ngày.

**Với file append-only, đổi schema không phải thay đổi tương thích ngược
— nó là thay đổi phá vỡ, và nó phá ở lần ĐỌC tiếp theo, không phải lần
ghi.**

Xem `docs/DECISIONS.md`, mục 2026-08-08.
