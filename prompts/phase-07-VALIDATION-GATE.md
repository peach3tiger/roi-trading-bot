# Phase 7 — ĐIỂM DỪNG: Kiểm định chống overfit

Đọc `docs/Brain-Crypto-Bybit.md` §4.8 và §4.9.

**Đây không phải một phase xây dựng. Đây là điểm quyết định có nên tiếp tục dự án hay không.**

Bạn có ~8 năm dữ liệu BTC, chứa 2 chu kỳ. Cỡ mẫu cực nhỏ. Walk-forward thông thường tạo cảm giác an toàn giả vì các cửa sổ OOS chồng lấn về chế độ thị trường.

## Bốn bài kiểm định

### a. Giai đoạn 2022 (`--period 2022`)
Bear kéo dài, vol giảm dần. Bài test khắc nghiệt nhất với một hệ thống phân loại theo volatility.
Báo cáo: return, max DD, phân bố allocation, so với buy-and-hold cùng kỳ.

### b. Độ nhạy mốc đóng bar (`--bar-offset 0,6,12,18`)
Chạy lại toàn bộ backtest với bar đóng ở 4 mốc UTC khác nhau. Mốc 00:00 là quy ước tuỳ ý — thị trường không đóng cửa.
Sharpe của 4 lần chạy phải chênh nhau **không quá 0.3**.

### c. ETH ngoài mẫu (`--symbol ETHUSDT`)
Chốt **toàn bộ** tham số trên BTC trước. Rồi chạy đúng cấu hình đó trên ETH, **không chỉnh một con số nào**.

### d. Ablation feature (`--ablation`)
Bỏ từng feature ra, đo tác động. Xuất `feature_ablation.csv`.

### e. Có/không trend gate (`--no-trend-gate`)
So sánh riêng 2022, riêng 2020–2021, và toàn kỳ. Tiêu chí giữ gate: cải thiện max DD ≥ 25% với chi phí ≤ 20% CAGR.

## Tiêu chí đi tiếp — phải thoả ĐỦ 8

1. Sharpe OOS > 1.0 sau toàn bộ chi phí
2. Đánh bại buy-and-hold BTC về **Calmar ratio**
3. Đánh bại vol-targeting tĩnh về Sharpe ≥ 0.2
4. Nằm ngoài 2 độ lệch chuẩn của phân phối benchmark ngẫu nhiên
5. Trong 2022 không lỗ nặng hơn buy-and-hold
6. Sharpe của 4 lần bar-offset chênh ≤ 0.3
7. ETH không tune: Sharpe > 0.5
8. Phí < 30% lợi nhuận gộp

## Việc cần làm

Chạy đủ 5 bài kiểm định. Lập bảng đối chiếu 8 tiêu chí, ghi rõ PASS/FAIL từng cái kèm con số thực tế.

Viết `docs/VALIDATION_REPORT.md` chứa toàn bộ kết quả.

**Không được diễn giải kết quả theo hướng có lợi.** Nếu tiêu chí 5 fail, ghi FAIL, không ghi "gần đạt". Nếu tôi có vẻ muốn nghe kết quả tốt, cứ báo cáo đúng con số.

## Kết luận

- **8/8 PASS** → đi tiếp Phase 8
- **6–7/8** → quay lại Phase 3–6, xác định điểm yếu cụ thể, sửa, chạy lại toàn bộ kiểm định
- **< 6/8** → thiết kế có vấn đề cơ bản. Ghi lại kết luận. Cân nhắc dừng.

Đừng xây tầng thực thi cho một chiến lược chưa chứng minh được edge. Đó là cách phần lớn dự án loại này thất bại — không phải vì code sai, mà vì dành ba tháng hoàn thiện phần thực thi cho thứ không có lợi thế nào.
