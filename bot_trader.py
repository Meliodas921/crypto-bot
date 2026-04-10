#!/usr/bin/env python3
"""
Crypto AutoTrader Bot — Lab Work Solutions
Coins: BTC, SUI, SOL, ZEC
Simulates live Kraken trading with 0.26% taker fee on every trade.
Minimum sell = $10 to prevent selling for cents.
Paper trading only — no real money.
Runs every 15 minutes via GitHub Actions (free, 24/7).
"""

import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent
STATE_FILES = {
    "BTC": BASE / "BTC_Bot_State.json",
    "SUI": BASE / "SUI_Bot_State.json",
    "SOL": BASE / "SOL_Bot_State.json",
    "ZEC": BASE / "ZEC_Bot_State.json",
}
CONFIG_PATH = BASE / "Bot_Strategy_Config.json"
LOG_PATH    = BASE / "Trading_Log.md"

COINGECKO_IDS = {
    "BTC": "bitcoin",
    "SUI": "sui",
    "SOL": "solana",
    "ZEC": "zcash",
}

DEFAULT_EXCHANGE = {"useFeePct": 0.0026}
DEFAULT_COIN = {
    "initialEntryUSD": 20, "stopLossPct": -20,
    "takeProfitPct": 10, "takeProfitSellFraction": 0.6,
    "dipBuyPctChange": -0.7, "dipBuyUSD": 20, "dipBuyMinCash": 20,
    "momentumPctChange": 1.2, "momentumUSD": 20, "momentumMinCash": 20,
    "reloadMinCash": 20, "reloadMaxAbsPctChange": 0.2, "reloadUSD": 20,
    "minSellValueUSD": 10,
}
DEFAULT_HEALTH = {
    "stopLossAlertAfterNTriggers": 3,
    "takeProfitNeverFiredAfterNRuns": 20,
    "momentumWinRateAlertBelowPct": 40, "momentumMinTriggersBeforeAlert": 5,
    "reloadDailyAlertAboveNTriggers": 6, "consecutiveLossesAlertAfter": 3,
}

def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def fetch_prices():
    ids = ",".join(COINGECKO_IDS.values())
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
            prices = {}
            for symbol, gecko_id in COINGECKO_IDS.items():
                p = data.get(gecko_id, {}).get("usd")
                if p and float(p) > 0:
                    prices[symbol] = float(p)
            if len(prices) == len(COINGECKO_IDS):
                return prices
            log(f"Price fetch incomplete — retry {attempt+1}")
        except Exception as e:
            log(f"Price fetch error (attempt {attempt+1}): {e}")
        import time; time.sleep(5)
    return None

def blank_state(symbol, price):
    return {
        "coin": symbol,
        "cash": 100.0, "holdings": 0.0, "avgBuyPrice": 0.0,
        "realizedPnl": 0.0, "totalFeesPaid": 0.0,
        "wins": 0, "totalSellTrades": 0,
        "lastPrice": 0.0, "lastUpdated": "",
        "priceHistory": [], "priceTimestamps": [], "trades": [],
        "analytics": {
            "dayHigh": price, "dayLow": price, "sessionStart": price,
            "totalBuyVolume": 0.0, "totalSellVolume": 0.0,
            "largestWin": 0.0, "largestLoss": 0.0,
            "consecutiveWins": 0, "consecutiveLosses": 0,
            "dailySnapshots": [], "strategyAlerts": [],
        },
        "ruleStats": {
            "INITIAL_ENTRY": {"triggers": 0, "totalUSD": 0.0},
            "STOP_LOSS":   {"triggers": 0, "wins": 0, "losses": 0, "totalPnl": 0.0},
            "TAKE_PROFIT": {"triggers": 0, "wins": 0, "losses": 0, "totalPnl": 0.0},
            "DIP_BUY":     {"triggers": 0, "totalUSD": 0.0},
            "MOMENTUM":    {"triggers": 0, "wins": 0, "losses": 0, "totalPnl": 0.0},
            "RELOAD":      {"triggers": 0, "totalUSD": 0.0},
            "HOLD":        {"triggers": 0},
            "runsWithNoTrade": 0, "totalRuns": 0,
        },
    }

