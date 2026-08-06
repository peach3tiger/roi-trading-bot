# ops/RUNBOOK.md — vận hành container

Đọc `ops/Dockerfile`/`ops/docker-compose.yml`/`ops/entrypoint.sh`/
`ops/health_check.py` trước file này nếu chưa quen — đây chỉ là quy trình
xử lý sự cố, không lặp lại nội dung đã có trong code/comment.

## Trạng thái hiện tại — đọc trước khi làm gì khác

**`main.py` chưa có live loop.** Phase 9 (`broker/bybit_client.py`,
`prompts/phase-09-bybit-broker.md`) và Phase 10 (main loop,
`prompts/phase-10-main-loop.md`) đều còn là stub (`NotImplementedError`).
Chạy container hôm nay (`docker compose up`) sẽ:

1. `ops/entrypoint.sh` chạy xong tiền kiểm (không có gì chặn trên máy sạch).
2. `exec python main.py` → rơi vào nhánh cuối của `main()` → raise
   `NotImplementedError("Live loop chưa được implement — dùng --backtest")`.
3. Container thoát mã khác 0. `restart: unless-stopped` sẽ crash-loop.

**Đây là hành vi ĐÚNG** — fail loud cho một tính năng chưa xây, không phải
lỗi của `ops/`. `ops/` được dựng TRƯỚC Phase 9/10 có chủ đích: hạ tầng
triển khai (Dockerfile, healthcheck, quy trình vận hành) không phụ thuộc
main loop đã xong hay chưa, và sẵn sàng ngay khi Phase 10 landed. Nếu
crash-loop gây phiền lúc phát triển, đổi `restart:` trong
`docker-compose.yml` thành `on-failure:3` hoặc `no` tạm thời — đừng đổi
thành `always` để che triệu chứng.

Có thể dùng **ngay hôm nay**, độc lập với Phase 9/10:

```bash
cp .env.example .env   # lần đầu — docker compose lỗi ngay nếu thiếu .env
docker compose -f ops/docker-compose.yml build
docker compose -f ops/docker-compose.yml run --rm bot python ops/health_check.py
docker compose -f ops/docker-compose.yml run --rm bot python main.py --backtest \
  --feature-subset log_return_1,log_return_5,realized_vol_20,vol_ratio_5_20,adx_14,sma50_slope,trade_count_zscore_50,trade_count_sma10_slope \
  --start 2018-02-09 --end 2026-08-04 --output-dir reports/my_run
```

(`--feature-subset` ở đây **bắt buộc** — thiếu nó chạy bộ 14 cột Tầng 1
mặc định, một cấu hình khác đã bị loại, không phải hệ thống 6/8 §4.9 đã
kiểm định. Xem README.md gốc repo, mục "Cách chạy".)

## Ranh giới với forward test — KHÔNG được vi phạm

Container này **không bao giờ** chạy `forward/logger.py`. Forward test
chạy qua **launchd trên máy host** (`forward/com.regime-trader-crypto.forward-test.plist`),
đọc `forward/config_frozen.yaml` đã đóng băng và hash-kiểm mỗi lần chạy —
xem `forward/README.md` và `docs/DECISIONS.md` mục "Forward test — tiền
đăng ký". Lý do: đưa nó vào container đổi môi trường thực thi (Python
version trong image, hệ điều hành, filesystem, cách lên lịch) giữa chừng
một thí nghiệm 12 tháng đã tiền đăng ký (bắt đầu 2026-08-06, mốc cuối
2027-08-06) — phá đúng giả định "cùng một hệ thống xuyên suốt" mà thí
nghiệm đó cần để kết quả có nghĩa.

`docker-compose.yml` mount `../forward:/app/forward:ro` — **chỉ đọc**, chỉ
để một dashboard/log-viewer tương lai xem trạng thái forward test, không
phải để container tự chạy nó. `ops/entrypoint.sh` chặn cứng bất kỳ lệnh
nào chứa `forward.logger`/`forward/logger.py` trong `"$@"` — nếu bạn thấy
container thoát vì lỗi "TỪ CHỐI: lệnh chứa...", đó là guard này hoạt động
đúng, sửa lại `command:` chứ không sửa entrypoint.

