# regime-trader-crypto

Bot phân bổ danh mục theo chế độ biến động (HMM), BTC/USDT spot trên Bybit.
Luôn long, không bao giờ short — giảm tỷ trọng khi biến động cao thay vì
đảo chiều.

- Spec đầy đủ: [`docs/Brain-Crypto-Bybit.md`](docs/Brain-Crypto-Bybit.md)
- Quy tắc bất biến (đọc trước mỗi phiên làm việc): [`CLAUDE.md`](CLAUDE.md)
- Prompt từng phase: [`prompts/`](prompts/)

## Trạng thái

Đang ở **Phase 1 — Scaffolding**. Chỉ có skeleton (import, class stub,
type hint, docstring). Không có logic thật ở đâu trong codebase.

## Kiến trúc tầng

```
HMM regime  →  StrategyOrchestrator  →  ┐
StructuralTrendGate (cap)              ├→  min() → RiskManager (veto) → OrderExecutor
                                        ┘
```

Mỗi tầng chỉ được **giảm** tỷ trọng — `final_allocation = min(...)`, không
bao giờ `max()`. Risk manager có quyền phủ quyết tuyệt đối và độc lập hoàn
toàn với HMM.

## Cài đặt

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env                              # điền credentials thật
cp config/credentials.yaml.example config/credentials.yaml
```

## Kiểm tra

```bash
python -c "import core, broker, data, backtest, monitoring"
pytest tests/ --collect-only
ruff check .
python -c "import yaml; yaml.safe_load(open('config/settings.yaml'))"
```

## Testnet là mặc định

`config/settings.yaml: exchange.testnet: true`. Chuyển sang mainnet yêu
cầu gõ tay chuỗi xác nhận đầy đủ — xem `broker/bybit_client.py`.

## Tiêu chí đi tiếp sau Phase 4

Sẽ được ghi vào đây **trước khi** chạy backtest đầu tiên, theo §4.9 của
spec — không được quyết định tiêu chí sau khi đã nhìn kết quả.
