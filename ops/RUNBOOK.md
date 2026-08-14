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

## `health.json` — `status: degraded`

**Triệu chứng:** `${STATE_DIR}/health.json` có `"status": "degraded"`, hoặc
một cảnh báo `HEALTH_CHECK_FAILED` mức WARNING 60 giây sau khi bot khởi động
(`monitoring/health.py::assert_healthy_or_alert`, Phase 12b §B.3).

**Đọc `reasons` TRƯỚC.** File tự nói vì sao nó không phải `ok` — đừng đoán
từ triệu chứng, đừng đi kiểm tra mạng theo phản xạ:

```bash
python -c "import json,os,pathlib;p=pathlib.Path(os.environ.get('STATE_DIR','state'))/'health.json';d=json.loads(p.read_text());print(d['status']);[print(' -',r) for r in d['reasons']]"
```

`degraded` nghĩa là **bot vẫn đang chạy và vẫn đang giao dịch**, chỉ là một
điều kiện đã lệch khỏi bình thường. Nó KHÔNG phải lý do dừng bot. Bốn
nguyên nhân, theo đúng thứ tự hay gặp:

1. **`Chậm N bar` (`bars_behind` 1–2)** — bar mới nhất đã đóng nhưng bot
   chưa xử lý xong. Bình thường trong vài phút đầu sau 00:00 UTC hoặc sau
   một lần khởi động lại: vòng poll 60s cần vài nhịp để bắt kịp. Tự khỏi.
   **Can thiệp tay khi:** còn `degraded` sau 30 phút, hoặc `bars_behind`
   tăng dần qua các lần đọc. Lúc đó xem mục "Mất dữ liệu giá" bên trên.

2. **`Lệch đồng hồ ...ms`** — vượt `monitoring.clock_drift_alert_ms`
   (1000ms) nhưng chưa tới ngưỡng dừng lệnh (`clock_drift_halt_ms`, 2500ms).
   Xem mục **CLOCK_DRIFT** bên trên để xử lý; **can thiệp NGAY**, đừng chờ:
   khoảng cách giữa 1000 và 2500ms rất hẹp và khi vượt 2500 bot ngừng gửi
   lệnh mới hoàn toàn.

3. **`Có lệnh chưa khớp ...s`** — một lệnh treo quá
   `monitoring.unfilled_order_degraded_seconds` (300s). Đáng lo vì
   `execution.order_timeout_seconds` là 30s: lệnh lẽ ra đã bị huỷ từ lâu.
   **Can thiệp NGAY** — đây là dấu hiệu vòng huỷ không chạy, không phải
   dấu hiệu thị trường chậm khớp. Kiểm tra lệnh mở trên sàn, đối chiếu
   `logs/trades.log`, huỷ tay nếu cần.

4. **`Model HMM cũ N ngày`** — quá 2× `hmm.retrain_interval_days` (14
   ngày). Retrain đã thất bại im lặng nhiều lần liên tiếp. Xem mục **HMM
   retrain lỗi** bên trên. **Can thiệp trong ngày**, không khẩn cấp: bot
   vẫn ra quyết định, nhưng bằng một model không còn phản ánh chế độ thị
   trường hiện tại — càng để lâu càng khó biết các quyết định gần đây có
   giá trị gì.

**Không có nguyên nhân nào ở trên mà vẫn `degraded`?** `reasons` là danh
sách vét cạn — rỗng mà status không phải `ok` nghĩa là `health.json` và
`monitoring/health.py::evaluate` đã lệch nhau. Đó là bug, xem mục
"`invariant_violations`" bên dưới.

---

## `health.json` — `status: down`

**Triệu chứng:** `"status": "down"`, hoặc cảnh báo `HEALTH_CHECK_FAILED`
mức **CRITICAL**.

`down` nghĩa là **bot có thể vẫn còn tiến trình sống nhưng không còn giao
dịch được**. Ba nguyên nhân, loại trừ nhau:

1. **`Circuit breaker đang halt (...)`** — KHÔNG phải sự cố kỹ thuật. Đây
   là hệ thống phòng thủ đang làm đúng việc của nó. Đi thẳng tới mục
   **"Circuit breaker kích hoạt"** bên trên và làm theo level. Đặc biệt:
   **đừng khởi động lại bot để "cho hết down"** — với `PEAK_HALT` thì
   `ops/entrypoint.sh` sẽ từ chối khởi động, và nếu bằng cách nào đó nó
   khởi động được thì bạn vừa vô hiệu hoá đúng cơ chế đã chặn một chuỗi
   thua lỗ.

2. **`API không phản hồi ở lần gọi gần nhất`** — lần gọi sàn gần nhất ném
   lỗi. Xem mục **"Mất dữ liệu giá"** và **"Xác thực sàn thất bại"** bên
   trên (hai nguyên nhân khác nhau, cùng triệu chứng ở đây — `logs/main.log`
   phân biệt được: lỗi mạng/timeout vs `-2015`/`-2014`/`AuthenticationError`).
   Bot tự thử lại mỗi 60s; **can thiệp tay khi** quá 15 phút không tự khỏi.

3. **`Chậm N bar (> 2)`** — quá hai chu kỳ bar mà không xử lý được bar
   nào. Với bar 1D nghĩa là **hơn hai ngày im lặng**. Đây là chế độ hỏng
   đã thực sự xảy ra ngày 2026-08-06..08 (forward test dừng im lặng ba
   ngày, xem `docs/DECISIONS.md`). Kiểm tra theo thứ tự:
   - Tiến trình còn sống không? (`docker compose ps`, hoặc `launchctl print`
     cho job forward test)
   - `logs/main.log` dừng ở đâu, dòng cuối nói gì?
   - `${STATE_DIR}/state_snapshot.json` — `written_at_utc` là bao giờ?
   - Nếu tiến trình đã chết: khởi động lại rồi đọc mục **"Khôi phục sau
     crash"**. Bot tự tua lại các bar bị lỡ (`_pending_bar_dates`), chỉ bar
     cuối được phép đặt lệnh.

**Đừng xoá `health.json` để "reset trạng thái".** Nó được ghi đè mỗi chu
kỳ poll; xoá nó chỉ làm mất bằng chứng của lần hỏng vừa rồi và không đổi
được gì trong hành vi của bot.

---

## `health.json` — có trường `invariant_violations`

**Triệu chứng:** `health.json` chứa khoá `invariant_violations`, và/hoặc
một cảnh báo `INTERNAL_ERROR` mức CRITICAL.

**Đây KHÔNG phải sự cố vận hành. Đây là bug trong code.** `status` có thể
vẫn là `ok` — có chủ ý: `degraded`/`down` nghĩa là "chờ hoặc thử lại", còn
vi phạm bất biến nghĩa là "phải sửa code". Trộn hai thứ lại thì bug được
xử lý bằng cách chờ, tức là không bao giờ được xử lý.

Nội dung vi phạm hiện chỉ có một loại: `final_allocation != min(hmm_allocation,
trend_gate_cap, risk_manager_cap)` — CLAUDE.md **bất biến #2**, nguyên tắc
lõi của toàn bộ mô hình phòng thủ nhiều lớp.

**Can thiệp NGAY, thủ công:**

1. **Dừng bot.** Đây là một trong rất ít trường hợp đáng dừng: một tầng
   phòng thủ đang không giới hạn được thứ nó phải giới hạn, và mỗi bar
   tiếp theo là một lệnh đặt dưới giả định sai.
2. Ghi lại `health.json` nguyên văn (bằng chứng — nó sẽ bị ghi đè sau 60
   giây nếu bot còn chạy).
3. `pytest tests/test_properties.py tests/test_wiring_equivalence.py` —
   hai file này canh đúng bất biến đó ở tầng đơn vị. Nếu chúng xanh trong
   khi hệ thống thật vi phạm, khoảng trống nằm ở phần NỐI DÂY, không phải
   ở công thức.
4. Không khởi động lại cho tới khi tìm ra nguyên nhân.

---

## WATCHDOG_KILL — watchdog đã kết thúc bot