## Biến môi trường

| biến | mặc định | ý nghĩa |
|---|---|---|
| `CONFIG_PATH` | `config/settings.yaml` | đường dẫn `settings.yaml` — **không phải** `forward/config_frozen.yaml` |
| `MODEL_PATH` | `models/hmm_model.pkl` | model HMM của live loop — **riêng biệt** với `forward/state/hmm_model.pkl` (cache nội bộ của forward test, đừng trỏ nhầm) |
| `LOG_DIR` | `logs/` | thư mục log xoay vòng (`monitoring/logger.py`) |
| `STATE_DIR` | `state/` (mặc định script) / `/app/state` (container) | `trading_halted.lock`, `state_snapshot.json` |
| `REQUIRE_HMM_MODEL` | `true` | `false` để health check chỉ WARN thay vì FAIL khi chưa có model — dùng lúc bring-up trước khi Phase 10 train lần đầu |
| `BYBIT_TESTNET` | `true` | health check ping testnet hay mainnet — **luôn để `true`** trừ khi đã qua đủ mốc ở CLAUDE.md bất biến #12 |

---

## Circuit breaker kích hoạt

**Triệu chứng:** log có dòng `CircuitBreaker <LEVEL>: daily_dd=...`
(`core/risk_manager.py::CircuitBreaker.check`, log level WARNING), hoặc
dashboard (khi có) hiện DD vượt ngưỡng ở khối RISK.

1. **Xác định level** — `DAILY_REDUCE`/`DAILY_HALT`/`WEEKLY_REDUCE`/
   `WEEKLY_HALT`/`PEAK_HALT` (`core/risk_manager.py::BreakerLevel`), ưu
   tiên PEAK_HALT > WEEKLY_HALT > DAILY_HALT > WEEKLY_REDUCE > DAILY_REDUCE
   — chỉ level nghiêm trọng nhất được log/áp dụng.
