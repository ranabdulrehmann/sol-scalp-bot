import os
import time
import math
import ccxt
import pandas as pd
from datetime import datetime, timezone
import logging
from pathlib import Path

LOG_DIR = os.getenv("LOG_DIR", "/app/logs")     # <-- matches your Coolify volume mount
LOG_PREFIX = os.getenv("LOG_PREFIX", "solbot")  # optional

Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
log_file = os.path.join(
    LOG_DIR,
    f"{LOG_PREFIX}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"
)

logger = logging.getLogger("bot")
logger.setLevel(logging.INFO)

fmt = logging.Formatter("%(asctime)sZ %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")

# 1) File handler (persistent)
fh = logging.FileHandler(log_file)
fh.setFormatter(fmt)
logger.addHandler(fh)

# 2) Console handler (Coolify logs)
ch = logging.StreamHandler()
ch.setFormatter(fmt)
logger.addHandler(ch)
#==============

# ================== CONFIG ==================
SYMBOL = os.getenv("SYMBOL", "SOL/USDT")

DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
LIVE_CONFIRM = os.getenv("LIVE_CONFIRM", "NO").upper()  # must be YES to trade live

TP = float(os.getenv("TP_PCT", "0.007"))   # 0.7%
SL = float(os.getenv("SL_PCT", "0.004"))   # 0.4%

RISK_FRACTION = float(os.getenv("RISK_FRACTION", "0.20"))  # 20% of USDT per trade
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "2"))
MAX_LOSSES_PER_DAY = int(os.getenv("MAX_LOSSES_PER_DAY", "2"))
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", "120"))

TICKER_POLL_SEC = int(os.getenv("TICKER_POLL_SEC", "2"))
CANDLE_REFRESH_SEC = int(os.getenv("CANDLE_REFRESH_SEC", "20"))
TREND_REFRESH_SEC = int(os.getenv("TREND_REFRESH_SEC", "300"))

API_KEY = os.getenv("MEXC_API_KEY", "mx0vglYklOGQGI9761")
API_SECRET = os.getenv("MEXC_API_SECRET", "529c8bbba99e4b3785d30a8bc5da4594")

# ================== EXCHANGE ==================
exchange = ccxt.mexc({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "enableRateLimit": True,
})

exchange.load_markets()
market = exchange.market(SYMBOL)

# ================== STATE ==================
in_position = False
entry_price = 0.0
pos_amount = 0.0

trades_today = 0
losses_today = 0
last_trade_ts = 0

last_candle_check = 0
last_trend_check = 0
trend_ok = False

def log(msg: str):
    logger.info(msg)

def day_key_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

current_day = day_key_utc()

def reset_daily_if_needed():
    global current_day, trades_today, losses_today
    dk = day_key_utc()
    if dk != current_day:
        current_day = dk
        trades_today = 0
        losses_today = 0
        log("Daily counters reset.")

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def safe_can_trade():
    if trades_today >= MAX_TRADES_PER_DAY:
        return False, "Max trades/day reached"
    if losses_today >= MAX_LOSSES_PER_DAY:
        return False, "Max losses/day reached"
    if last_trade_ts and (time.time() - last_trade_ts) < COOLDOWN_SEC:
        return False, "Cooldown active"
    return True, "OK"

def get_usdt_free():
    bal = exchange.fetch_balance()
    usdt = bal.get("USDT", {}).get("free", None)
    if usdt is None:
        usdt = bal.get("free", {}).get("USDT", 0)
    return float(usdt)

def get_sol_free():
    bal = exchange.fetch_balance()
    sol = bal.get("SOL", {}).get("free", None)
    if sol is None:
        sol = bal.get("free", {}).get("SOL", 0)
    return float(sol)

def to_amount_precision(amount: float) -> float:
    return float(exchange.amount_to_precision(SYMBOL, amount))

def to_price_precision(price: float) -> float:
    return float(exchange.price_to_precision(SYMBOL, price))

def place_limit_buy(amount: float, price: float):
    if DRY_RUN or LIVE_CONFIRM != "YES":
        log(f"[SIM] BUY {SYMBOL} amount={amount} price={price}")
        return {"id": "sim-buy"}
    return exchange.create_limit_buy_order(SYMBOL, amount, price)

def place_limit_sell(amount: float, price: float):
    if DRY_RUN or LIVE_CONFIRM != "YES":
        log(f"[SIM] SELL {SYMBOL} amount={amount} price={price}")
        return {"id": "sim-sell"}
    return exchange.create_limit_sell_order(SYMBOL, amount, price)