**Triệu chứng:** cảnh báo `WATCHDOG_KILL` mức CRITICAL, và
`${STATE_DIR}/watchdog_kill.json` tồn tại.

**Bot KHÔNG tự khởi động lại. Đó là thiết kế.** Watchdog giết bot rồi
supervisor bật lại ngay sẽ tạo một vòng lặp crash mà không ai để ý — bot
chết và sống lại cả ngàn lần, mỗi lần để lại một trạng thái dở dang, và
biểu đồ uptime trông hoàn hảo.

```bash
python -c "import json,os,pathlib;p=pathlib.Path(os.environ.get('STATE_DIR','state'))/'watchdog_kill.json';d=json.loads(p.read_text());print(d['killed_at_utc'],d['reason']);print(d['detail']);print('tín hiệu:',d['signal_used'])"
```

Ba `reason`, ba nguyên nhân khác nhau:

| `reason` | Nghĩa | Nhìn vào đâu |
|---|---|---|
| `heartbeat_stale` | Tiến trình còn sống nhưng không tiến — deadlock, hoặc kẹt trong một lời gọi mạng không timeout | `logs/main.log` dòng cuối; `last_heartbeat.bar_ts` cho biết kẹt ở bar nào |
| `loop_seq_stuck` | Heartbeat vẫn được ghi (mtime tươi) nhưng vòng lặp đứng — nặng hơn `stale`, nghĩa là có luồng còn sống nhưng luồng chính kẹt | Cùng chỗ; nghi ngờ deadlock giữa hai luồng |
| `pid_gone` | Bot đã chết trước khi watchdog kịp làm gì | Log hệ thống: OOM killer, panic, `docker logs` |

`signal_used`: `SIGTERM` = bot thoát sạch, `state_snapshot.json` nhiều khả
năng còn đúng. `SIGKILL` = bot **không** phản hồi trong 30 giây; snapshot
có thể cũ hơn thực tế trên sàn.

**Bắt buộc trước khi khởi động lại:**

```bash
python scripts/recovery_checklist.py    # thoát khác 0 nếu có mục NGHIÊM TRỌNG
```

Xoá `watchdog_kill.json` sau khi đã đọc — file này bị ghi đè ở lần kill
tiếp theo, nên nó không phải nhật ký.

---

## DATA_QUALITY — `data_quality.lock` tồn tại

**Triệu chứng:** cảnh báo `DATA_QUALITY_FAILED`, log bot lặp
`data_quality.lock tồn tại — DỪNG sinh signal`.

**Bot vẫn chạy, vẫn giữ vị thế và stop, chỉ không sinh signal mới.** Nó bỏ
qua TOÀN BỘ việc xử lý bar — kể cả enforce stop-loss, có chủ ý: nhánh đó
so giá bar với stop, và giá bar chính là thứ vừa bị tuyên bố là không tin
được. Đóng vị thế theo một mức giá sai là hiện thực hoá một khoản lỗ chưa
từng xảy ra.

```bash
python -c "import json,os,pathlib;p=pathlib.Path(os.environ.get('STATE_DIR','state'))/'data_quality.lock';d=json.loads(p.read_text());[print(f\"{v['check']:28} {v['bar']:26} {v['detail']}\") for v in d['violations']]"
```

**`LARGE_PRICE_MOVE` KHÔNG khoá bot.** Nếu bạn thấy cảnh báo đó mà không
thấy lock, đó là đúng hành vi: giá nhảy >30% đã được xác nhận bởi nguồn
thứ hai, tức là biến động THẬT. Bot phải chạy tiếp — đó là lúc trend gate
hạ trần và risk manager cắt size, chính là thứ hệ thống được xây để làm.

**Xử lý:** điều tra nguồn dữ liệu (`data/history_loader.py` cache? sàn trả
sai?), sửa, rồi `rm ${STATE_DIR}/data_quality.lock` **bằng tay**. Không tự
hết hạn — một lock tự hết hạn nghĩa là nguyên nhân không bao giờ bị điều
tra.

---