def apply_strategy(state, price, cfg, fee):
    cash     = state.get("cash", 100.0)
    holdings = state.get("holdings", 0.0)
    avg_buy  = state.get("avgBuyPrice", 0.0)
    last     = state.get("lastPrice", 0.0)
    min_sell = cfg.get("minSellValueUSD", 10)

    pct_change = ((price - last) / last * 100) if last > 0 else 0.0
    from_avg   = ((price - avg_buy) / avg_buy * 100) if (holdings > 0 and avg_buy > 0) else None

    def make_buy(usd, reason):
        coins_gross = usd / price
        coins_net   = coins_gross * (1 - fee)
        fee_usd     = coins_gross * fee * price
        old_val     = holdings * avg_buy if avg_buy > 0 else 0
        new_hold    = holdings + coins_net
        new_avg     = (old_val + usd) / new_hold if new_hold > 0 else price / (1 - fee)
        return {
            "action": reason, "type": "BUY", "reason": reason,
            "usd": usd, "coins": coins_net, "fee_usd": fee_usd,
            "cash_after": cash - usd, "pnl": None,
            "hold_after": new_hold, "avg_after": new_avg,
        }

    def make_sell(coins, reason):
        gross = coins * price
        net   = gross * (1 - fee)
        if net < min_sell:
            return None
        fee_usd = gross * fee
        pnl     = net - (avg_buy * coins)
        new_hold = holdings - coins
        return {
            "action": reason, "type": "SELL", "reason": reason,
            "usd": net, "coins": coins, "fee_usd": fee_usd,
            "cash_after": cash + net, "pnl": pnl,
            "hold_after": new_hold if new_hold > 0.0001 else 0.0,
            "avg_after":  avg_buy  if new_hold > 0.0001 else 0.0,
        }

    if holdings == 0 and cash >= cfg["initialEntryUSD"]:
        return "INITIAL ENTRY", make_buy(cfg["initialEntryUSD"], "INITIAL ENTRY"), pct_change, from_avg

    if holdings > 0 and from_avg is not None and from_avg <= cfg["stopLossPct"]:
        gross = holdings * price
        net   = gross * (1 - fee)
        pnl   = net - (avg_buy * holdings)
        if net < 1.0:
            return "STOP LOSS", {"action": "STOP LOSS", "type": "WIPE", "reason": "STOP LOSS (wiped)",
                "usd": 0.0, "coins": holdings, "fee_usd": 0.0,
                "cash_after": cash, "pnl": pnl, "hold_after": 0.0, "avg_after": 0.0}, pct_change, from_avg
        return "STOP LOSS", {"action": "STOP LOSS", "type": "SELL", "reason": "STOP LOSS",
            "usd": net, "coins": holdings, "fee_usd": gross * fee,
            "cash_after": cash + net, "pnl": pnl, "hold_after": 0.0, "avg_after": 0.0}, pct_change, from_avg

    if holdings > 0 and from_avg is not None and from_avg >= cfg["takeProfitPct"]:
        t = make_sell(holdings * cfg["takeProfitSellFraction"], "TAKE PROFIT")
        if t:
            return "TAKE PROFIT", t, pct_change, from_avg

    if pct_change <= cfg["dipBuyPctChange"] and cash >= cfg["dipBuyMinCash"]:
        return "DIP BUY", make_buy(min(cfg["dipBuyUSD"], cash), "DIP BUY"), pct_change, from_avg

    if (pct_change >= cfg["momentumPctChange"] and cash >= cfg["momentumMinCash"]
            and (holdings == 0 or (from_avg is not None and from_avg < 0))):
        return "MOMENTUM", make_buy(cfg["momentumUSD"], "MOMENTUM"), pct_change, from_avg

    if (holdings > 0 and cash >= cfg["reloadMinCash"]
            and abs(pct_change) <= cfg["reloadMaxAbsPctChange"]):
        return "RELOAD", make_buy(cfg["reloadUSD"], "RELOAD"), pct_change, from_avg

    return "HOLD", None, pct_change, from_avg

