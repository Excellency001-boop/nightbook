"""
Nightbook's core loop. Meant to be invoked every 5 minutes by a GitHub
Actions schedule, not run as a persistent process, each invocation is one
full cycle: fetch live book -> append to data/history.csv -> recompute the
fee-aware, robustness-checked edge for every symbol using ALL accumulated
history -> write a decision (QUOTE or STAND_DOWN) with a real, honest reason
for each -> write decisions/latest.md and append to decisions/log.jsonl.

This does not place real orders. There is no capital, no exchange
credentials, no card anywhere in this repo. A QUOTE decision means "the
validated methodology says this symbol currently clears the bar," a paper
decision, not a live trade. See README.md.

Methodology (identical to what was validated in bitget-s2/research before
this was deployed, not a new untested approach):
- Fee floor: 20bps round trip (10bps maker/taker, confirmed live against
  Bitget's real API, no maker rebate). Hard constraint, not a tuning knob.
- A resting quote is only counted as "filled" when the next snapshot's NBBO
  fully crossed through our price, a conservative proxy since snapshots are
  5 minutes apart here (coarser than the 60s research collector), so real
  fills are undercounted, not flattered.
- A symbol only gets QUOTE if net edge is positive at every one of 4 tested
  quote depths (50/70/90/100% into the spread) with at least MIN_FILLS at
  each depth. One good number at one arbitrary depth is not enough, this is
  exactly the check that caught RAMD and RMETA reversing sign with more
  data during validation.
- Known scheduled events (FOMC/CPI/jobs/earnings) force STAND_DOWN
  regardless of edge. Unscheduled news is not covered, out of scope.
- Only operates outside regular market hours, during regular hours rToken
  orders route straight through to NYSE/NASDAQ, this strategy's whole
  premise doesn't apply there.
"""

import csv
import json
import os
import time
from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo

from bitget_client import get
from fill_sim import simulate_fill
from event_calendar import near_known_event

ET = ZoneInfo("America/New_York")

ORDERBOOK_PATH = "/api/v2/spot/market/orderbook"
NOTIONAL_TIERS_USD = [1_000, 5_000, 10_000, 25_000, 50_000, 100_000]
ROUND_TRIP_FEE_BPS = 20.0
QUOTE_DEPTHS = [0.5, 0.7, 0.9, 1.0]
MIN_FILLS_FOR_TRUST = 20
ADVERSE_SELECTION_LOOKAHEAD = 3

HISTORY_CSV = "data/history.csv"
DECISIONS_MD = "decisions/latest.md"
DECISIONS_LOG = "decisions/log.jsonl"

FIELDNAMES = (
    ["ts_utc", "symbol", "market_state", "mid", "spread_bps"]
    + [f"buy_slip_bps_{n}" for n in NOTIONAL_TIERS_USD]
    + [f"sell_slip_bps_{n}" for n in NOTIONAL_TIERS_USD]
    + [f"buy_fillable_{n}" for n in NOTIONAL_TIERS_USD]
    + [f"sell_fillable_{n}" for n in NOTIONAL_TIERS_USD]
)


def market_state(ts_utc: datetime) -> str:
    et = ts_utc.astimezone(ET)
    weekday = et.weekday()
    if weekday >= 5:
        return "weekend"
    t = et.time()
    if dtime(9, 30) <= t < dtime(16, 0):
        return "regular_hours"
    return "after_hours_weekday"


def fetch_orderbook(symbol: str, retries: int = 2):
    params = {"symbol": symbol, "type": "step0", "limit": "50"}
    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = get(ORDERBOOK_PATH, params)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            bids = [(float(p), float(s)) for p, s in data.get("bids", [])]
            asks = [(float(p), float(s)) for p, s in data.get("asks", [])]
            return bids, asks
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(1.5)
    raise last_error


def collect_snapshot(symbols: list[str]) -> list[dict]:
    now = datetime.now(timezone.utc)
    state = market_state(now)
    rows = []
    for symbol in symbols:
        try:
            bids, asks = fetch_orderbook(symbol)
            if not bids or not asks:
                continue
            row = {"ts_utc": now.isoformat(), "symbol": symbol, "market_state": state}
            for tier in NOTIONAL_TIERS_USD:
                r = simulate_fill(bids, asks, tier)
                row["mid"] = r["mid"]
                row["spread_bps"] = r["spread_bps"]
                row[f"buy_slip_bps_{tier}"] = r["buy_slippage_vs_mid_bps"]
                row[f"sell_slip_bps_{tier}"] = r["sell_slippage_vs_mid_bps"]
                row[f"buy_fillable_{tier}"] = r["buy_vwap"] is not None
                row[f"sell_fillable_{tier}"] = r["sell_vwap"] is not None
            rows.append(row)
        except Exception as e:
            print(f"[{now.isoformat()}] {symbol} fetch error: {e}")
    return rows


