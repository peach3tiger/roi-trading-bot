# STATE — bàn giao trạng thái

Đọc file này đầu mỗi phiên. Tối đa một trang. Chi tiết ở `DECISIONS.md` và
`VALIDATION_REPORT.md`. Cập nhật ở cuối mỗi phase, ghi đè, không phụ lục thêm.

## Đang ở đâu

- Phase 1–8 xong. Phase 9 (Bybit broker) đã viết code + unit test đầy đủ,
  148 passed / 4 skipped. Nghiệm thu cần mạng thật thì **bị chặn**.
- Forward test chạy từ 2026-08-06, cấu hình đóng băng, launchd hằng ngày.
  Mốc đánh giá: 2026-11-06 / 2027-02-06 / 2027-08-06.
- Cổng: `CLAUDE.md` #12 sửa ngày 2026-08-06 — xây tầng thực thi ở **testnet**
  được, **mainnet** bị chặn tới 2027-08-06.

## Đang bị chặn

API key Bybit testnet không hợp lệ (401 / retCode 10003 "API key is invalid").
Public endpoint OK, mọi endpoint xác thực fail.

Nghi ngờ hàng đầu: key tạo trên `bybit.com` thay vì `testnet.bybit.com` —
testnet là hệ thống tài khoản hoàn toàn riêng.

Chặn nghiệm thu Phase 9 mục 3, 4, 5, 6.

## Việc còn treo, theo thứ tự ưu tiên

1. `tests/test_forward_golden.py` — **chưa có**. Bắt buộc xong **trước Phase 10**,
   vì Phase 10 chạm tầng điều phối mà forward logger dùng chung.
2. `ops/health_check.py` — check `exchange_connectivity` chỉ gọi `fetch_time()`
   (public) nên báo xanh dù key hỏng. Tách thành `exchange_reachable` +
   `exchange_authenticated`.
3. `_call_with_retry` trong `bybit_client.py` — đang là blacklist (retry mọi thứ
   trừ 10006). Đảo thành whitelist: chỉ retry 10006, lỗi mạng/timeout, HTTP 5xx.
4. `orderLinkId` trùng — chưa có nhánh xử lý, sẽ retry 3 lần vô ích rồi raise.
   Cần retCode thật từ test mạng trước khi sửa. Phải log đầy đủ retCode + retMsg
   ở mọi `submit_order` thất bại.
5. 4 test skip trong `test_hmm.py`.
6. Lỗi mypy trong `test_forward_logger.py`.
7. Copy `phase-12b-harness-engineering.md` và `phase-12c-shadow-deploy.md`
   vào `prompts/` (đã soạn, chưa có trong repo).

## Việc tiếp theo

Sửa key testnet → nghiệm thu Phase 9 mục 3–6 → golden test → Phase 10.

## Quy tắc đã học, không lặp lại

- Mọi số đo thị trường lấy từ **testnet không dùng để hiệu chỉnh tham số**.
  Thanh khoản testnet là nhân tạo (spread đo được 0.00015% so với mainnet ~0.01–0.02%).
- Không bao giờ log giá trị key/secret, kể cả một phần.
- Không bao giờ hai tiến trình cùng khả năng đặt lệnh trên một tài khoản.
