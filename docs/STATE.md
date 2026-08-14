# STATE — bàn giao trạng thái

**File này chỉ chứa TRẠNG THÁI HIỆN TẠI.** Bất cứ thứ gì mô tả điều đã xảy
ra thuộc về `DECISIONS.md`. Vượt một trang = có lịch sử lẫn vào, cắt bớt.
Khi một giả thuyết bị bác bỏ, **XOÁ** nó khỏi file này ngay, đừng để lại.

Lý do của mọi quyết định + "quy tắc đã học, không lặp lại":
`docs/DECISIONS.md`. Kết quả kiểm định: `docs/VALIDATION_REPORT.md`.
Vận hành: `ops/RUNBOOK.md`. Bất biến: `CLAUDE.md`.

## Đang ở đâu

Phase 1–11 + 12b xong. Phase 12c, 12d chưa xây.

```
pytest              731 passed / 6 deselected     # KHÔNG phải "tất cả"
pytest -m slow      6 passed                      # bộ đầy đủ là HAI lệnh
ruff check .        86/86 file .py
mypy .              86/86 file .py
python ops/verify_scope.py                        # in PHẠM VI của cả ba
```

## Chạy cái gì

| Việc | Lệnh |
|---|---|
| Bot | `python main.py --live-loop` (testnet mặc định) |
| Forward test | `python -m forward.runner` — **KHÔNG** phải `forward.logger` |
| Đọc dữ liệu forward | `forward.runner.load_all_bars()` (gộp v1+v2) |
| Cổng trước merge | `pytest -m slow && python ops/readiness_gate.py --base origin/main` |
| Drift | `python -m monitoring.drift` |
| Digest | `python -m monitoring.daily_digest` |

`${STATE_DIR}` (mặc định `./state`, gitignore) chứa: `state_snapshot.json`,
`trading_halted.lock`, `status.json` (sức khoẻ kênh alert), `health.json`,
`drift.json`.

## Bị chặn

**Testnet chặn ở tầng tài khoản GitHub** — không phải lỗi Binance, không
phải lỗi code (`exchange_reachable` OK, 155–178ms). Khi bị chặn, xác định
ĐÚNG lớp bị chặn trước khi debug; đừng sửa `CCXTClient`/`health_check`/
`main.py` cho việc này.

Chặn các nghiệm thu cần mạng thật: `CCXTClient` submit/cancel/idempotency,
kill+restart thật, `--dry-run` 24h, dashboard/Telegram thật.

## Việc còn treo, theo thứ tự ưu tiên

0. **`.env` có `TELEGRAM_BOT_TOKEN=`/`TELEGRAM_CHAT_ID=` RỖNG** → mọi
   watchdog phát hiện đúng nhưng **không gửi được cảnh báo nào**. Điểm mù
   còn lại của chính cơ chế dựng ra để chống điểm mù. Không phụ thuộc
   testnet — làm được bất cứ lúc nào có token.
1. Điền `EXCHANGE_API_KEY`/`EXCHANGE_API_SECRET` + nghiệm thu qua mạng
   thật — **chờ hết chặn**.
2. `main.py --dashboard` chưa wire vào CLI. Cần chốt trước: lưu thêm field
   vào `state_snapshot.json` hay tính lại mỗi lần render.
3. `AlertType.STABLECOIN_DEPEG` chưa wire — cần chọn nguồn giá USDT/USD
   đáng tin trên Binance spot. Không bịa bằng cặp proxy chưa kiểm chứng.
4. Phase 12c (`prompts/phase-12c-shadow-deploy.md`). Xây nó cũng đóng luôn
   mục nghiệm thu ĐẠT-một-cách-rỗng ở dưới.
5. `.github/workflows/ci.yml` **chưa từng chạy trên GitHub thật**. Lần
   push tới là lần đầu — đọc kết quả trước khi tin nó.

## Cạm bẫy đang mở

- **Mục nghiệm thu ĐẠT một cách RỖNG:**
  `grep -rn "order_executor|submit_order" ops/shadow_runner.py` (Phase 12c)
  hiện ĐẠT vì file **không tồn tại**. `ops/verify_scope.py` in nó ra kèm
  nhãn `CHƯA XÂY`. Đóng khi xây 12c.
- **`pytest` một lệnh không phải "toàn bộ xanh"** — 6 test `slow` bị loại
  bởi `addopts = "-m 'not slow'"`.
- **Drift phân bố allocation không có sức phát hiện dưới 365 bar.** Đừng
  đọc "drift im lặng" thành "hành vi khớp baseline". Đo được: ở cửa sổ
  30–182 bar nó không phân biệt được bot hỏng hoàn toàn với hoạt động
  bình thường. Đủ dữ liệu từ 2027-08-06.
- **Ba tầng hồi quy, đừng để trùng nhau:** `test_forward_golden` <1s /
  `test_snapshot` ~8s / `regression_harness` ~137s. Vai trò từng tầng:
  docstring `tests/test_snapshot.py`.

## Ranh giới không được vượt

- `forward/logger.py` + `forward/config_frozen.yaml` **đóng băng**, ghim
  SHA256 trong `tests/golden/frozen_hashes.json`. Đổi = kết thúc thí
  nghiệm forward hiện tại; ghi `DECISIONS.md` TRƯỚC.
- **Không cuộn schema log lần nữa** trong thời gian thí nghiệm
  (`forward/SCHEMA.md`). Cần thêm cột → `forward/extra_<name>.csv` khoá
  theo `bar_date`.
- Không code nào ngoài `forward/` được **ghi** vào `forward/`.
- Mainnet: khoá tới 2027-08-06 **và** §4.9 phải được đánh giá lại trên dữ
  liệu forward (CLAUDE.md #12).

## Việc tiếp theo

Phase 12c (shadow deploy) — không phụ thuộc testnet, làm được ngay.
Song song: điền token Telegram (mục 0), vốn không chờ ai.