## EMERGENCY_KILL — dừng khẩn cấp thủ công

```bash
python scripts/emergency_kill.py --reason "mô tả ngắn vì sao"
```

Nó làm: ghi `trading_halted.lock` → huỷ lệnh VÀO/REBALANCE → **giữ nguyên
mọi lệnh bảo vệ** → **KHÔNG đóng vị thế spot** → SIGTERM bot, 30s, SIGKILL
→ in tóm tắt.

**Đọc kỹ cảnh báo cuối bản in nếu còn vị thế.** Stop-loss của hệ thống này
**không nằm trên sàn** — `modify_stop()` chỉ ghi vào bộ nhớ tiến trình,
enforce do vòng lặp bot làm mỗi bar. Sau khi script chạy xong, vị thế còn
nguyên và **không còn gì canh nó**. Hoặc đặt stop thủ công trên sàn ngay,
hoặc theo dõi tay tới khi khởi động lại.

Vì sao không đóng vị thế: đóng trong hoảng loạn là hiện thực hoá khoản lỗ
ở đúng thời điểm tệ nhất, và nó mâu thuẫn với luận điểm của hệ thống —
giảm tỷ trọng theo biến động, không thoát sạch.

---

## Chạy watchdog và data harness — launchd (macOS) / systemd (Linux)

Cả hai là **tiến trình riêng**, không phải thread trong bot. Một tiến
trình bị treo không tự phát hiện được rằng nó đang treo.

### macOS — launchd

`launchd` có `KeepAlive` (khởi động lại khi tiến trình **thoát**) nhưng
**không phát hiện được tiến trình treo**. Không có tương đương
`systemd Type=notify` + `WatchdogSec`. Nên trên macOS bắt buộc phải chạy
`monitoring/watchdog.py` như một job riêng:

```xml
<!-- ~/Library/LaunchAgents/com.regime-trader.watchdog.plist -->
<key>ProgramArguments</key>
<array>
  <string>/ĐƯỜNG/DẪN/TUYỆT/ĐỐI/.venv/bin/python</string>
  <string>-m</string><string>monitoring.watchdog</string>
</array>
<key>WorkingDirectory</key><string>/ĐƯỜNG/DẪN/TUYỆT/ĐỐI/regime-trader-crypto</string>
<key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>.../logs/watchdog.out.log</string>
<key>StandardErrorPath</key><string>.../logs/watchdog.err.log</string>
```

`StandardErrorPath` **bắt buộc**: một job không có nó thì lỗi không để lại
dấu vết nào — đó là cách sự cố forward test ẩn được 3 ngày (2026-08-06 →
08-08).

Cùng khuôn cho `monitoring.data_harness`.

