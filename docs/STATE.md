# STATE — bàn giao trạng thái

Đọc file này đầu mỗi phiên. Tối đa một trang. Chi tiết ở `DECISIONS.md` và
`VALIDATION_REPORT.md`. Cập nhật ở cuối mỗi phase, ghi đè, không phụ lục thêm.

## Đang ở đâu

- Phase 1–8 xong. Phase 9 (broker) chuyển từ Bybit sang **Binance qua
  ccxt** (2026-08-06, xem `docs/DECISIONS.md`). 207 passed / 0 skipped.
- Forward test chạy từ 2026-08-06, cấu hình đóng băng, launchd hằng ngày.
  Mốc đánh giá: 2026-11-06 / 2027-02-06 / 2027-08-06. Không đụng tới trong
  phiên này.
- Cổng: `CLAUDE.md` #12 — xây tầng thực thi ở **testnet** được, **mainnet**
  bị chặn tới 2027-08-06.

## Testnet đang bị chặn — KHÔNG PHẢI lỗi Binance, KHÔNG PHẢI lỗi code

Chặn ở tầng tài khoản GitHub. Đã xác nhận trước đó bằng gọi thật:
`exchange_reachable` OK (mạng/API Binance testnet sống bình thường,
155ms), vấn đề nằm ngoài cả hai lớp (sàn, code). Không debug thêm ở
hướng "sửa CCXTClient"/"sửa health_check" cho việc này — không phải chỗ
hỏng. Mọi việc cần key Binance testnet thật (nghiệm thu submit_order/
cancel_order/idempotency qua mạng) **tạm dừng**, không phải ưu tiên hiện
tại — xem "Việc còn treo" bên dưới, thứ tự đã đổi để tránh nó.

## Việc còn treo, theo thứ tự ưu tiên (cập nhật — tránh nhánh bị chặn testnet)

1. ~~`tests/test_forward_golden.py`~~ — đã có từ trước phiên này, không
   phải việc thật (đã kiểm tra lại và báo lại khi được giao nhầm là
   "chưa có, quan trọng nhất").
