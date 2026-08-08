# ops/RUNBOOK.md — vận hành container

Đọc `ops/Dockerfile`/`ops/docker-compose.yml`/`ops/entrypoint.sh`/
`ops/health_check.py` trước file này nếu chưa quen — đây chỉ là quy trình
xử lý sự cố, không lặp lại nội dung đã có trong code/comment.

## Trạng thái hiện tại — đọc trước khi làm gì khác

**`main.py` chưa có live loop.** Phase 9 (`broker/ccxt_client.py` — đổi từ
`broker/bybit_client.py` ngày 2026-08-06, xem `docs/DECISIONS.md`,
`broker/bybit_client.py` vẫn còn trong repo nhưng deprecated) và Phase 10
(main loop, `prompts/phase-10-main-loop.md`) đều còn là stub
(`NotImplementedError`). Chạy container hôm nay (`docker compose up`) sẽ:

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
| `EXCHANGE_TESTNET` | `true` | health check ping testnet hay mainnet — **luôn để `true`** trừ khi đã qua đủ mốc ở CLAUDE.md bất biến #12. Đổi tên từ `BYBIT_TESTNET` ngày 2026-08-06 (đổi sàn Bybit -> Binance, xem `docs/DECISIONS.md`); KHÔNG còn đọc tên cũ — `.env` phải dùng đúng tên này |
| `EXCHANGE_API_KEY` / `EXCHANGE_API_SECRET` | *(rỗng, BẮT BUỘC cho `exchange_authenticated`)* | credential cho `exchange_authenticated` check — sàn thật đọc từ `config/settings.yaml: exchange.name`, không hardcode. Đổi tên từ `BYBIT_API_KEY`/`BYBIT_API_SECRET` cùng đợt trên, KHÔNG còn đọc tên cũ; thiếu biến nào thì `exchange_authenticated` FAIL và nêu đúng tên biến đó |

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

## Mất dữ liệu giá (REST polling thất bại)

**Đã bỏ WebSocket** (2026-08-06, xem `docs/DECISIONS.md`) — bot chạy bar
`1D`, `data/market_data.py::get_latest_kline()` poll REST trực tiếp mỗi
lần gọi, không cache, không heartbeat. Vì vậy KHÔNG còn kiểu lỗi "mất kết
nối im lặng" mà WebSocket từng có (`is_feed_alive()`/`subscribe_klines()`
đã bị xoá cùng đợt này — nếu bạn thấy code tham chiếu chúng, đó là tàn dư
cần dọn, không phải tính năng còn sống): mỗi lần poll HOẶC thành công HOẶC
raise ngay tại chỗ gọi, không có trạng thái lấp lửng "đã kết nối nhưng dữ
liệu cũ" cần một cơ chế riêng để phát hiện.

**Triệu chứng:** exception/traceback từ `get_latest_kline()` (hoặc bất kỳ
lệnh gọi `ExchangeClient` nào) trong log, KHÔNG PHẢI một ngưỡng "quá lâu
không có bar mới" như trước.

1. **Hành vi đúng khi một lần poll lỗi** (§Phase 7 "Xử lý lỗi", áp dụng
   tương tự): **tạm dừng sinh signal mới ở vòng đó, giữ stop loss đang
   hoạt động, thử lại ở vòng poll kế tiếp**. Không đóng vị thế chỉ vì một
   lần poll lỗi — lỗi REST không có nghĩa thị trường dừng, và đóng vị thế
   không có xác nhận giá mới là hành động rủi ro hơn là chờ.
2. Kiểm tra kết nối mạng container → sàn trước:
   `docker compose run --rm bot python ops/health_check.py` — mục
   `exchange_reachable` cho biết REST API còn sống hay không. Mục này
   CHỈ kiểm tra mạng, không kiểm tra key — xem mục "Xác thực sàn thất
   bại" bên dưới nếu `exchange_reachable` OK mà bot vẫn không giao dịch
   được.
3. Nếu lỗi lặp lại liên tục dù `exchange_reachable` OK: kiểm tra
   `broker/ccxt_client.py::CCXTClient._call_with_retry` đã retry đúng tập
   lỗi nhất thời (`ccxt.NetworkError` và các lớp con) chưa hết log
   WARNING trước khi raise — nếu raise ngay từ lần đầu với một lỗi lẽ ra
   tự khỏi (rate limit, timeout), đó là bug ở whitelist retry, không phải
   sự cố phía sàn.
4. Rate limit (`ccxt.RateLimitExceeded`/`DDoSProtection`) tự retry có
   backoff — không phải lỗi nghiêm trọng, xem log WARNING thay vì log
   ERROR để phân biệt.

---

## Xác thực sàn thất bại (key hết hạn/bị revoke/sai môi trường)

**Đây là chế độ hỏng phổ biến nhất khi vận hành thật** — phổ biến hơn cả
lỗi poll dữ liệu giá hay circuit breaker, vì nó có thể xảy ra ngay từ lần
khởi động đầu tiên và dễ bị hiểu nhầm là "đã kết nối được rồi".

`ops/health_check.py` tách RÕ hai việc, đừng nhầm lẫn:

