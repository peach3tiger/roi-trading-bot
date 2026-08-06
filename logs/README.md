# logs/

Log JSON có cấu trúc, xoay vòng (`monitoring/logger.py` — chưa implement
tại thời điểm tạo thư mục này): `main.log`, `trades.log`, `alerts.log`,
`regime.log`. 10MB/file, giữ 30 ngày (Brain-Crypto-Bybit.md §8.1).

Không commit nội dung thư mục này vào git (`.gitignore` khớp `*.log` ở
mọi cấp) — chỉ `README.md` này được track, để thư mục tồn tại sẵn cho
volume mount của `ops/docker-compose.yml`.
