# models/

Model HMM của **live loop** (Phase 10, `prompts/phase-10-main-loop.md` —
chưa implement tại thời điểm tạo thư mục này). Mặc định:
`models/hmm_model.pkl`, đổi qua biến môi trường `MODEL_PATH` (xem
`ops/RUNBOOK.md`).

**Không phải** `forward/state/hmm_model.pkl` — đó là cache riêng của
forward test (thí nghiệm đã đóng băng cấu hình, chạy qua launchd trên
host, xem `forward/README.md`). Hai model này độc lập, train trên lịch
riêng, đừng trỏ nhầm `MODEL_PATH` sang đường dẫn kia.

Nội dung thư mục này (`*.pkl`) không commit vào git (`.gitignore`) — chỉ
file `README.md` này được track, để thư mục tồn tại sẵn cho volume mount
của `ops/docker-compose.yml`.