log("LIVE-READY bot started")
log(f"DRY_RUN={DRY_RUN} LIVE_CONFIRM={LIVE_CONFIRM} SYMBOL={SYMBOL}")

while True:
    try:
        reset_daily_if_needed()

        # --- live ticker ---
        t = exchange.fetch_ticker(SYMBOL)
        last = float(t["last"])
        bid = float(t.get("bid") or last)
        ask = float(t.get("ask") or last)
        log(f"TICK {last:.4f}")

        # --- manage open position ---
        if in_position:
            tp_price = entry_price * (1 + TP)
            sl_price = entry_price * (1 - SL)

            if last >= tp_price:
                sell_price = to_price_precision(bid)
                sell_amt = to_amount_precision(pos_amount)
                log(f"TP HIT -> selling amount={sell_amt} at {sell_price}")
                place_limit_sell(sell_amt, sell_price)
                in_position = False
                entry_price = 0.0
                pos_amount = 0.0
                last_trade_ts = time.time()

            elif last <= sl_price:
                sell_price = to_price_precision(bid)
                sell_amt = to_amount_precision(pos_amount)
                log(f"SL HIT -> selling amount={sell_amt} at {sell_price}")
                place_limit_sell(sell_amt, sell_price)
                losses_today += 1
                in_position = False
                entry_price = 0.0
                pos_amount = 0.0
                last_trade_ts = time.time()

            time.sleep(TICKER_POLL_SEC)
            continue

        # --- only one position at a time (also prevent if you already hold SOL) ---
        if get_sol_free() > 0.0001:
            log("SOL balance detected -> not opening new position (1 position rule).")
            time.sleep(10)
            continue

        ok, reason = safe_can_trade()
        if not ok:
            log(f"NO TRADE: {reason}")
            time.sleep(10)
            continue

        # --- trend check (1h EMA100) every 5 min ---
        now = time.time()
        if now - last_trend_check >= TREND_REFRESH_SEC:
            ohlcv_1h = exchange.fetch_ohlcv(SYMBOL, timeframe="1h", limit=120)
            df_1h = pd.DataFrame(ohlcv_1h, columns=["ts","o","h","l","c","v"])
            ema100 = df_1h["c"].ewm(span=100).mean().iloc[-1]
            last_1h_close = float(df_1h["c"].iloc[-1])
            trend_ok = last_1h_close >= float(ema100)
            log(f"TREND 1H: close={last_1h_close:.4f} ema100={float(ema100):.4f} ok={trend_ok}")
            last_trend_check = now

        if not trend_ok:
            time.sleep(TICKER_POLL_SEC)
            continue

        # --- setup check (5m RSI) every 20 sec ---
        if now - last_candle_check >= CANDLE_REFRESH_SEC:
            ohlcv_5m = exchange.fetch_ohlcv(SYMBOL, timeframe="5m", limit=120)
            df_5m = pd.DataFrame(ohlcv_5m, columns=["ts","o","h","l","c","v"])
            df_5m["rsi"] = rsi(df_5m["c"])
            current_rsi = float(df_5m["rsi"].iloc[-1])
            current_close = float(df_5m["c"].iloc[-1])

            log(f"SETUP 5M: close={current_close:.4f} rsi={current_rsi:.1f}")

            if current_rsi < 35:
                usdt_free = get_usdt_free()
                usdt_to_use = usdt_free * RISK_FRACTION
                if usdt_to_use < 10:
                    log(f"USDT too low for trade: free={usdt_free:.2f}, using={usdt_to_use:.2f}")
                else:
                    buy_price = to_price_precision(bid)  # buy at bid
                    amount = to_amount_precision((usdt_to_use / buy_price) * 0.999)  # fee buffer

                    log(f"ENTER -> buy amount={amount} at {buy_price} | DRY_RUN={DRY_RUN} LIVE_CONFIRM={LIVE_CONFIRM}")
                    place_limit_buy(amount, buy_price)

                    # mark position as open immediately (simple). Next step we can add fill-checking.
                    in_position = True
                    entry_price = buy_price
                    pos_amount = amount
                    trades_today += 1
                    last_trade_ts = time.time()

            last_candle_check = now

        time.sleep(TICKER_POLL_SEC)

    except Exception as e:
        log(f"Error: {e}")
        time.sleep(10)