def update_state(state, action, trade, price, symbol):
    now   = datetime.now(timezone.utc)
    ts    = now.isoformat()
    label = now.strftime("%b %d %H:%M")

    if trade:
        state["cash"]        = round(trade["cash_after"], 6)
        state["holdings"]    = round(trade["hold_after"], 8)
        state["avgBuyPrice"] = round(trade["avg_after"], 8)
        state["totalFeesPaid"] = state.get("totalFeesPaid", 0) + trade.get("fee_usd", 0)

        if trade["type"] == "SELL" and trade["pnl"] is not None:
            state["realizedPnl"]     = state.get("realizedPnl", 0) + trade["pnl"]
            state["totalSellTrades"] = state.get("totalSellTrades", 0) + 1
            if trade["pnl"] >= 0:
                state["wins"] = state.get("wins", 0) + 1

        state.setdefault("trades", []).insert(0, {
            "ts": ts, "coin": symbol, "type": trade["type"],
            "coins": round(trade["coins"], 8), "price": round(price, 6),
            "total": round(trade["usd"], 4), "fee": round(trade.get("fee_usd", 0), 6),
            "pnl": round(trade["pnl"], 6) if trade["pnl"] is not None else None,
            "cash": round(trade["cash_after"], 4), "source": "BOT", "reason": trade["reason"],
        })
        state["trades"] = state["trades"][:100]

    state["lastPrice"]   = price
    state["lastUpdated"] = ts

    ph = state.setdefault("priceHistory", [])
    pt = state.setdefault("priceTimestamps", [])
    ph.append(price); state["priceHistory"]    = ph[-80:]
    pt.append(label); state["priceTimestamps"] = pt[-80:]

    a = state.setdefault("analytics", {
        "dayHigh": price, "dayLow": price, "sessionStart": price,
        "totalBuyVolume": 0.0, "totalSellVolume": 0.0,
        "largestWin": 0.0, "largestLoss": 0.0,
        "consecutiveWins": 0, "consecutiveLosses": 0,
        "dailySnapshots": [], "strategyAlerts": [],
    })
    a["dayHigh"] = max(a.get("dayHigh", price), price)
    a["dayLow"]  = min(a.get("dayLow",  price), price)

    if trade and trade["type"] == "BUY":
        a["totalBuyVolume"] = a.get("totalBuyVolume", 0) + trade["usd"]
    if trade and trade["type"] == "SELL":
        a["totalSellVolume"] = a.get("totalSellVolume", 0) + trade["usd"]
        pnl = trade.get("pnl") or 0
        if pnl >= 0:
            a["consecutiveWins"]   = a.get("consecutiveWins", 0) + 1
            a["consecutiveLosses"] = 0
            a["largestWin"] = max(a.get("largestWin", 0), pnl)
        else:
            a["consecutiveLosses"] = a.get("consecutiveLosses", 0) + 1
            a["consecutiveWins"]   = 0
            a["largestLoss"] = min(a.get("largestLoss", 0), pnl)

    rs = state.setdefault("ruleStats", {
        "INITIAL_ENTRY": {"triggers": 0, "totalUSD": 0.0},
        "STOP_LOSS":   {"triggers": 0, "wins": 0, "losses": 0, "totalPnl": 0.0},
        "TAKE_PROFIT": {"triggers": 0, "wins": 0, "losses": 0, "totalPnl": 0.0},
        "DIP_BUY":     {"triggers": 0, "totalUSD": 0.0},
        "MOMENTUM":    {"triggers": 0, "wins": 0, "losses": 0, "totalPnl": 0.0},
        "RELOAD":      {"triggers": 0, "totalUSD": 0.0},
        "HOLD":        {"triggers": 0},
        "runsWithNoTrade": 0, "totalRuns": 0,
    })
    rs["totalRuns"] = rs.get("totalRuns", 0) + 1
    key = action.replace(" ", "_").upper()

    if action == "HOLD":
        rs["HOLD"]["triggers"] = rs["HOLD"].get("triggers", 0) + 1
        rs["runsWithNoTrade"]  = rs.get("runsWithNoTrade", 0) + 1
    elif key in ("INITIAL_ENTRY", "DIP_BUY", "MOMENTUM", "RELOAD") and trade:
        r = rs.setdefault(key, {"triggers": 0, "totalUSD": 0.0})
        r["triggers"] = r.get("triggers", 0) + 1
        r["totalUSD"] = r.get("totalUSD", 0.0) + trade["usd"]
    elif key in ("STOP_LOSS", "TAKE_PROFIT") and trade:
        r = rs.setdefault(key, {"triggers": 0, "wins": 0, "losses": 0, "totalPnl": 0.0})
        r["triggers"] = r.get("triggers", 0) + 1
        pnl = trade.get("pnl") or 0
        if pnl >= 0: r["wins"]   = r.get("wins", 0) + 1
        else:        r["losses"] = r.get("losses", 0) + 1
        r["totalPnl"] = r.get("totalPnl", 0.0) + pnl

    return state

