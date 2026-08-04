# Phase 12 — Integration Testing & Documentation

Đọc `docs/Brain-Crypto-Bybit.md` PHASE 9.

## Việc cần làm

### Test tích hợp
- Dry run đầu-cuối: data → feature → HMM → strategy → trend gate → risk → lệnh mô phỏng
- Recovery: kill giữa lúc có lệnh chờ, restart, không đặt trùng
- Idempotency: gửi cùng signal hai lần, một lệnh duy nhất
- Risk stress: signal cực đoan bị cap, lệnh dồn dập bị chặn, lệnh không stop bị từ chối
- Testnet: đặt/sửa/huỷ lệnh, trạng thái sạch

### `README.md`
Theo §9.2 của spec. Bắt buộc có:
- Triết lý: "quản trị rủi ro quan trọng hơn tạo tín hiệu"
- Sơ đồ: `data → features → HMM → vol rank → allocation → trend gate → risk → exchange`
- Phần "Khác biệt so với bản equities" — chép §0 của spec
- **Kết quả kiểm định từ Phase 7** — cả phần đạt lẫn phần không đạt
- Ghi rõ: stop loss ở spot phụ thuộc bot online hay lệnh conditional của sàn (tuỳ kết quả Phase 9), và hệ quả của lựa chọn đó
- Disclaimer: mục đích học tập, crypto biến động cực đoan, sàn có rủi ro đối tác

## Nghiệm thu

- [ ] `pytest tests/ -v` — toàn bộ xanh, không skip, không xfail. Dán nguyên output.
- [ ] Bốn test bắt buộc đều xanh: `test_look_ahead`, `test_precision`, `test_layer_composition`, `test_cost_model`
- [ ] `ruff check . && mypy .` sạch
- [ ] Chạy liên tục 14 ngày trên testnet không can thiệp, xuất báo cáo cuối kỳ
- [ ] README đọc được bởi người chưa biết dự án và họ chạy được từ đầu

## Sau cùng

Trước khi nghĩ tới tiền thật:
1. Testnet tối thiểu 2 tuần liên tục
2. Đọc lại `VALIDATION_REPORT.md` từ Phase 7 một lần nữa
3. Vào mainnet với **số vốn nhỏ nhất bạn chấp nhận mất hoàn toàn**
4. Chạy số vốn đó ít nhất một tháng trước khi tăng
