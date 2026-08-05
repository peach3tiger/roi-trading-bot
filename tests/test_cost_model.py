"""tests.test_cost_model — phí tính đúng.

File này KHÔNG được skip, không được xfail, không được comment out
(CLAUDE.md bất biến #15).
"""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

from backtest.cost_model import CostModel

# Đúng giá trị trong config/settings.yaml. Nếu config đổi mà test vẫn xanh thì
# test đang đo một mô hình phí không còn tồn tại.
TAKER_FEE_PCT = Decimal("0.10")
MAKER_FEE_PCT = Decimal("0.10")
SLIPPAGE_PCT = Decimal("0.03")


def _model(assume_taker: bool = True) -> CostModel:
    return CostModel(
        taker_fee_pct=TAKER_FEE_PCT,
        maker_fee_pct=MAKER_FEE_PCT,
        slippage_pct=SLIPPAGE_PCT,
        assume_taker=assume_taker,
    )


def _trade_log(rows: list[tuple[Decimal, Decimal]]) -> pd.DataFrame:
    return pd.DataFrame({"fee": [r[0] for r in rows], "slippage_cost": [r[1] for r in rows]})


def test_rebalance_cost_includes_fee_and_slippage() -> None:
    model = _model()
    # notional = 0.5 BTC * 100_000 = 50_000 USDT
    # fee  = 50_000 * 0.10% = 50
    # slip = 50_000 * 0.03% = 15
    assert model.fee_cost(Decimal("0.5"), Decimal("100000")) == Decimal("50")
    assert model.slippage_cost(Decimal("0.5"), Decimal("100000")) == Decimal("15")
    assert model.rebalance_cost(Decimal("0.5"), Decimal("100000")) == Decimal("65")


def test_fee_pct_is_percent_not_fraction() -> None:
    """Bẫy đơn vị: 0.10 trong settings.yaml là 0.10%, không phải 10%.

    Bỏ phép chia 100 thì phí trên notional 1000 USDT thành 100 USDT thay vì 1
    USDT — backtest vẫn chạy, chỉ là mọi kết quả đều sai.
    """
    model = _model()
    assert model.fee_cost(Decimal("1"), Decimal("1000")) == Decimal("1.0")
    assert model.slippage_cost(Decimal("1"), Decimal("1000")) == Decimal("0.30")


def test_rebalance_cost_is_positive_for_sells() -> None:
    """delta_qty âm (bán bớt) vẫn tốn phí, không được hoàn phí."""
    model = _model()
    buy = model.rebalance_cost(Decimal("0.5"), Decimal("100000"))
    sell = model.rebalance_cost(Decimal("-0.5"), Decimal("100000"))
    assert sell == buy
    assert sell > 0


def test_total_cost_report_pct_of_gross_profit() -> None:
    model = _model()
    log = _trade_log([(Decimal("50"), Decimal("15")), (Decimal("30"), Decimal("5"))])

    report = model.total_cost_report(log, gross_profit=Decimal("1000"))

    assert report.total_fee_usdt == Decimal("80")
    assert report.total_slippage_usdt == Decimal("20")
    assert report.total_cost_usdt == Decimal("100")
    assert report.n_rebalances == 2
    assert report.cost_pct_of_gross_profit == Decimal("10")
    assert report.exceeds_fee_drag_threshold is False


def test_total_cost_report_flags_fee_drag_above_30pct() -> None:
    model = _model()
    log = _trade_log([(Decimal("300"), Decimal("50"))])
    report = model.total_cost_report(log, gross_profit=Decimal("1000"))
    assert report.cost_pct_of_gross_profit == Decimal("35")
    assert report.exceeds_fee_drag_threshold is True


def test_total_cost_report_without_gross_profit_returns_none_not_zero() -> None:
    """Thiếu lợi nhuận gộp thì báo là thiếu, không trả 0%."""
    model = _model()
    log = _trade_log([(Decimal("50"), Decimal("15"))])
    report = model.total_cost_report(log)
    assert report.total_cost_usdt == Decimal("65")
    assert report.cost_pct_of_gross_profit is None
    assert report.gross_profit_usdt is None


def test_total_cost_report_handles_non_positive_gross_profit() -> None:
    """Lỗ gộp mà vẫn trả phí: % không xác định nhưng vẫn phải bật cờ."""
    model = _model()
    log = _trade_log([(Decimal("50"), Decimal("15"))])
    report = model.total_cost_report(log, gross_profit=Decimal("-200"))
    assert report.cost_pct_of_gross_profit is None
    assert report.exceeds_fee_drag_threshold is True


def test_total_cost_report_sum_matches_trade_log_exactly() -> None:
    """Nghiệm thu Phase 6: tổng phí trong cost_report khớp tổng phí trong trade_log.

    1000 dòng 0.01 cộng qua float64 cho 9.999999999999831, không phải 10.
    Phép so sánh `==` dưới đây chỉ đúng nếu tổng được cộng trong không gian
    Decimal.
    """
    model = _model()
    log = _trade_log([(Decimal("0.01"), Decimal("0.02"))] * 1000)
    report = model.total_cost_report(log)
    assert report.total_fee_usdt == Decimal("10.00")
    assert report.total_slippage_usdt == Decimal("20.00")
    assert report.total_cost_usdt == Decimal("30.00")


def test_total_cost_report_rejects_trade_log_missing_cost_columns() -> None:
    model = _model()
    bad_log = pd.DataFrame({"fee": [Decimal("1")]})
    with pytest.raises(ValueError, match="slippage_cost"):
        model.total_cost_report(bad_log)


def test_assume_taker_fee_when_configured() -> None:
    """assume_taker: true dùng taker_fee_pct luôn, kể cả khi maker rẻ hơn.

    settings.yaml hiện đặt taker == maker == 0.10 nên hai nhánh không phân biệt
    được bằng giá trị config. Dùng maker rẻ hơn ở đây để chứng minh nhánh chọn
    đúng, chứ không phải trùng nhau do tình cờ.
    """
    cheap_maker = Decimal("0.02")
    taker_model = CostModel(TAKER_FEE_PCT, cheap_maker, SLIPPAGE_PCT, assume_taker=True)
    maker_model = CostModel(TAKER_FEE_PCT, cheap_maker, SLIPPAGE_PCT, assume_taker=False)

    assert taker_model.effective_fee_pct == TAKER_FEE_PCT
    assert maker_model.effective_fee_pct == cheap_maker

    # Bảo thủ: giả định taker không bao giờ rẻ hơn giả định maker.
    notional_args = (Decimal("1"), Decimal("100000"))
    assert taker_model.fee_cost(*notional_args) > maker_model.fee_cost(*notional_args)


def test_default_is_assume_taker() -> None:
    """Mặc định phải là nhánh bảo thủ, không phải nhánh rẻ."""
    model = CostModel(TAKER_FEE_PCT, Decimal("0.02"), SLIPPAGE_PCT)
    assert model.assume_taker is True
    assert model.effective_fee_pct == TAKER_FEE_PCT