Kiểm job đang chạy thật (đọc `runs` và `last exit code`, không chỉ "có
trong danh sách"):

```bash
launchctl print gui/$(id -u)/com.regime-trader.watchdog | grep -E "state|runs|last exit"
```

### Linux — systemd, tốt hơn

Dùng `Type=notify` + `WatchdogSec=60` + `sd_notify` cho chính tiến trình
bot: **kernel** giám sát, không phải một tiến trình Python khác cũng có
thể chết. `monitoring/watchdog.py` chỉ là đường thay thế cho macOS.

```ini
[Service]
Type=notify
WatchdogSec=60
Restart=no          # KHÔNG tự khởi động lại — xem mục WATCHDOG_KILL
```

`Restart=no` là có chủ ý và trùng lý do watchdog không tự bật lại bot.

---

## Kiểm cấu hình trước khi khởi động

```bash
python config/validate.py              # đầy đủ, cần credential
python config/validate.py --skip-env   # CI, không có credential thật
```

Kiểm 10 section bắt buộc, biến môi trường (**rỗng cũng là thiếu**), hash
`forward/config_frozen.yaml`, cờ `testnet`, và 6 bất biến `CLAUDE.md`
bằng **AST** (không grep — grep bắt nhầm docstring đang giải thích tại sao
cấm `predict()`, và cách sửa duy nhất là viết lại docstring cho vừa công
cụ).

---

## Vì sao KHÔNG dùng blue-green (quyết định kiến trúc)

Người đọc sau này sẽ muốn đảo ngược quyết định này. Đọc hết mục này trước.

Blue-green giả định các instance **không chia sẻ trạng thái**. Với bot
giao dịch, trạng thái thật nằm ở **SÀN**, không nằm trong tiến trình. Hai
instance cùng quản một tài khoản sẽ tính rebalance độc lập và cùng gửi
lệnh.

`orderLinkId` sinh deterministic chỉ chặn lệnh trùng khi hai instance tính
ra **CÙNG** allocation. Nếu chúng khác nhau — mà **khác nhau chính là lý
do bạn đang deploy** — cả hai lệnh đều qua sàn, và vị thế nhân đôi.

Vì vậy: **không bao giờ hai tiến trình cùng khả năng đặt lệnh trên một tài
khoản** (`CLAUDE.md` bất biến #20).

Mô hình thay thế:

| Giai đoạn | Cơ chế |
|---|---|
| Kiểm logic | So sánh ngoại tuyến tất định trên toàn bộ dữ liệu lịch sử — `ops/compare_versions.py` |
| Kiểm tầng sàn | Shadow mode CHỈ-ĐỌC 24–48 giờ — `ops/shadow_runner.py` |
| Chuyển đổi | Restart MỘT instance duy nhất, bàn giao qua `state_snapshot.json` |
| Rollback | `git checkout <ref cũ>`, restart |

Shadow mode **không** dùng để kiểm logic: `compare_versions` làm việc đó
tốt hơn nhiều (hàng nghìn bar thay vì vài chục, tái lập được, vài chục
giây). Shadow chỉ trả lời những câu backtest không trả lời được — phản hồi
API thật, `instrumentRules` thật, lệch đồng hồ, mạng chập chờn.

---

## Triển khai phiên bản mới — CHECKLIST

Không bỏ bước. Bước 2 và 5 là hai cổng; bước 6 là điều kiện thời điểm.

```
 1. pytest && pytest -m slow              -> cả HAI đều xanh
                                             (một lệnh không phải "toàn bộ")
 2. python -m ops.compare_versions \
      --ref-a <ref đang chạy> --ref-b <ref mới>
                                          -> khớp 100%, HOẶC lệch CÓ CHỦ ĐÍCH
                                             đã ghi docs/DECISIONS.md kèm bảng
                                             hiệu năng cũ/mới
 3. pytest tests/regression_harness.py -m slow   -> xanh
 4. pytest tests/test_forward_golden.py          -> xanh
 5. python -m ops.shadow_runner   (24–48 giờ, song song với --dry-run)
    python -m ops.shadow_diff                     -> 4 trường chính khớp 100%
                                                     và đủ >= 24h bar CHUNG
 6. python -m ops.deploy_conditions               -> đủ điều kiện thời điểm
    + TỰ TRẢ LỜI: "Tôi có mặt được 2 GIỜ tới không?"  (§E.3, không đo được)
 7. Dừng instance production. Xác nhận state_snapshot.json đã ghi.
 8. git checkout <ref mới> && restart
 9. Theo dõi 2 GIỜ:
      cat ${STATE_DIR}/health.json    -> status "ok"
      cat ${STATE_DIR}/drift.json     -> không cảnh báo
      cat ${STATE_DIR}/heartbeat.json -> loop_seq TĂNG
10. Ghi docs/DECISIONS.md: ref cũ, ref mới, ngày, kết quả so sánh.
```

**Rollback:** `git checkout <ref cũ>` rồi restart. Trạng thái đọc lại từ
`state_snapshot.json` và **đối soát với sàn** — chạy
`python scripts/recovery_checklist.py`, cùng cơ chế dùng cho khôi phục sau
crash. Nhớ: stop-loss KHÔNG nằm trên sàn (xem mục riêng), nên khoảng thời
gian giữa dừng và khởi động lại là khoảng vị thế KHÔNG được canh.

### Điều kiện thời điểm — không dùng lịch (§E)

Luật "không deploy tối Thứ Sáu" là luật của thị trường CÓ giờ đóng cửa:
nó tồn tại vì cuối tuần không ai trực. Crypto chạy 24/7, nên mang luật đó
vào đây là mang theo một giả định đã chết — và nó cho cảm giác an toàn vào
Thứ Ba lúc thị trường đang sập.

Ba điều kiện đo được thay cho nó:

| | Điều kiện | Ai kiểm |
|---|---|---|
| E.1 | Biến động 24h dưới phân vị 80 lịch sử (ngưỡng đo được: **3.561%**, chặn **20%** số ngày) | `ops/deploy_conditions.py` |
| E.2 | Không lệnh đang chờ, không circuit breaker hoạt động, không `trading_halted.lock` | `ops/deploy_conditions.py` |
| E.3 | **Bạn có mặt được ít nhất 2 giờ tới** | **CON NGƯỜI** — không đo được |

E.3 là điều kiện THẬT đằng sau luật Thứ Sáu. `deploy_conditions.py` in câu
hỏi đó ở mọi lần chạy, kể cả khi mọi thứ khác đều ĐẠT — đó là lúc dễ bỏ
qua nhất.

Lưu ý về `ok = ???` trong báo cáo: đó là **không xác định được**, KHÔNG
phải "đạt". Ví dụ không hỏi được sàn về lệnh mở. Cổng chỉ xanh khi mọi
điều kiện `ĐẠT`.

**Phải chờ tối đa bao lâu?** Đo trên 3137 bar: các ngày bị E.1 chặn đến
thành 456 chuỗi liên tiếp, chuỗi **dài nhất 6 ngày**, p95 = 3 ngày, trung
vị 1 ngày. Ba chuỗi 6 ngày đều rơi vào đợt sập lớn (2021-01-10,
2020-03-12, 2019-06-25) — cổng chặn đúng lúc, không chặn ngẫu nhiên.

Vì chuỗi dài nhất là 6 ngày (dưới ngưỡng cân nhắc 14), **KHÔNG có lối
thoát ghi đè** cho E.1. Cần can thiệp gấp trong lúc bị chặn thì đó là sự
cố, không phải deploy — dùng `scripts/emergency_kill.py`. Nếu sau này phép
đo cho chuỗi > 14 ngày,
`tests/test_deploy_conditions.py::test_chuoi_chan_lien_tiep_dai_nhat_la_6_ngay`
sẽ đỏ và yêu cầu dựng lối thoát CÓ KIỂM SOÁT — người vận hành sẽ tự chế ra
một cái lúc 2 giờ sáng nếu không có.

### Cổng DEPLOY khác cổng MERGE

```bash
python ops/readiness_gate.py --base origin/main                 # MERGE (CI dùng)
python ops/readiness_gate.py --base origin/main --scope deploy  # DEPLOY
```

`--scope deploy` cộng thêm mọi **mục nghiệm thu chưa xác nhận được** và ĐỎ
nếu còn mục nào. Hiện có 1: Phase 12c #4 (shadow 24h thật) — chờ testnet
Binance hoạt động lại. Tách hai cổng là có chủ ý: gộp lại sẽ làm CI đỏ vì
một lý do không liên quan tới diff đang xét, và một CI đỏ vì lý do không
liên quan sẽ bị bỏ qua.

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

# Sức khoẻ lúc chạy: status + lý do (monitoring/health.py, ghi mỗi chu kỳ poll)
python -c "import json,os,pathlib;p=pathlib.Path(os.environ.get('STATE_DIR','state'))/'health.json';d=json.loads(p.read_text());print(d['status'],'|',d['updated_at']);[print(' -',r) for r in d['reasons']]"

# Tầng nào đang giới hạn allocation (bốn trường, không chỉ final)
python -c "import json,os,pathlib;p=pathlib.Path(os.environ.get('STATE_DIR','state'))/'health.json';d=json.loads(p.read_text());[print(f'{k:18} {d[k]}') for k in ('hmm_allocation','trend_gate_cap','risk_manager_cap','final_allocation')]"
```