2. **REDUCE (daily hoặc weekly)** — không cần can thiệp. Hệ thống tự nhân
   `target_allocation_pct` với 0.5 cho tới lần `reset_daily()`/
   `reset_weekly()` kế tiếp (00:00 UTC / Thứ Hai 00:00 UTC — CLAUDE.md bất
   biến #10). Theo dõi, không thao tác.
3. **HALT (daily hoặc weekly)** — không đặt lệnh mới cho tới reset. Vị thế
   hiện có GIỮ NGUYÊN (stop loss đã đặt vẫn hoạt động — xem `broker/order_executor.py`
   ghi chú về stop ở spot). Không cần can thiệp trừ khi muốn đóng thủ công.
4. **PEAK_HALT** — nghiêm trọng nhất. Hệ thống tự ghi
   `${STATE_DIR}/trading_halted.lock` và **dừng vô thời hạn** — không tự
   phục hồi dù equity tăng lại. `ops/entrypoint.sh` sẽ từ chối khởi động
   lại container trong khi file này còn tồn tại (đúng thiết kế, xem
   Brain-Crypto-Bybit.md §5.2: "phải xoá thủ công mới chạy lại").

   **Trước khi xoá lock file:**
   - Đọc nội dung file (`triggered_at`, `peak_dd`, ngưỡng) — đã ghi rõ
     trong `core/risk_manager.py::RiskManager._write_halt_lock`.
   - Xem lại `logs/main.log`/`logs/trades.log` quanh mốc `triggered_at`:
     đây là sụt giảm thị trường thật, hay bug (regime sai liên tục, order
     executor lỗi, dữ liệu giá sai)?
   - Nếu là sụt giảm thị trường thật và không có bug: quyết định có tiếp
     tục chạy ở mức allocation nào khi khởi động lại (cân nhắc bắt đầu ở
     allocation thấp, không phải target đầy đủ ngay).
   - Nếu nghi ngờ bug: **không xoá lock**, điều tra trước. Đây chính là lý
     do file tồn tại — ngăn một bug lặp lại vô hạn lần trong lúc "tự động
     phục hồi".
   - Xoá: `rm ${STATE_DIR}/trading_halted.lock` (trên host, đúng thư mục
     đã mount — xoá TRONG container không đủ nếu volume mount đúng, vì
     file sống trên host).

---

## HMM retrain lỗi

**Triệu chứng:** log lỗi trong bước "Hàng tuần: retrain HMM"
(Brain-Crypto-Bybit.md §Phase 7 vòng lặp chính, bước 12) — có thể là
`ValueError` từ `HMMRegimeEngine.select_and_train` (thiếu `min_train_bars`),
lỗi mạng lúc tải dữ liệu mới, hoặc exception từ chính quá trình fit EM.

1. **Hành vi mặc định của hệ thống khi lỗi HMM** (Brain-Crypto-Bybit.md
   §Phase 7 "Xử lý lỗi"): **giữ nguyên regime hiện tại**, không dừng vòng
   lặp chính, không hoảng loạn bán/mua theo lỗi. Nếu log KHÔNG cho thấy
   hành vi này (vòng lặp dừng hẳn, hoặc regime bị đặt về giá trị mặc định/
   rác), đó là bug cần sửa trước khi tiếp tục — không phải hành vi retrain
   lỗi thông thường.
2. **Kiểm tra dữ liệu đầu vào trước** — hầu hết lỗi retrain là lỗi dữ liệu,
   không phải lỗi thuật toán:
   - Đủ bar chưa? Cần tối thiểu `hmm.min_train_bars` (730, xem
     `config/settings.yaml`) bar feature liên tục, không NaN.
   - `data/history_loader.py::DataIntegrityError` gần đây trong log? Nếu
     có, xử lý nguồn dữ liệu trước, đừng ép retrain qua lỗi này.
3. **Model cache cũ** — nếu `${MODEL_PATH}` đang trỏ một file hỏng/không
   load được (`ops/health_check.py` sẽ FAIL ở mục `hmm_model` với thông
   báo cụ thể), xoá file đó — hệ thống retrain lại từ đầu ở lần thử kế
   tiếp (tốn `n_candidates × n_init` lần fit EM, không tức thời — xem
   `forward/README.md` mục Retrain để hiểu chi phí tương tự).
4. **Không** thử "sửa" bằng cách hạ `min_train_bars` hay đổi `n_candidates`
   giữa chừng chỉ để retrain qua được — đó là thay đổi tham số ảnh hưởng
   hành vi hệ thống, phải qua đúng quy trình CLAUDE.md bất biến #13/#14
   (ablation, ghi vào `settings.yaml` có lý do), không phải một bản vá vội
   để retrain không lỗi nữa.
5. Sau khi xử lý nguyên nhân gốc, xác nhận bằng
   `docker compose run --rm bot python ops/health_check.py` trước khi để
   container tự chạy lại theo lịch.

---

## Mất WebSocket

**Triệu chứng:** không có bar/tick mới trong log quá **2× chu kỳ bar**
(Brain-Crypto-Bybit.md §6.6 — timeframe `1D` nên ngưỡng là 2 ngày; đây là
ngưỡng đã định nghĩa trong spec cho `data/market_data.py::subscribe_klines`,
chưa implement tại thời điểm viết file này).

1. **Hành vi đúng khi mất feed** (§Phase 7 "Xử lý lỗi"): **tạm dừng sinh
   signal mới, giữ stop loss đang hoạt động**. Không đóng vị thế chỉ vì
   mất feed — mất feed không có nghĩa thị trường dừng, và đóng vị thế
   không có xác nhận giá mới là hành động rủi ro hơn là chờ.
2. Kiểm tra kết nối mạng container → Bybit trước:
   `docker compose run --rm bot python ops/health_check.py` — mục
   `exchange_reachable` cho biết REST API còn sống hay không (WebSocket
   là kênh riêng, nhưng nếu REST cũng chết thì khả năng cao là vấn đề
   mạng/DNS của container, không phải riêng WebSocket của Bybit). Mục này
   CHỈ kiểm tra mạng, không kiểm tra key — xem mục "Xác thực Bybit thất
   bại" bên dưới nếu `exchange_reachable` OK mà bot vẫn không giao dịch
   được.
3. Bybit ngắt WebSocket im lặng theo chu kỳ (§6.6) — thiết kế đúng đã có
   heartbeat ping/pong + tự kết nối lại; nếu tự kết nối lại liên tục thất
   bại, kiểm tra rate limit (`retCode 10006` — backoff, không phải lỗi
   nghiêm trọng, xem §Phase 7) trước khi nghi ngờ nguyên nhân khác.
4. Nếu mất feed kéo dài bất thường (nhiều giờ) mà REST API vẫn sống bình
   thường: nghi ngờ bug ở tầng subscribe/reconnect, không phải sự cố phía
   Bybit — xem log traceback đầy đủ, không chỉ dòng cảnh báo đầu tiên.

---

## Xác thực Bybit thất bại (key hết hạn/bị revoke/sai môi trường)

**Đây là chế độ hỏng phổ biến nhất khi vận hành thật** — phổ biến hơn cả
mất WebSocket hay circuit breaker, vì nó có thể xảy ra ngay từ lần khởi
động đầu tiên và dễ bị hiểu nhầm là "đã kết nối được rồi".

`ops/health_check.py` tách RÕ hai việc, đừng nhầm lẫn:

- `exchange_reachable` — public endpoint (`fetch_time`), **không cần API
  key**. OK chỉ có nghĩa là mạng/DNS/Bybit's server đang sống. **Không
  chứng minh được key hợp lệ.**
- `exchange_authenticated` — một request CẦN xác thực thật
  (`fetch_balance`, không đặt lệnh, không đổi trạng thái tài khoản). Đây
  mới là check phát hiện: key hết hạn, bị revoke trên dashboard, thiếu
  quyền (permission scope), hoặc — lỗi hay gặp nhất — **dán nhầm key
  MAINNET vào môi trường testnet hay ngược lại** (Bybit testnet/mainnet
  có không gian API key HOÀN TOÀN TÁCH BIỆT, một key chỉ dùng được đúng
  một môi trường).

**Triệu chứng điển hình:** `exchange_reachable` báo OK (đôi khi latency
rất tốt, < 300ms) nhưng `exchange_authenticated` FAIL với thông điệp dạng
`API key is invalid. (ErrCode: 10003)` hoặc lỗi 401. Đây là tình huống
THẬT đã gặp lúc kiểm thử Phase 9 (key trong `.env` bị Bybit từ chối ở
tầng xác thực dù server phản hồi bình thường ở tầng mạng) — chính là lý do
`check_exchange_authenticated` được tách ra làm check riêng. **Trước khi
có check này, `ops/health_check.py` chỉ gọi `fetch_time()` nên báo "kết
nối OK" trong đúng tình huống này — sai lệch nghiêm trọng, vì bot tưởng
sẵn sàng mà không đặt được lệnh nào.**

Quy trình xử lý:

1. Đọc kỹ thông điệp lỗi của `exchange_authenticated` — retCode/retMsg từ
   chính Bybit, KHÔNG chứa credential (an toàn để dán vào ticket/log).
   `401`/`10003` = key không được sàn công nhận (sai/hết hạn/revoke/sai
   môi trường); các retCode khác (vd. `10004` chữ ký sai) có thể chỉ ra
   nguyên nhân khác (đồng hồ lệch quá — xem `exchange_reachable`'s cảnh
   báo lệch đồng hồ, hoặc secret bị gõ sai).
2. Vào **testnet.bybit.com** (không phải bybit.com) → API Management —
   xác nhận key trong `.env` còn tồn tại, chưa hết hạn, chưa bị revoke, và
   có đủ quyền (ít nhất "Read" cho tài khoản; "Trade" khi cần đặt lệnh
   thật). So khớp `BYBIT_TESTNET` trong `.env` với đúng dashboard đang mở
   (testnet vs mainnet là hai trang, hai bộ key khác nhau).
3. Nếu phải tạo key mới: cập nhật `.env` (không commit — đã có trong
   `.gitignore`/`.dockerignore`), chạy lại
   `docker compose run --rm bot python ops/health_check.py` để xác nhận
   `exchange_authenticated` chuyển OK trước khi tin tưởng chạy tiếp.
4. **Không** coi `exchange_reachable` OK là đủ để kết luận "hệ thống sẵn
   sàng" ở bất kỳ đâu khác trong vận hành (dashboard, alert, quyết định
   thủ công) — luôn nhìn cả hai check.