2. ~~4 test skip trong `test_hmm.py`~~ — **XONG.** 14 test thay 4 skip
   (BIC selection, gán nhãn theo return-rank, vol-rank độc lập, bộ lọc ổn
   định/hysteresis, flicker rate). **Phát hiện + sửa 1 bug thật trong lúc
   viết test** (không phải đọc code):
   `HMMRegimeEngine._extract_variances()` đọc sai vị trí variance cho
   `covariance_type` khác `"full"` — `diag`/`tied`/`spherical` sai (chỉ
   `full` tình cờ đúng), vì `model.covars_` của hmmlearn 0.3.3 LUÔN trả
   full matrix bất kể covariance_type, khác giả định cũ. Không lộ ra vì
   production chỉ chạy `covariance_type: full` (settings.yaml) — sẽ lộ
   ngay khi ablation thử loại khác. Đã sửa: bỏ nhánh theo loại, luôn đọc
   đường chéo ma trận full. Chi tiết đầy đủ: `docs/DECISIONS.md`, mục
   "Lấp 4 test skip trong test_hmm.py". Tự kiểm chứng bằng mutation
   (CLAUDE.md #16): 11/14 test đỏ đúng theo 5 mutation, 3 không liên quan
   vẫn xanh, đã revert.
3. ~~Lỗi mypy trong `tests/test_forward_logger.py`~~ — **XONG.** 2 nguyên
   nhân gốc: (a) `dict.fromkeys(fields, "")` khiến mypy suy ra
   `dict[str, str]` từ giá trị điền mặc định thay vì tôn trọng chữ ký hàm
   `dict[str, object]` — annotate biến tường minh; (b) fixture
   `_forward_harness` thiếu return type + 5 chỗ dùng nó thiếu type param
   — thêm alias `_ForwardHarness = tuple[Path, pd.DataFrame, Path]`, gắn
   vào cả fixture lẫn mọi hàm test dùng nó. Chỉ thêm type hint, không đổi
   logic — 23/23 test cũ pass nguyên vẹn. `mypy .` toàn repo sạch (50
   file, 0 lỗi) — trước đây (kể cả nhiều phiên trước phiên này) luôn còn
   sót 8 lỗi ở đúng file này.
4. **`prompts/phase-10-main-loop.md`** — ưu tiên thật hiện tại. Xây được
   và test được bằng `--dry-run`, KHÔNG cần sàn thật. Chỉ phần nghiệm thu
   đặt lệnh thật mới cần testnet (bị chặn, xem trên) — không chặn việc
   xây main loop.
5. Điền `EXCHANGE_API_KEY`/`EXCHANGE_API_SECRET` + nghiệm thu `CCXTClient`
   qua mạng thật — **TẠM DỪNG**, chờ testnet hết bị chặn ở tầng tài khoản
   GitHub. Không phải việc kế tiếp.
6. Copy `phase-12b-harness-engineering.md` và `phase-12c-shadow-deploy.md`
   vào `prompts/` (đã soạn, chưa có trong repo).

## Đổi sàn Bybit -> Binance (ccxt) — 2026-08-06 (tóm tắt, chi tiết ở DECISIONS.md)

Bybit chặn theo khu vực (`retCode 10024`), không kết nối được cả testnet
lẫn mainnet kể cả public endpoint. Thay bằng `broker/ccxt_client.py::CCXTClient`,
`broker/bybit_client.py` giữ lại (deprecated, không xoá — bằng chứng
quyết định). WebSocket -> REST polling toàn bộ (`ExchangeClient` bỏ
`subscribe_klines`/`subscribe_executions`; `PositionTracker.on_execution()`
bỏ, thêm `poll()` — Phase 10 main loop PHẢI gọi `position_tracker.poll()`
mỗi vòng, đây là đường cập nhật vị thế duy nhất bây giờ).
`broker/order_executor.py` — 0 thay đổi logic (ABC không rò rỉ chi tiết
sàn). `ops/health_check.py` đọc `exchange.name` từ config, env var
`EXCHANGE_API_KEY/SECRET/TESTNET` — fallback đọc tên `BYBIT_*` cũ đã BỎ
HẲN (không còn đọc, kể cả làm dự phòng) — thiếu biến nào thì
`exchange_authenticated` FAIL và nêu đúng tên biến đó.

## Việc tiếp theo

Phase 10 main loop (`--dry-run`, không cần testnet) -> [testnet hết bị
chặn] -> nghiệm thu CCXTClient qua mạng thật.

## Quy tắc đã học, không lặp lại

- Mọi số đo thị trường lấy từ **testnet không dùng để hiệu chỉnh tham số**.
  Thanh khoản testnet là nhân tạo.
- Không bao giờ log giá trị key/secret, kể cả một phần.
- Không bao giờ hai tiến trình cùng khả năng đặt lệnh trên một tài khoản.
- Khả năng truy cập theo khu vực/tài khoản có thể chặn bất kỳ lớp nào
  (sàn — Bybit; hạ tầng — GitHub) bất kỳ lúc nào, không cảnh báo trước.
  Khi bị chặn, xác định ĐÚNG lớp bị chặn trước khi debug (đừng sửa code
  để "chữa" một chặn ở tầng tài khoản) — chuyển sang việc không phụ thuộc
  lớp đó, quay lại khi hết chặn, không đoán/không chờ không việc gì.
- Trước khi "xây lại" một file bị báo là thiếu/chưa có: kiểm tra thật
  (file tồn tại? đã commit? có trên remote? test pass?) rồi mới tin —
  CLAUDE.md #16 (đột biến trước khi tin test) áp dụng tương tự cho việc
  tin một báo cáo trạng thái: xác minh trước khi hành động, không phải
  sau.
- Thư viện ngoài có thể đổi hành vi giữa các phiên bản theo cách âm thầm
  đúng-ngữ-pháp-sai-ngữ-nghĩa (hmmlearn's `covars_` luôn trả full matrix
  bất kể `covariance_type`, khác giả định code cũ) — code không lỗi cú
  pháp, không raise, chỉ âm thầm tính sai. Viết test đọc lại GIÁ TRỊ THẬT
  từ một lần fit thật, không chỉ test "không crash", là cách duy nhất bắt
  được loại lỗi này.
