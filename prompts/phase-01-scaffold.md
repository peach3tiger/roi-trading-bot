# Phase 1 — Scaffolding & Environment

Đọc `docs/Brain-Crypto-Bybit.md` phần PHASE 1 trước khi bắt đầu.

## Việc cần làm

Tạo cấu trúc project đúng như cây thư mục trong spec PHASE 1. Bao gồm cả các file mới không có trong spec gốc: `core/trend_gate.py`, `broker/base.py`, `broker/ccxt_client.py`, `broker/instrument_rules.py`, `data/history_loader.py`, `backtest/cost_model.py`, `tests/test_precision.py`, `tests/test_cost_model.py`, `tests/test_trend_gate.py`, `tests/test_layer_composition.py`.

Với mỗi file Python:
- Import cần thiết
- Class stub với type hint đầy đủ và docstring giải thích **tại sao** module tồn tại
- Method signature với `...` hoặc `raise NotImplementedError`
- **Không implement logic gì cả**

Tạo `requirements.txt` và `config/settings.yaml` đúng nội dung trong spec — bao gồm section `features` và `trend_gate`.

Tạo `.env.example`, `config/credentials.yaml.example`, `.gitignore` (phải có `.env`, `credentials.yaml`, `data/cache/`, `*.pkl`, `trading_halted.lock`).

Set up pytest, ruff, mypy trong `pyproject.toml`.

## Nghiệm thu

- [ ] `python -c "import core, broker, data, backtest, monitoring"` chạy không lỗi
- [ ] `pytest tests/ --collect-only` thu được tất cả test file, không lỗi import
- [ ] `ruff check .` sạch
- [ ] `settings.yaml` load được bằng `yaml.safe_load()` và chứa đủ các section: exchange, costs, hmm, features, trend_gate, strategy, risk, backtest
- [ ] `git status` không thấy `.env` hay bất kỳ file credential nào
- [ ] Không có file nào chứa logic thật — đây chỉ là bộ xương

Dán output của cả 4 lệnh trên.
