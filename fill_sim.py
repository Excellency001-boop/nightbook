"""
Walks a real order book to answer: if I market-order buy/sell $X notional
right now, what price do I actually get, and how many bps is that away from
the best quote and from mid?
"""


def _walk(levels: list[tuple[float, float]], notional_usd: float):
    """levels: list of (price, size) already sorted best-first."""
    remaining = notional_usd
    cost = 0.0
    qty = 0.0
    for price, size in levels:
        level_notional = price * size
        if level_notional <= remaining:
            cost += level_notional
            qty += size
            remaining -= level_notional
        else:
            take_qty = remaining / price
            cost += take_qty * price
            qty += take_qty
            remaining = 0.0
            break
    if remaining > 1e-9 or qty == 0:
        return None  # book too thin to fill this notional at all
    return cost / qty  # volume-weighted average fill price


def simulate_fill(bids: list[tuple[float, float]], asks: list[tuple[float, float]], notional_usd: float):
    """
    bids: [(price, size), ...] descending by price (best bid first)
    asks: [(price, size), ...] ascending by price (best ask first)
    Returns dict with buy/sell VWAP fill price and slippage in bps, or None
    per side if the book can't absorb that notional.
    """
    best_bid, best_ask = bids[0][0], asks[0][0]
    mid = (best_bid + best_ask) / 2
    spread_bps = (best_ask - best_bid) / mid * 1e4

    buy_vwap = _walk(asks, notional_usd)
    sell_vwap = _walk(bids, notional_usd)

    result = {"mid": mid, "spread_bps": spread_bps, "notional_usd": notional_usd}

    result["buy_vwap"] = buy_vwap
    result["buy_slippage_vs_ask_bps"] = (
        (buy_vwap - best_ask) / best_ask * 1e4 if buy_vwap is not None else None
    )
    result["buy_slippage_vs_mid_bps"] = (
        (buy_vwap - mid) / mid * 1e4 if buy_vwap is not None else None
    )

    result["sell_vwap"] = sell_vwap
    result["sell_slippage_vs_bid_bps"] = (
        (best_bid - sell_vwap) / best_bid * 1e4 if sell_vwap is not None else None
    )
    result["sell_slippage_vs_mid_bps"] = (
        (mid - sell_vwap) / mid * 1e4 if sell_vwap is not None else None
    )

    return result
