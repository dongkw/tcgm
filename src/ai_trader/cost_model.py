"""Trading cost model for A-share paper trading."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_tax_rate_sell: float = 0.0005
    slippage_rate: float = 0.001


def money(value: float) -> float:
    return round(float(value), 2)


def price(value: float) -> float:
    return round(float(value), 4)


def estimate_trade(
    side: str,
    quantity: int,
    reference_price: float,
    model: CostModel | None = None,
) -> dict[str, float]:
    """Return fill and cost fields.

    Slippage is reflected in fill_price. slippage_cost is only reported for
    attribution and must not be added to or subtracted from net_amount again.
    """

    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if reference_price <= 0:
        raise ValueError("reference_price must be positive")

    model = model or CostModel()
    side = side.upper()
    if side == "BUY":
        fill_price = price(reference_price * (1 + model.slippage_rate))
    elif side == "SELL":
        fill_price = price(reference_price * (1 - model.slippage_rate))
    else:
        raise ValueError(f"unsupported side: {side}")

    gross_amount = money(quantity * fill_price)
    commission = money(max(gross_amount * model.commission_rate, model.min_commission))
    stamp_tax = money(gross_amount * model.stamp_tax_rate_sell) if side == "SELL" else 0.0
    slippage_cost = money(abs(fill_price - reference_price) * quantity)

    if side == "BUY":
        net_amount = money(gross_amount + commission)
    else:
        net_amount = money(gross_amount - commission - stamp_tax)

    return {
        "reference_price": float(reference_price),
        "fill_price": fill_price,
        "gross_amount": gross_amount,
        "commission": commission,
        "stamp_tax": stamp_tax,
        "slippage_cost": slippage_cost,
        "net_amount": net_amount,
    }
