import os
import time
import ccxt
import pandas as pd

API_KEY = os.getenv("MEXC_API_KEY", "mx0vglYklOGQGI9761")
API_SECRET = os.getenv("MEXC_API_SECRET", "529c8bbba99e4b3785d30a8bc5da4594")
SYMBOL = os.getenv("SYMBOL", "SOL/USDT")

TP = 0.007   # 0.7%
SL = 0.004   # 0.4%

TICKER_POLL_SEC = 2          # fast updates
CANDLE_REFRESH_SEC = 20      # safe refresh for OHLCV
TREND_REFRESH_SEC = 300      # 1H trend refresh every 5 minutes

exchange = ccxt.mexc({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "enableRateLimit": True,
})

in_position = False
entry_price = 0.0
trades_today = 0
losses_today = 0

last_candle_check = 0
last_trend_check = 0
trend_ok = False

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

print("FAST DRY RUN BOT STARTED", flush=True)

while True:
    try:
        # 1) Fast ticker update (every 2 seconds)
        t = exchange.fetch_ticker(SYMBOL)
        live_price = float(t["last"])
        print(f"TICK {live_price:.4f}", flush=True)

        # 2) Manage open position using live ticker (no need to pull candles)
        if in_position:
            tp_price = entry_price * (1 + TP)
            sl_price = entry_price * (1 - SL)

            if live_price >= tp_price:
                print(f"TAKE PROFIT hit at {live_price:.4f}", flush=True)
                in_position = False

            elif live_price <= sl_price:
                print(f"STOP LOSS hit at {live_price:.4f}", flush=True)
                losses_today += 1
                in_position = False

            time.sleep(TICKER_POLL_SEC)
            continue

        # 3) Trend check only every 5 minutes
        now = time.time()
        if now - last_trend_check >= TREND_REFRESH_SEC:
            ohlcv_1h = exchange.fetch_ohlcv(SYMBOL, timeframe="1h", limit=120)
            df_1h = pd.DataFrame(ohlcv_1h, columns=["ts","o","h","l","c","v"])
            ema100 = df_1h["c"].ewm(span=100).mean().iloc[-1]
            last_1h = float(df_1h["c"].iloc[-1])
            trend_ok = last_1h >= float(ema100)
            print(f"TREND 1H: close={last_1h:.4f} ema100={float(ema100):.4f} ok={trend_ok}", flush=True)
            last_trend_check = now

        if not trend_ok:
            time.sleep(TICKER_POLL_SEC)
            continue

        # 4) Entry setup check only every 20 seconds
        if now - last_candle_check >= CANDLE_REFRESH_SEC:
            ohlcv_5m = exchange.fetch_ohlcv(SYMBOL, timeframe="5m", limit=120)
            df_5m = pd.DataFrame(ohlcv_5m, columns=["ts","o","h","l","c","v"])
            df_5m["rsi"] = rsi(df_5m["c"])
            current_rsi = float(df_5m["rsi"].iloc[-1])
            current_close = float(df_5m["c"].iloc[-1])

            print(f"SETUP 5M: close={current_close:.4f} rsi={current_rsi:.1f}", flush=True)

            if current_rsi < 35:
                entry_price = live_price
                in_position = True
                trades_today += 1
                print(f"ENTER (DRY RUN) at {entry_price:.4f}", flush=True)

            last_candle_check = now

        time.sleep(TICKER_POLL_SEC)

    except Exception as e:
        print("Error:", e, flush=True)
        time.sleep(10)