---

## Khôi phục sau crash

Thị trường 24/7 — container **sẽ** crash-restart giữa lúc có lệnh đang
chờ hoặc vị thế đang mở. Đây là chuyện thường ngày, không phải trường hợp
hiếm (Brain-Crypto-Bybit.md §6.5).

1. **Khởi động lại tự tuần tự theo đúng thứ tự đã định nghĩa**
   (§Phase 7 "Khởi động", 10 bước) — health check (`ops/health_check.py`,
   qua `ops/entrypoint.sh`) chỉ phủ bước 1 (config hợp lệ) và một phần
   bước 3-4 (model tồn tại); **đối soát với sàn** (bước 6) và **đọc
   `state_snapshot.json`** (bước 7) là việc của `main.py` khi Phase 10
   xong, không phải của `ops/`.
2. **Đối soát bắt buộc trước khi giao dịch tiếp** (§6.5): so số dư/vị thế
   thực tế trên sàn với `state_snapshot.json` đã lưu. Lệch → **tin sàn**,
   ghi log cảnh báo, không tự "sửa" số dư sàn để khớp state cũ.
3. **Idempotency qua `orderLinkId`** (CLAUDE.md bất biến #8): nếu crash
   xảy ra đúng lúc một lệnh đang chờ khớp, khởi động lại và gửi lại "cùng"
   lệnh đó (`orderLinkId` sinh deterministic từ `(symbol, bar_timestamp,
   target_allocation)`) sẽ bị sàn từ chối trùng thay vì đặt hai lần — nếu
   thấy vị thế nhân đôi sau một lần crash-restart, đây là bug ở
   `orderLinkId` (không deterministic đúng, hoặc bị bỏ qua ở đâu đó), báo
   ngay, đừng coi là "thị trường biến động mạnh".
4. **`trading_halted.lock` sống sót qua crash** — vì nó là file trên
   volume đã mount (`${STATE_DIR}`), không phải cờ trong bộ nhớ tiến
   trình. Một lần PEAK_HALT trước khi crash vẫn chặn được khởi động lại
   sau crash — đây là thiết kế đúng, xem mục "Circuit breaker kích hoạt"
   ở trên nếu gặp trường hợp này.
5. Sau khi container lên lại và pass health check, theo dõi vài bar đầu
   tiên kỹ hơn bình thường — regime/allocation ngay sau khôi phục là nơi
   dễ lộ bug nhất (state cũ + dữ liệu mới gặp nhau).

---

## Kiểm tra nhanh (không có sự cố cụ thể)

```bash
# Build lại image sau khi đổi code
docker compose -f ops/docker-compose.yml build

# Health check độc lập, không khởi động vòng lặp chính
docker compose -f ops/docker-compose.yml run --rm bot python ops/health_check.py

# Xem log container đang chạy
docker compose -f ops/docker-compose.yml logs -f bot

# Trạng thái healthcheck Docker (OK/unhealthy) của container đang chạy
docker inspect --format='{{json .State.Health}}' regime-trader-crypto
```