- `exchange_reachable` — public endpoint (`fetch_time`), **không cần API
  key**. OK chỉ có nghĩa là mạng/DNS/server sàn đang sống. **Không chứng
  minh được key hợp lệ.**
- `exchange_authenticated` — một request CẦN xác thực thật
  (`fetch_balance`, không đặt lệnh, không đổi trạng thái tài khoản). Đây
  mới là check phát hiện: key hết hạn, bị revoke trên dashboard, thiếu
  quyền (permission scope), hoặc — lỗi hay gặp nhất — **dán nhầm key
  MAINNET vào môi trường testnet hay ngược lại** (testnet/mainnet của
  hầu hết sàn, kể cả Binance, có không gian API key HOÀN TOÀN TÁCH BIỆT,
  một key chỉ dùng được đúng một môi trường).

**Triệu chứng điển hình:** `exchange_reachable` báo OK (đôi khi latency
rất tốt, < 300ms) nhưng `exchange_authenticated` FAIL với thông điệp báo
lỗi xác thực (dạng "invalid API key" hoặc HTTP 401 — nội dung chính xác
tuỳ sàn, xem `ccxt.AuthenticationError` trong log). Đây là tình huống
THẬT đã gặp lúc kiểm thử Phase 9 với Bybit (key trong `.env` bị từ chối ở
tầng xác thực dù server phản hồi bình thường ở tầng mạng) — chính là lý do
`check_exchange_authenticated` được tách ra làm check riêng. **Trước khi
có check này, `ops/health_check.py` chỉ gọi `fetch_time()` nên báo "kết
nối OK" trong đúng tình huống này — sai lệch nghiêm trọng, vì bot tưởng
sẵn sàng mà không đặt được lệnh nào.**

Quy trình xử lý:

1. Đọc kỹ thông điệp lỗi của `exchange_authenticated` — message nguyên
   văn từ ccxt (đã bọc mã lỗi gốc của sàn), KHÔNG chứa credential (an
   toàn để dán vào ticket/log).
2. Vào trang testnet của đúng sàn đang cấu hình
   (`config/settings.yaml: exchange.name` — Binance:
   **testnet.binance.vision**, không phải binance.com) → API Management —
   xác nhận key trong `.env` (`EXCHANGE_API_KEY`/`EXCHANGE_API_SECRET`)
   còn tồn tại, chưa hết hạn, chưa bị revoke, và có đủ quyền (ít nhất
   "Read" cho tài khoản; "Trade" khi cần đặt lệnh thật). So khớp
   `EXCHANGE_TESTNET` trong `.env` với đúng dashboard đang mở (testnet vs
   mainnet là hai trang, hai bộ key khác nhau).
3. Nếu phải tạo key mới: cập nhật `.env` (không commit — đã có trong
   `.gitignore`/`.dockerignore`), chạy lại
   `docker compose run --rm bot python ops/health_check.py` để xác nhận
   `exchange_authenticated` chuyển OK trước khi tin tưởng chạy tiếp.
4. **Không** coi `exchange_reachable` OK là đủ để kết luận "hệ thống sẵn
   sàng" ở bất kỳ đâu khác trong vận hành (dashboard, alert, quyết định
   thủ công) — luôn nhìn cả hai check.

---

## CLOCK_DRIFT — đồng hồ máy lệch so với sàn

