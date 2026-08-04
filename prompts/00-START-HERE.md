# Cách chạy bộ prompt này trên Claude Code

## Chuẩn bị (làm một lần)

```bash
mkdir regime-trader-crypto && cd regime-trader-crypto
git init
mkdir -p docs prompts

# copy các file đã tạo vào đúng chỗ
cp ~/Downloads/CLAUDE.md              ./CLAUDE.md
cp ~/Downloads/Brain-Crypto-Bybit.md  ./docs/
cp ~/Downloads/prompts/*.md           ./prompts/

claude
```

`CLAUDE.md` phải nằm ở thư mục gốc. Claude Code tự đọc nó ở mọi phiên — đó là nơi chứa các bất biến không được vi phạm.

## Vòng lặp làm việc

Mỗi phase là một phiên riêng. Đừng chạy nhiều phase trong một phiên — context sẽ đầy và chất lượng giảm rõ rệt.

```
> Đọc CLAUDE.md và docs/Brain-Crypto-Bybit.md. Sau đó thực hiện prompts/phase-01-scaffold.md
```

Kết thúc mỗi phase:

```
> Chạy phần "Nghiệm thu" trong prompt vừa rồi. Báo cáo từng mục pass/fail.
> /clear
```

Rồi commit trước khi sang phase tiếp theo:

```bash
git add -A && git commit -m "Phase 1: scaffold"
```

## Ba mẹo thực tế

**Dùng plan mode cho các phase khó.** Phase 2 (HMM + forward algorithm) và Phase 4 (backtester) là chỗ dễ sai nhất. Gõ `Shift+Tab` hai lần để vào plan mode, để Claude trình bày kế hoạch trước khi viết code.

**Bắt Claude chạy test, đừng tin lời nó.** Sau mỗi phase, yêu cầu cụ thể: `chạy pytest tests/ -v và dán nguyên output`. Báo cáo bằng văn xuôi "đã pass" không thay thế được output thật.

**`/clear` giữa các phase.** Context từ phase trước không giúp gì cho phase sau và làm giảm chất lượng. Spec đã ở trong `docs/`, Claude đọc lại được khi cần.

## Thứ tự

```
phase-01-scaffold.md
phase-02-data-loader.md      ← dữ liệu trước, không có dữ liệu thì không làm gì được
phase-03-hmm-engine.md       ← test_look_ahead.py phải xanh trước khi đi tiếp
phase-04-strategy.md
phase-05-trend-gate.md
phase-06-backtester.md
phase-07-VALIDATION-GATE.md  ← ĐIỂM DỪNG, đọc kỹ file này
phase-08-risk-manager.md
phase-09-bybit-broker.md
phase-10-main-loop.md
phase-11-monitoring.md
phase-12-integration-tests.md
```

Phase 07 là điểm dừng thật. Nếu kết quả không đạt, đừng chạy phase 08. Quay lại 03–06 hoặc dừng dự án.