def append_history(rows: list[dict]):
    os.makedirs(os.path.dirname(HISTORY_CSV), exist_ok=True)
    write_header = not os.path.exists(HISTORY_CSV)
    with open(HISTORY_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def load_history() -> list[dict]:
    with open(HISTORY_CSV) as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["_ts"] = datetime.fromisoformat(r["ts_utc"])
        r["_mid"] = float(r["mid"]) if r.get("mid") not in ("", None) else None
        r["_spread_bps"] = float(r["spread_bps"]) if r.get("spread_bps") not in ("", None) else None
    return rows


def best_bid_ask(row):
    mid, spread_bps = row["_mid"], row["_spread_bps"]
    if mid is None or spread_bps is None:
        return None, None
    half_spread = mid * (spread_bps / 1e4) / 2
    return mid - half_spread, mid + half_spread


def backtest_symbol(rows, quote_inside_fraction):
    rows = sorted(
        (r for r in rows if r["market_state"] in ("after_hours_weekday", "weekend")),
        key=lambda r: r["_ts"],
    )
    fills = []
    for i in range(len(rows) - 1):
        now_r, nxt = rows[i], rows[i + 1]
        gap_s = (nxt["_ts"] - now_r["_ts"]).total_seconds()
        if gap_s > 600:
            continue
        best_bid, best_ask = best_bid_ask(now_r)
        if best_bid is None:
            continue
        half_spread = (best_ask - best_bid) / 2
        our_bid = best_bid + (1 - quote_inside_fraction) * half_spread
        our_ask = best_ask - (1 - quote_inside_fraction) * half_spread
        nxt_bid, nxt_ask = best_bid_ask(nxt)
        if nxt_bid is None:
            continue
        if nxt_bid >= our_ask:
            edge_bps = (our_ask - now_r["_mid"]) / now_r["_mid"] * 1e4
            fills.append({"side": "sell", "idx": i + 1, "edge_bps": edge_bps})
        if nxt_ask <= our_bid:
            edge_bps = (now_r["_mid"] - our_bid) / now_r["_mid"] * 1e4
            fills.append({"side": "buy", "idx": i + 1, "edge_bps": edge_bps})

    for f in fills:
        j = f["idx"]
        look = rows[j : j + ADVERSE_SELECTION_LOOKAHEAD + 1]
        if len(look) >= 2 and look[0]["_mid"]:
            drift_bps = (look[-1]["_mid"] - look[0]["_mid"]) / look[0]["_mid"] * 1e4
            f["adverse_bps"] = -drift_bps if f["side"] == "sell" else drift_bps
        else:
            f["adverse_bps"] = None

    if not fills:
        return None, 0
    avg_edge = sum(f["edge_bps"] for f in fills) / len(fills)
    adverse_vals = [f["adverse_bps"] for f in fills if f["adverse_bps"] is not None]
    avg_adverse = sum(adverse_vals) / len(adverse_vals) if adverse_vals else 0
    net_bps = avg_edge - ROUND_TRIP_FEE_BPS / 2 - avg_adverse
    return net_bps, len(fills)


def decide_symbol(symbol: str, sym_rows: list[dict], now: datetime):
    event = near_known_event(now, symbol)
    if event is not None:
        return "STAND_DOWN", f"known scheduled event nearby: {event}", {}

    state = market_state(now)
    if state == "regular_hours":
        return "STAND_DOWN", "regular market hours, strategy only operates after-hours/weekend", {}

    results = {}
    for depth in QUOTE_DEPTHS:
        net_bps, n_fills = backtest_symbol(sym_rows, depth)
        results[depth] = {"net_bps": net_bps, "n_fills": n_fills}

    min_fills = min(r["n_fills"] for r in results.values())
    if min_fills < MIN_FILLS_FOR_TRUST:
        return (
            "STAND_DOWN",
            f"insufficient sample, only {min_fills} fills at the weakest quote depth, need {MIN_FILLS_FOR_TRUST}+",
            results,
        )

    all_positive = all((r["net_bps"] or 0) > 0 for r in results.values())
    if all_positive:
        worst = min(r["net_bps"] for r in results.values())
        return "QUOTE", f"validated: positive net edge at every quote depth tested, worst case {worst:.1f}bps", results

    return "STAND_DOWN", "edge not robust across quote depths, negative or inconsistent", results


def write_outputs(now: datetime, decisions: list[dict]):
    os.makedirs("decisions", exist_ok=True)

    lines = [
        "# Nightbook, live decision log",
        "",
        f"Last run: {now.isoformat()}",
        "",
        "Paper decisions only, no real orders, no capital, no exchange credentials in this repo.",
        "",
        "| symbol | decision | reason |",
        "|---|---|---|",
    ]
    for d in decisions:
        lines.append(f"| {d['symbol']} | {d['decision']} | {d['reason']} |")
    with open(DECISIONS_MD, "w") as f:
        f.write("\n".join(lines) + "\n")

    with open(DECISIONS_LOG, "a") as f:
        for d in decisions:
            f.write(json.dumps({"ts_utc": now.isoformat(), **d}) + "\n")


def main():
    with open("symbols.json") as f:
        symbols = json.load(f)

    fresh_rows = collect_snapshot(symbols)
    append_history(fresh_rows)
    print(f"Appended {len(fresh_rows)} fresh snapshots.")

    history = load_history()
    now = datetime.now(timezone.utc)

    decisions = []
    for symbol in symbols:
        sym_rows = [r for r in history if r["symbol"] == symbol]
        decision, reason, results = decide_symbol(symbol, sym_rows, now)
        decisions.append({"symbol": symbol, "decision": decision, "reason": reason})
        print(f"{symbol}: {decision}, {reason}")

    write_outputs(now, decisions)


if __name__ == "__main__":
    main()