def append_log(results):
    if not LOG_PATH.exists():
        LOG_PATH.write_text("# Trading Log — Lab Work Solutions\n\n")
    now = datetime.now()
    dt  = now.strftime("%b %d %Y %H:%M")
    lines = []
    for symbol, (state, action, price, alerts) in results.items():
        port = state.get("cash", 0) + state.get("holdings", 0) * price
        lines.append(
            f"**[{dt}]** | {symbol} ${price:.4f} → {action} | "
            f"Cash: ${state['cash']:.2f} | Holdings: {state['holdings']:.6f} | "
            f"Portfolio: ${port:.2f} | P&L: ${state.get('realizedPnl',0):.4f} | "
            f"Fees: ${state.get('totalFeesPaid',0):.4f}"
        )
        for alert in alerts:
            lines.append(f"  → {alert}")
    with open(LOG_PATH, "a") as f:
        f.write("\n".join(lines) + "\n")

def main():
    log("=== Crypto AutoTrader (BTC/SUI/SOL/ZEC) starting ===")
    cfg_raw = load_json(CONFIG_PATH, {})
    ex_cfg  = {**DEFAULT_EXCHANGE, **cfg_raw.get("exchange", {})}
    h_cfg   = {**DEFAULT_HEALTH,   **cfg_raw.get("healthMonitor", {})}
    fee     = ex_cfg["useFeePct"]

    prices = fetch_prices()
    if not prices:
        log("Could not fetch prices — aborting.")
        return
    log("Prices: " + " | ".join(f"{s}=${p:.4f}" for s, p in prices.items()))

    results = {}
    for symbol in ["BTC", "SUI", "SOL", "ZEC"]:
        price    = prices[symbol]
        coin_cfg = {**DEFAULT_COIN, **cfg_raw.get(symbol, {})}
        state    = load_json(STATE_FILES[symbol]) or blank_state(symbol, price)
        action, trade, pct_chg, from_avg = apply_strategy(state, price, coin_cfg, fee)
        if trade:
            pnl_str = f"${trade['pnl']:.4f}" if trade['pnl'] is not None else "N/A"
            log(f"{symbol}: {action} | ${trade['usd']:.2f} | fee ${trade['fee_usd']:.4f} | pnl {pnl_str}")
        else:
            log(f"{symbol}: HOLD | price={price:.4f} pct={pct_chg:+.2f}%")
        state = update_state(state, action, trade, price, symbol)
        alerts = []
        save_json(STATE_FILES[symbol], state)
        results[symbol] = (state, action, price, alerts)

    append_log(results)
    total_port = sum(r[0].get("cash", 0) + r[0].get("holdings", 0) * prices[s]
                     for s, r in results.items())
    total_fees = sum(r[0].get("totalFeesPaid", 0) for r in results.values())
    log(f"Combined portfolio: ${total_port:.2f} | Total fees: ${total_fees:.4f}")
    log("=== Run complete ===")

if __name__ == "__main__":
    main()
