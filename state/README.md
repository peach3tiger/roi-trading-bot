# state/

Trạng thái runtime của live loop (Phase 10 — chưa implement):

- `trading_halted.lock` — circuit breaker peak-DD ghi file này
  (`core/risk_manager.py::RiskManager._write_halt_lock`), xoá THỦ CÔNG
  sau khi đã điều tra (xem `ops/RUNBOOK.md`, mục "Circuit breaker kích
  hoạt"). Sống trên volume mount này để sống sót qua container restart —
  đúng thiết kế, không phải bug nếu container không tự khởi động lại
  được sau một lần halt.
- `state_snapshot.json` — ghi mỗi bar (Brain-Crypto-Bybit.md §Phase 7),
  đọc lại lúc khởi động để hồi phục phiên trước.

Không commit nội dung thư mục này vào git (`.gitignore` — cả hai tên file
trên đã bị chặn ở mọi cấp thư mục) — chỉ `README.md` này được track, để
thư mục tồn tại sẵn cho volume mount của `ops/docker-compose.yml`.
