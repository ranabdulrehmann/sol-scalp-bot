import os
import time
import ccxt
import pandas as pd

API_KEY = os.getenv("MEXC_API_KEY", "mx0vglYklOGQGI9761")
API_SECRET = os.getenv("MEXC_API_SECRET", "529c8bbba99e4b3785d30a8bc5da4594")
SYMBOL = os.getenv("SYMBOL", "SOL/USDT")

TP = 0.007   # 0.7%
SL = 0.004   # 0.4%

exchange = ccxt.mexc({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "enableRateLimit": True,
})

in_position = False
entry_price = 0
trades_today = 0
losses_today = 0

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

print("DRY RUN SCALPING BOT STARTED", flush=True)

while True:
    try:
        # Reset limits daily not implemented yet (keep simple)
        if trades_today >= 3 or losses_today >= 2:
            print("Daily limit reached. Waiting...", flush=True)
            time.sleep(60)
            continue

        # 1H Trend Filter
        ohlcv_1h = exchange.fetch_ohlcv(SYMBOL, timeframe="1h", limit=200)
        df_1h = pd.DataFrame(ohlcv_1h, columns=["ts","o","h","l","c","v"])
        ema200 = df_1h["c"].ewm(span=200).mean().iloc[-1]
        last_1h = df_1h["c"].iloc[-1]

        if last_1h < ema200:
            print("1H trend bearish. No trade.", flush=True)
            time.sleep(30)
            continue

        # 5M Setup
        ohlcv_5m = exchange.fetch_ohlcv(SYMBOL, timeframe="5m", limit=100)
        df_5m = pd.DataFrame(ohlcv_5m, columns=["ts","o","h","l","c","v"])
        df_5m["rsi"] = rsi(df_5m["c"])
        current_rsi = df_5m["rsi"].iloc[-1]
        current_price = df_5m["c"].iloc[-1]

        if not in_position and current_rsi < 35:
            entry_price = current_price
            in_position = True
            trades_today += 1
            print(f"ENTER (DRY RUN) at {entry_price}", flush=True)

        if in_position:
            tp_price = entry_price * (1 + TP)
            sl_price = entry_price * (1 - SL)

            if current_price >= tp_price:
                print(f"TAKE PROFIT hit at {current_price}", flush=True)
                in_position = False

            elif current_price <= sl_price:
                print(f"STOP LOSS hit at {current_price}", flush=True)
                losses_today += 1
                in_position = False

        time.sleep(20)

    except Exception as e:
        print("Error:", e, flush=True)
        time.sleep(60)
