"""main.py — điểm vào CLI: live loop, backtest, train-only, stress-test.

Không có bước "chờ thị trường mở" — thị trường crypto 24/7, toàn bộ logic
giờ giao dịch bị loại bỏ khỏi hệ thống này (xem CLAUDE.md bất biến #10).
"""

from __future__ import annotations

import argparse


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="regime-trader-crypto")
    parser.add_argument("--dry-run", action="store_true", help="Chạy full pipeline, không đặt lệnh")
    parser.add_argument("--backtest", action="store_true", help="Walk-forward backtester")
    parser.add_argument("--train-only", action="store_true", help="Train HMM rồi thoát")
    parser.add_argument("--stress-test", action="store_true", help="Chạy stress test")
    parser.add_argument("--compare", action="store_true", help="So sánh benchmark")
    parser.add_argument("--dashboard", action="store_true", help="Xem dashboard của instance đang chạy")
    parser.add_argument("--testnet", action="store_true", default=True, help="Ép dùng testnet (mặc định)")
    parser.add_argument("--live", action="store_true", help="Ép dùng mainnet (yêu cầu xác nhận gõ tay)")
    parser.add_argument("--start", type=str, help="Ngày bắt đầu backtest, YYYY-MM-DD")
    parser.add_argument("--end", type=str, help="Ngày kết thúc backtest, YYYY-MM-DD")
    parser.add_argument("--period", type=str, help="Kiểm tra riêng một giai đoạn, vd. 2022")
    parser.add_argument("--symbol", type=str, help="Symbol kiểm định ngoài mẫu, vd. ETHUSDT")
    parser.add_argument("--sweep", type=str, help="Tên tham số cần quét")
    parser.add_argument("--range", type=str, help="min,max,step cho --sweep")
    parser.add_argument("--no-trend-gate", action="store_true", help="So sánh có/không Phase 3.5")
    parser.add_argument(
        "--bar-offset", type=str, help="Danh sách offset giờ UTC để kiểm tra độ nhạy mốc đóng bar"
    )
    parser.add_argument("--ablation", action="store_true", help="Quét feature từng cái một")
    return parser


def startup() -> None:
    """Load config, kết nối sàn, đồng bộ giờ, cache InstrumentRules,
    load/train HMM, khởi tạo risk manager + position tracker (đối soát),
    kiểm tra state_snapshot.json và trading_halted.lock, mở WebSocket feed.
    """
    raise NotImplementedError


def main_loop() -> None:
    """Vòng lặp chính mỗi khi bar đóng — chạy vĩnh viễn, thị trường 24/7."""
    raise NotImplementedError


def shutdown() -> None:
    """SIGINT/SIGTERM: đóng WebSocket, KHÔNG đóng vị thế, ghi state_snapshot.json."""
    raise NotImplementedError


def main() -> None:
    parser = build_arg_parser()
    parser.parse_args()
    raise NotImplementedError


if __name__ == "__main__":
    main()