**Triệu chứng:** giống HỆT mục "Xác thực sàn thất bại" ở trên —
`exchange_reachable` OK nhưng request ký (order, `fetch_balance`, ...) bị
sàn từ chối. Với Binance cụ thể: lỗi `-1021` ("Timestamp for this request
is outside of the recvWindow"), dễ bị chẩn đoán NHẦM thành key
sai/hết hạn vì bề ngoài (auth thất bại) giống nhau — **luôn kiểm tra lệch
đồng hồ TRƯỚC khi nghi ngờ key** nếu message lỗi có chữ "timestamp"/
"recvWindow"/"-1021".

**Nguyên nhân thường gặp:**
- NTP bị tắt (thủ công hoặc do chính sách hệ thống) — đồng hồ trôi dần,
  có thể tới hàng giây sau vài ngày không đồng bộ.
- Máy vừa ngủ đông/ngủ dậy (suspend/resume) — đồng hồ hệ thống có thể
  không tự đồng bộ lại ngay, đặc biệt trên máy dev/laptop chạy bot thủ
  công (không phải server 24/7).
- Đồng hồ CMOS trôi trên máy chạy lâu không reboot.
- Container không đồng bộ NTP độc lập với host (hiếm nhưng có thể xảy ra
  tuỳ cấu hình Docker/hypervisor).

**Cách phát hiện:** hai lớp kiểm tra, KHÔNG dùng công thức ngây thơ (xem
`monitoring/clock.py` — công thức trừ trực tiếp `server_time - now()` báo
lệch giả, cộng gộp cả round-trip mạng vào kết quả):

- Khởi động: `run_live_loop()` gọi `monitoring.clock.measure_clock_drift()`
  qua `ExchangeClient.get_server_time()` (đã hiệu chỉnh round-trip, trung
  vị 3 lần đo) — **FAIL rõ ràng, thoát exit 1**, in số đo thực tế
  (`drift_ms`/`round_trip_ms`) nếu vượt `monitoring.clock_drift_halt_ms`
  (`config/settings.yaml`, mặc định 2500ms — quá nửa `recvWindow` mặc
  định 5000ms của Binance).
- Mỗi bar trong vòng lặp chính: đo lại, ghi vào `logs/regime.log`
  (`event: clock_check`) — vượt `monitoring.clock_drift_alert_ms` (mặc
  định 1000ms) → `AlertType.CLOCK_DRIFT` (rate limit 15 phút như mọi
  alert khác); vượt `clock_drift_halt_ms` → bot **dừng gửi lệnh mới bar
  đó, giữ nguyên vị thế/stop hiện có** tới khi đo lại thấy đã đồng bộ (KHÔNG
  tự đóng vị thế — một breach stop-loss thật trong đúng bar bị halt sẽ
  không được enforce cho tới bar kế tiếp đồng hồ đã đồng bộ, đánh đổi có
  chủ đích: cố gắng đóng vị thế lúc đồng hồ lệch chỉ tốn một request chắc
  chắn thất bại).

**Cách sửa:**

- **macOS** (máy dev chạy bot thủ công, không phải container):
  System Settings → General → Date & Time → bật "Set date and time
  automatically". Nếu đã bật mà vẫn lệch (thường sau khi máy ngủ dậy lâu):
  tắt rồi bật lại, hoặc `sudo sntp -sS time.apple.com` từ Terminal để ép
  đồng bộ ngay lập tức.
- **Linux/container** (triển khai thật, xem "Trạng thái hiện tại" đầu
  file): xác nhận `systemd-timesyncd`/`chronyd`/`ntpd` đang chạy trên
  HOST (`timedatectl status` — trường `System clock synchronized` phải
  `yes`). Container KHÔNG có đồng hồ riêng — nó dùng đồng hồ của host qua
  kernel, sửa NTP ở container không có tác dụng nếu host đang sai.

**TUYỆT ĐỐI KHÔNG** bật ccxt `options={'adjustForTimeDifference': True}`
để "vá" việc này — xem docstring `monitoring/clock.py` cho lý do đầy đủ:
cờ đó giấu triệu chứng (request vẫn đi lọt) mà không sửa nguyên nhân
(đồng hồ hệ thống vẫn sai), và một hệ thống KHÁC đọc cùng chiếc máy đó
(log timestamp, cron job khác) vẫn sẽ tin vào giờ sai.

Sau khi sửa: chạy lại `python ops/health_check.py` (WARN sớm ở >1000ms,
công thức ngây thơ — chỉ để heads-up nhanh, không phải nguồn quyết định
chính thức) rồi `python main.py --dry-run` để xác nhận bước kiểm tra
khởi động (FAIL cứng ở >2500ms) qua được.

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

## Triển khai thay đổi công thức `orderLinkId` — CHỈ khi không có lệnh chờ

**Áp dụng cho lần sửa 2026-08-08** (`normalize()` trong
`OrderExecutor.generate_order_link_id`) **và mọi lần đổi công thức hash
về sau.**

`orderLinkId` là khoá chống trùng duy nhất của hệ thống (CLAUDE.md bất
biến #8). Đổi công thức hash làm **MỌI id thay đổi** — cùng một
`(symbol, bar_timestamp, target_allocation)` sinh ra id khác trước và sau
khi triển khai.

**Hệ quả:** trong đúng cửa sổ chuyển đổi, một lệnh đã gửi bằng bản CŨ
đang chờ khớp trên sàn sẽ **không được bản MỚI nhận ra**. Bot tính lại
cùng quyết định đó, sinh id mới, sàn thấy một lệnh hoàn toàn khác và
**khớp cả hai** — đúng kịch bản nhân đôi vị thế mà `orderLinkId` sinh ra
để chặn. Lớp chống trùng mất tác dụng đúng tại thời điểm chuyển đổi, và
chỉ tại thời điểm đó.

Quy trình bắt buộc trước khi deploy:

```bash
# 1. Không còn lệnh nào đang chờ trên sàn
python -c "
from main import load_settings, build_exchange_client
s = load_settings()
c = build_exchange_client(s, testnet=s['exchange']['testnet'])
print(c.get_open_orders())
"
```

2. Kết quả phải là danh sách **rỗng**. Còn lệnh → huỷ hết, hoặc chờ khớp
   xong, rồi mới deploy.
3. Dừng container **trước** khi build image mới — không rolling update:
   hai tiến trình chạy hai công thức hash khác nhau trên cùng một tài
   khoản là đúng thứ mục "Khôi phục sau crash" §3 nói phải báo ngay.
4. Deploy, khởi động lại, xác nhận `get_open_orders()` vẫn rỗng ở bar đầu
   tiên trước khi để bot chạy không giám sát.

Thời điểm an toàn nhất: ngay sau khi một bar đã xử lý xong và trước bar
kế tiếp (bar 1D, ranh giới 00:00 UTC — CLAUDE.md bất biến #10), lúc đó
gần như chắc chắn không có lệnh chờ.

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
