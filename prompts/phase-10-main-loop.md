# Phase 10 — Main Loop & Orchestration

Đọc `docs/Brain-Crypto-Bybit.md` PHASE 7.

## Việc cần làm

Implement `main.py` theo trình tự khởi động và vòng lặp trong spec.

Điểm khác biệt lớn nhất so với bot equity: **không có giờ giao dịch.** Không `is_market_open()`, không chờ mở cửa. Vòng lặp chạy vĩnh viễn.

Vì vậy:
- Ghi `state_snapshot.json` **mỗi bar**, không chỉ khi thoát
- Kiểm tra `trading_halted.lock` lúc khởi động, có thì in lý do và thoát
- Đối soát vị thế với sàn lúc khởi động, tin sàn khi lệch
- Tắt: đóng WebSocket, **không** đóng vị thế, ghi snapshot

Xử lý lỗi: API 3 lần retry backoff; rate limit (`retCode 10006`) backoff không coi là lỗi nghiêm trọng; lỗi HMM giữ regime cũ; mất feed tạm dừng signal nhưng giữ stop.

CLI đủ các cờ trong spec, `--testnet` mặc định.

Viết file service `systemd` mẫu với auto-restart.

## Nghiệm thu

- [ ] `python main.py --dry-run` chạy full pipeline, log signal, không đặt lệnh nào
- [ ] `grep -rn "is_market_open\|market_hours" .` — không có kết quả
- [ ] Chạy `--dry-run`, kill process giữa chừng, chạy lại → khôi phục đúng trạng thái từ snapshot
- [ ] Tạo `trading_halted.lock` thủ công → khởi động phải thoát ngay với thông báo rõ ràng
- [ ] Mô phỏng mất WebSocket → hệ thống phát hiện trong vòng 2 chu kỳ bar và cảnh báo
- [ ] Chạy `--dry-run` liên tục 24 giờ trên testnet, không crash, không rò rỉ bộ nhớ
